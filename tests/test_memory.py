"""用户档案记忆（E3-1）的 pytest 用例。

验证：
- 无 profile.json 时返回空档案、注入块为空（行为退化为现状）；
- load / save / update 读写闭环（数据目录本地 JSON）；
- to_prompt_section ≤300 字、空档案返回空串；
- MemoryManager.build_injections 组装档案块；
- Agent 集成：注入 memory 后 system 第④层出现档案；未注入时第四层省略。
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from lighttrail.agent import Agent
from lighttrail.agent.tools import registry
from lighttrail.memory import MemoryManager, UserProfile


class _FakeChatClient:
    """记录调用参数的伪客户端（无工具调用，直接返回最终回复）。"""

    def __init__(self) -> None:
        self.calls: list = []

    def chat(self, messages, *, model=None, tools=None, temperature=0.2) -> dict:
        self.calls.append({"messages": messages, "model": model, "tools": tools})
        return {"role": "assistant", "content": "好的。"}


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
    Path(data_dir, "profile.json").write_text(json.dumps(fields, ensure_ascii=False), encoding="utf-8")


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
    saved = json.loads(Path(memory_dir, "profile.json").read_text(encoding="utf-8"))
    assert saved["camera_body"] == "索尼 A7M4"
    assert saved["preferences"] == ["街头"]

    reloaded = MemoryManager(memory_dir)
    assert reloaded.profile.camera_body == "索尼 A7M4"


def test_update_ignores_unknown_fields(memory_dir) -> None:
    """未知字段被忽略，不写入。"""
    manager = MemoryManager(memory_dir)
    manager.update_profile({"api_key": "secret", "camera_body": "理光 GR3"})
    saved = json.loads(Path(memory_dir, "profile.json").read_text(encoding="utf-8"))
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
    agent = Agent(fake, registry, model="ecnu-plus", memory=MemoryManager(memory_dir))
    agent.run("你好")
    system = fake.calls[0]["messages"][0]["content"]
    assert "## 用户档案与语义记忆" in system
    assert "松下 S5M2" in system


def test_agent_without_memory_omits_layer_four(memory_dir) -> None:
    """未注入 MemoryManager：第④层省略（行为与 E1-2 一致）。"""
    fake = _FakeChatClient()
    agent = Agent(fake, registry, model="ecnu-plus")
    agent.run("你好")
    system = fake.calls[0]["messages"][0]["content"]
    assert "## 用户档案与语义记忆" not in system
