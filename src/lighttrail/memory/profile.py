"""用户档案记忆（M1.1）：data/profile.json 常驻精简注入。

设计要点（架构 v2.0 §2.5，E3-1 落地）：
- 档案决定所有建议的个性化基线，每轮都在 system prompt 第④层（常驻，≤300 字）；
- 写入路径：用户显式声明或确认后 update+save（绝不自动推断写入）；
- 数据本地化：JSON 起步，仅存纯文本结构，不存 API Key 等敏感信息；
- 无 profile.json 时 load 返回空档案，注入段为空（行为退化为现状）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_PROFILE_FILE = "profile.json"

# 常驻注入段的字符上限（≤300 字 ≈ 300 token 的宽松落点）
_MAX_SECTION_CHARS = 300

# 档案字段（与 data/profile.example.json 模板保持一致）
_FIELDS = ("camera_body", "lenses", "preferences", "common_locations", "skill_level")


def _truncate(text: str, limit: int) -> str:
    """截断到 limit 字符，超限补省略号。"""
    if len(text) <= limit:
        return text
    cut = max(0, limit - 1)
    return text[:cut] + "…"


@dataclass
class UserProfile:
    """用户摄影档案（可序列化字段与模板一致）。"""

    camera_body: str = ""
    lenses: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
    common_locations: list[str] = field(default_factory=list)
    skill_level: str = ""

    # ------ 对外接口 ------
    @classmethod
    def load(cls, data_dir: str | Path) -> UserProfile:
        """从 data_dir/profile.json 加载档案；文件缺失时返回空档案。

        Args:
            data_dir: 数据目录（如 settings.data_dir）。

        Returns:
            档案实例；缺失/非法 JSON 时为空档案（不抛错，保证系统可用）。
        """
        path = Path(data_dir) / _PROFILE_FILE
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        return cls(**{key: raw[key] for key in _FIELDS if key in raw})

    def save(self, data_dir: str | Path) -> Path:
        """把档案写入 data_dir/profile.json（自动建目录）。

        Args:
            data_dir: 数据目录。

        Returns:
            写入的文件路径。
        """
        path = Path(data_dir) / _PROFILE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: getattr(self, key) for key in _FIELDS}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def update(self, fields: dict[str, Any]) -> UserProfile:
        """部分更新档案字段（忽略未知字段）。

        Args:
            fields: 待更新字段（键须在 _FIELDS 内，列表字段整体替换）。

        Returns:
            self（便于链式调用）。
        """
        for key, value in fields.items():
            if key in _FIELDS:
                setattr(self, key, value)
        return self

    def to_prompt_section(self) -> str:
        """压缩为常驻注入段落（≤300 字）；空档案返回空串。

        Returns:
            形如「相机：松下 S5M2；镜头：24-105mm F4；偏好题材：风光/星空…」。
        """
        parts: list[str] = []
        if self.camera_body:
            parts.append(f"相机：{self.camera_body}")
        if self.lenses:
            parts.append("镜头：" + "、".join(self.lenses))
        if self.preferences:
            parts.append("偏好题材：" + "、".join(self.preferences))
        if self.common_locations:
            parts.append("常去机位：" + "、".join(self.common_locations))
        if self.skill_level:
            parts.append(f"水平：{self.skill_level}")
        return _truncate("；".join(parts), _MAX_SECTION_CHARS)
