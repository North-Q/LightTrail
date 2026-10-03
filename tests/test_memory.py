"""用户档案记忆（E3-1）的 pytest 用例。

验证：
- 无 profile.json 时返回空档案、注入块为空（行为退化为现状）；
- load / save / update 读写闭环（数据目录本地 JSON）；
- to_prompt_section ≤300 字、空档案返回空串；
- MemoryManager.build_injections 组装档案块；
- Agent 集成：注入 memory 后 system 第④层出现档案；未注入时第四层省略。
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from lighttrail.adapters.llm.provider import ChatClientProvider
from lighttrail.adapters.llm.pydantic_bridge import LightTrailModel
from lighttrail.composition import build_context, build_registry
from lighttrail.contracts.context import RequestContext
from lighttrail.memory import MemoryManager, SemanticStore, UserProfile
from lighttrail.memory.migration import migrate_memory_namespace
from lighttrail.runtime.agent import AgentRuntime


class _FakeChatClient:
    """记录调用参数的伪客户端（无工具调用，直接返回最终回复）。"""

    def __init__(self) -> None:
        self.calls: list = []

    def chat(
        self, messages, *, model=None, tools=None, temperature=0.2, usage_callback=None
    ) -> dict:
        self.calls.append({"messages": messages, "model": model, "tools": tools})
        return {"role": "assistant", "content": "好的。"}

    async def acall(self, messages, **kwargs) -> dict:
        """async 通道：B2-7 起 runtime 经 Model 桥走 acall。"""
        allowed = {k: v for k, v in kwargs.items() if k in {"model", "tools", "temperature"}}
        return self.chat(messages, **allowed)


@pytest.fixture()
def memory_dir() -> Path:
    """自建临时数据目录（pytest tmp_path 受沙箱 ACL 限制时仍可用）。"""
    directory = Path(tempfile.mkdtemp(prefix="lt_memory_"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


def _example_profile() -> dict:
    return {
        "camera_body": "松下 S5M2（全画幅）",
        "lenses": ["24-105mm F4", "契卡 14mm 定焦"],
        "preferences": ["风光", "星空"],
        "common_locations": ["临港海边", "外滩"],
        "skill_level": "进阶爱好者",
    }


def _write_profile(data_dir, fields: dict) -> None:
    Path(data_dir, "profile.json").write_text(
        json.dumps(fields, ensure_ascii=False), encoding="utf-8"
    )


def test_load_absent_returns_empty_profile(memory_dir) -> None:
    """无 profile.json：空档案、注入块为空。"""
    manager = MemoryManager(memory_dir)
    assert manager.profile == UserProfile()
    assert manager.build_injections() == []


def test_load_from_file(memory_dir) -> None:
    """从 profile.json 加载字段并生成注入段。"""
    _write_profile(memory_dir, _example_profile())
    manager = MemoryManager(memory_dir)
    section = manager.build_injections()[0]
    assert section.name == "profile"
    assert "相机：松下 S5M2（全画幅）" in section.text
    assert "偏好题材：风光、星空" in section.text
    assert "常去机位：临港海边、外滩" in section.text


def test_load_ignores_invalid_json(memory_dir) -> None:
    """非法 JSON 不抛错，回退空档案。"""
    Path(memory_dir, "profile.json").write_text("{oops", encoding="utf-8")
    manager = MemoryManager(memory_dir)
    assert manager.profile.to_prompt_section() == ""


def test_update_and_save_persists(memory_dir) -> None:
    """update_profile 落盘后可重新加载。"""
    manager = MemoryManager(memory_dir)
    manager.update_profile({"camera_body": "索尼 A7M4", "preferences": ["街头"]})
    saved = json.loads(
        Path(memory_dir, "users", "_local", "memory", "profile.json").read_text(encoding="utf-8")
    )
    assert saved["camera_body"] == "索尼 A7M4"
    assert saved["preferences"] == ["街头"]

    reloaded = MemoryManager(memory_dir)
    assert reloaded.profile.camera_body == "索尼 A7M4"


def test_update_ignores_unknown_fields(memory_dir) -> None:
    """未知字段被忽略，不写入。"""
    manager = MemoryManager(memory_dir)
    manager.update_profile({"api_key": "secret", "camera_body": "理光 GR3"})
    saved = json.loads(
        Path(memory_dir, "users", "_local", "memory", "profile.json").read_text(encoding="utf-8")
    )
    assert "api_key" not in saved
    assert saved["camera_body"] == "理光 GR3"


def test_to_prompt_section_truncated_to_300_chars(memory_dir) -> None:
    """超长档案注入段被截断到 ≤300 字并标注。"""
    manager = MemoryManager(memory_dir)
    manager.update_profile({"preferences": ["风光"] * 200})  # 400 字
    text = manager.profile.to_prompt_section()
    assert len(text) <= 300
    assert "…" in text


def test_agent_injects_profile_into_layer_four(memory_dir) -> None:
    """注入 MemoryManager 后：system 第④层含档案文本。"""
    _write_profile(memory_dir, _example_profile())
    fake = _FakeChatClient()
    registry = build_registry()
    runtime = AgentRuntime(
        LightTrailModel(ChatClientProvider(fake), model_name="ecnu-plus"),
        registry,
        context=build_context(registry, memory=MemoryManager(memory_dir)),
    )
    runtime.run("你好")
    system = fake.calls[0]["messages"][0]["content"]
    assert "## 用户档案与语义记忆" in system
    assert "松下 S5M2" in system


# ------ 语义记忆（E3-3）------
def test_semantic_store_load_and_match(memory_dir) -> None:
    """semantic.json 存在时按关键词命中注入，无关意图不命中。"""
    Path(memory_dir, "semantic.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "content": "偏好低云量+高云为主的晚霞，成功率约 7 成",
                        "keywords": ["火烧云", "晚霞"],
                    },
                    {"content": "新月前后拍银河出片率最高", "keywords": ["银河", "星空"]},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = SemanticStore(memory_dir / "semantic.json")
    assert store.match("今晚火烧云概率如何") == ["偏好低云量+高云为主的晚霞，成功率约 7 成"]
    assert store.match("随便聊聊") == []


def test_semantic_store_missing_file_returns_empty(memory_dir) -> None:
    """无 semantic.json：命中为空，不抛错。"""
    store = SemanticStore(memory_dir / "semantic.json")
    assert store.match("火烧云") == []


def test_semantic_double_confirm_write_flow(memory_dir) -> None:
    """候选未确认不命中；确认后命中并落盘；重复确认返回 False。"""
    path = memory_dir / "semantic.json"
    store = SemanticStore(path)
    candidate = store.staged_add("低云量火烧云成功率高", ["火烧云"])
    # 未确认 → 不命中、不落盘
    assert store.match("火烧云") == []
    assert not path.exists()
    # 确认 → 命中 + 落盘
    assert store.confirm(candidate) is True
    assert store.match("火烧云") == ["低云量火烧云成功率高"]
    assert path.exists()
    # 重复确认同一候选 → False
    assert store.confirm(candidate) is False


def test_semantic_reject_discards_candidate(memory_dir) -> None:
    """否决候选后不参与命中，也不落盘。"""
    path = memory_dir / "semantic.json"
    store = SemanticStore(path)
    candidate = store.staged_add("污染性结论", ["火烧云"])
    assert store.reject(candidate) is True
    assert store.match("火烧云") == []
    assert not path.exists()


def test_semantic_match_limited_to_two(memory_dir) -> None:
    """命中超过 2 条时只注入前 2 条。"""
    store = SemanticStore(path=None)
    for i, kw in enumerate(["火烧云", "晚霞", "霞"]):
        store.staged_add(f"经验{i}", [kw])
        store.confirm(i)
    hits = store.match("火烧云晚霞霞")
    assert len(hits) == 2
    assert hits == ["经验0", "经验1"]


def test_memory_manager_injections_include_semantic(memory_dir) -> None:
    """注入顺序：profile → semantic → events；语义未命中时只含 profile/events。"""
    manager = MemoryManager(memory_dir, semantic_store=SemanticStore(path=None))
    manager.update_profile({"preferences": ["风光"]})
    manager.add_event(
        "2026-07-02T19:05:00+08:00",
        "临港海边",
        subject_type="火烧云",
        summary="晚霞中等偏上",
    )
    manager.semantic_propose("低云量火烧云成功率高", ["火烧云"])
    manager.semantic_confirm(0)

    blocks = manager.build_injections("临港海边拍火烧云")
    assert [b.name for b in blocks] == ["profile", "semantic", "events"]
    assert "低云量火烧云成功率高" in blocks[1].text

    uncovered = manager.build_injections("晚上吃饭去哪？")
    assert [b.name for b in uncovered] == ["profile"]  # 无关意图：无语义、无事件


def test_agent_without_memory_omits_layer_four(memory_dir) -> None:
    """未注入 MemoryManager：第④层省略（行为与 E1-2 一致）。"""
    fake = _FakeChatClient()
    runtime = AgentRuntime(
        LightTrailModel(ChatClientProvider(fake), model_name="ecnu-plus"), build_registry()
    )
    runtime.run("你好")
    system = fake.calls[0]["messages"][0]["content"]
    assert "## 用户档案与语义记忆" not in system


# ------ 模板/命名空间共识（B4 期发现 F10，为 B5 迁移铺路）------
_REPO_ROOT = Path(__file__).resolve().parents[1]
_EXAMPLE_PROFILE = _REPO_ROOT / "data" / "profile.example.json"


def test_profile_example_covers_all_profile_fields() -> None:
    """示例模板字段必须覆盖 UserProfile 全部字段（此前漏 `favorite_spots`，复制即缺位）。"""
    example = json.loads(_EXAMPLE_PROFILE.read_text(encoding="utf-8"))
    missing = [field.name for field in dataclasses.fields(UserProfile) if field.name not in example]
    assert missing == [], f"data/profile.example.json 缺字段：{missing}"


def test_profile_example_spot_shape_is_machine_readable() -> None:
    """示例档案不预装来源与坐标系未核实的精确机位。"""
    example = json.loads(_EXAMPLE_PROFILE.read_text(encoding="utf-8"))
    assert example["favorite_spots"] == []


def test_user_id_namespace_is_isolated(memory_dir: Path) -> None:
    """不同 user_id 的事件和档案必须落在互不重叠的命名空间。"""
    manager = MemoryManager(memory_dir)
    local = RequestContext()
    other = RequestContext(user_id="other")
    manager.update_profile(local, {"camera_body": "松下 S5M2"})
    manager.update_profile(other, {"camera_body": "索尼 A7C2"})
    manager.add_event(local, "2026-09-15T19:00:00+08:00", "临港")
    manager.add_event(other, "2026-09-15T19:00:00+08:00", "外滩")
    assert manager.profile_for(local).camera_body == "松下 S5M2"
    assert manager.profile_for(other).camera_body == "索尼 A7C2"
    assert (memory_dir / "users" / "_local" / "memory" / "events.db").exists()
    assert (memory_dir / "users" / "other" / "memory" / "events.db").exists()


def test_concurrent_state_initialization_shares_one_store_set(
    memory_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一用户并发首次访问只构造并缓存一套存储对象。"""
    from lighttrail.memory import manager as manager_module

    event_instances: list[object] = []
    semantic_instances: list[object] = []
    original_event_store = manager_module.EventStore
    original_semantic_store = manager_module.SemanticStore

    def make_event_store(path: str | Path) -> object:
        instance = original_event_store(path)
        event_instances.append(instance)
        return instance

    def make_semantic_store(path: str | Path | None = None) -> object:
        instance = original_semantic_store(path)
        semantic_instances.append(instance)
        return instance

    monkeypatch.setattr(manager_module, "EventStore", make_event_store)
    monkeypatch.setattr(manager_module, "SemanticStore", make_semantic_store)
    manager = MemoryManager(memory_dir)
    context = RequestContext(user_id="concurrent-user")

    with ThreadPoolExecutor(max_workers=12) as executor:
        states = list(executor.map(lambda _: manager._state(context), range(48)))

    assert all(state is states[0] for state in states)
    assert len(event_instances) == 1
    assert len(semantic_instances) == 1


def test_memory_migration_is_idempotent(memory_dir: Path) -> None:
    """旧根目录文件只迁一次，重复执行不覆盖已迁移的数据。"""
    (memory_dir / "profile.json").write_text('{"camera_body": "松下 S5M2"}', encoding="utf-8")
    first = migrate_memory_namespace(memory_dir)
    second = migrate_memory_namespace(memory_dir)
    target = memory_dir / "users" / "_local" / "memory" / "profile.json"
    assert first.moved == (target,)
    assert second.moved == ()
    assert json.loads(target.read_text(encoding="utf-8"))["camera_body"] == "松下 S5M2"


def test_memory_migration_rejects_path_like_user_id(memory_dir: Path) -> None:
    """迁移脚本不允许 user_id 逃逸出 users 目录。"""
    with pytest.raises(ValueError, match="user_id"):
        migrate_memory_namespace(memory_dir, "../other")
