"""事件记忆（M1.2）：SQLite 按需检索存储，不常驻注入。

设计要点（架构 v2.0 §2.5，E3-2 落地）：
- 只存结构化拍摄事件（时间/地点/坐标/题材/结论/教训/标签/天气天象快照/器材），
  数据本地化、不入库（data/ 已 gitignore）；
- 读写都走参数化 SQL（LIKE 匹配，防注入），检索维度：关键词 / 地点 / 题材；
- 事件字段含精确坐标（coordinates）与天气/天象快照（weather_snapshot）——
  v0.3 增补，是复拍提醒（D2.3-07）「优于你 3 月 18 那场」的数据前提；
- 注入策略：仅当意图涉及地点/题材时检索 top-k，绝不常驻（事件量大且稀疏相关）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 事件行安全截断上限（to_prompt_section 单行字符数）
_EVENT_LINE_CHARS = 140
# 天气/天象快照中优先展示的键（其余字段按出现顺序补充）
_SNAPSHOT_PREFERRED_KEYS = ("云量（%）", "平均云量（%）", "火烧云评分", "月相", "天气")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    location TEXT NOT NULL,
    coordinates TEXT NOT NULL DEFAULT '',
    subject_type TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    lesson TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    weather_snapshot TEXT NOT NULL DEFAULT '{}',
    equipment TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
"""


@dataclass(frozen=True)
class EventRecord:
    """一条拍摄事件记忆（可检索、可注入、可聚合）。

    Attributes:
        id: 数据库自增 ID。
        timestamp: 事件发生时间（ISO 8601）。
        location: 地点名称（如「临港海边」「福州大楼」）。
        coordinates: 精确坐标（如 "31.23,121.47"，楼栋/公寓级；复拍对比用）。
        subject_type: 题材（如 日出 / 日落 / 火烧云 / 银河 / 星空）。
        summary: 事件结论摘要。
        lesson: 经验教训（D4 复盘产出）。
        outcome: 结果（success / fail，空串未知）。
        tags: 标签列表。
        weather_snapshot: 当时的天气/天象快照 dict（云量/火烧云评分/月相…）。
        equipment: 器材参数（后可接 EXIF）。
    """

    id: int
    timestamp: str
    location: str
    coordinates: str = ""
    subject_type: str = ""
    summary: str = ""
    lesson: str = ""
    outcome: str = ""
    tags: tuple[str, ...] = ()
    weather_snapshot: dict[str, Any] = field(default_factory=dict)
    equipment: str = ""


class EventStore:
    """SQLite 事件存储：增、查、聚合（全部参数化查询）。"""

    def __init__(self, db_path: str | Path) -> None:
        """初始化存储（自动建目录与表结构）。

        Args:
            db_path: SQLite 文件路径（如 data/events.db）。
        """
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """轻量迁移：旧库缺 outcome 列时补列（E6-3 成功率统计前提）。"""
        columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
        if "outcome" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN outcome TEXT NOT NULL DEFAULT ''")

    # ------ 对外接口：写入 ------
    def add_event(
        self,
        timestamp: str,
        location: str,
        *,
        subject_type: str = "",
        summary: str = "",
        lesson: str = "",
        outcome: str = "",
        tags: list[str] | tuple[str, ...] | None = None,
        coordinates: str = "",
        weather_snapshot: dict[str, Any] | None = None,
        equipment: str = "",
    ) -> int:
        """追加一条拍摄事件。

        Args:
            timestamp: 事件时间（ISO 8601）。
            location: 地点名称。
            subject_type: 题材。
            summary: 结论摘要。
            lesson: 经验教训。
            outcome: 结果（success / fail，空串未知；E6-3 成功率统计用）。
            tags: 标签列表。
            coordinates: 精确坐标。
            weather_snapshot: 天气/天象快照 dict（D2.3-07 复拍对比基线）。
            equipment: 器材参数。

        Returns:
            新事件的自增 ID。
        """
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO events"
                " (timestamp, location, subject_type, summary, lesson, outcome, tags,"
                "  coordinates, weather_snapshot, equipment)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    timestamp,
                    location,
                    subject_type,
                    summary,
                    lesson,
                    outcome,
                    json.dumps(list(tags or []), ensure_ascii=False),
                    coordinates,
                    json.dumps(weather_snapshot or {}, ensure_ascii=False),
                    equipment,
                ),
            )
            return int(cursor.lastrowid)

    # ------ 对外接口：检索 ------
    def search_events(
        self,
        query: str = "",
        *,
        location: str | None = None,
        subject_type: str | None = None,
        limit: int = 5,
    ) -> list[EventRecord]:
        """按关键词/地点/题材检索事件（参数化 LIKE，时间倒序）。

        Args:
            query: 关键词，匹配地点/摘要/标签（模糊）。
            location: 地点关键字（模糊）。
            subject_type: 题材（精确）。
            limit: 返回条数上限。

        Returns:
            命中事件列表（按 timestamp 倒序）；无命中返回空列表。
        """
        conditions: list[str] = []
        params: list[Any] = []
        if query:
            like = f"%{query}%"
            conditions.append("(location LIKE ? OR summary LIKE ? OR tags LIKE ?)")
            params.extend([like, like, like])
        if location:
            conditions.append("location LIKE ?")
            params.append(f"%{location}%")
        if subject_type:
            conditions.append("subject_type = ?")
            params.append(subject_type)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(int(limit))
        sql = f"SELECT * FROM events{where} ORDER BY timestamp DESC LIMIT ?"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._to_record(row) for row in rows]

    def occurrence_counts(
        self,
        *,
        location: str | None = None,
        subject_type: str | None = None,
    ) -> dict[str, int]:
        """按地点聚合命中事件的次数（复拍提醒/次数统计的数据基础）。

        Args:
            location: 可选地点过滤（模糊）。
            subject_type: 可选题材过滤（精确）。

        Returns:
            {地点: 次数}，按次数降序。
        """
        conditions: list[str] = []
        params: list[Any] = []
        if location:
            conditions.append("location LIKE ?")
            params.append(f"%{location}%")
        if subject_type:
            conditions.append("subject_type = ?")
            params.append(subject_type)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"SELECT location AS k, COUNT(*) AS c FROM events{where} GROUP BY k ORDER BY c DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return {str(row["k"]): int(row["c"]) for row in rows}

    def count_all(self) -> int:
        """事件总数（测试与诊断）。"""
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()
        return int(row["c"])

    # ------ 内部实现 ------
    def _connect(self) -> sqlite3.Connection:
        """新建连接（行工厂为 Row，便于按列名读取）。"""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _to_record(row: sqlite3.Row) -> EventRecord:
        """把数据库行还原为 EventRecord（JSON 字段反序列化）。"""
        return EventRecord(
            id=int(row["id"]),
            timestamp=str(row["timestamp"]),
            location=str(row["location"]),
            coordinates=str(row["coordinates"]),
            subject_type=str(row["subject_type"]),
            summary=str(row["summary"]),
            lesson=str(row["lesson"]),
            outcome=str(row["outcome"]),
            tags=tuple(json.loads(row["tags"] or "[]")),
            weather_snapshot=json.loads(row["weather_snapshot"] or "{}"),
            equipment=str(row["equipment"]),
        )


def to_prompt_section(events: list[EventRecord], limit: int = 6) -> str:
    """把事件列表压缩为注入文本（每条一行，含快照关键字段）。

    Args:
        events: 待注入的事件（通常为检索 top-k）。
        limit: 最多注入条数。

    Returns:
        多行文本「· [日期] 地点｜题材｜摘要｜快照…」；空列表返回空串。
    """
    lines: list[str] = []
    for event in events[:limit]:
        parts = [f"[{event.timestamp[:10]}] {event.location or '未知地点'}"]
        if event.subject_type:
            parts.append(event.subject_type)
        if event.summary:
            parts.append(event.summary)
        snapshot_summary = _compact_snapshot(event.weather_snapshot)
        if snapshot_summary:
            parts.append(snapshot_summary)
        if event.coordinates:
            parts.append(f"坐标 {event.coordinates}")
        if event.equipment:
            parts.append(event.equipment)
        line = "｜".join(parts)
        if len(line) > _EVENT_LINE_CHARS:
            line = line[: _EVENT_LINE_CHARS - 1] + "…"
        lines.append(f"· {line}")
    return "\n".join(lines)


def _compact_snapshot(snapshot: dict[str, Any]) -> str:
    """把天气/天象快照压缩为「键: 值；…」短串（最多 4 项，优先常用键）。"""
    if not snapshot:
        return ""
    ordered = [key for key in _SNAPSHOT_PREFERRED_KEYS if key in snapshot]
    ordered += [key for key in snapshot if key not in ordered]
    pieces = [f"{key}:{snapshot[key]}" for key in ordered[:4]]
    return "；".join(pieces)
