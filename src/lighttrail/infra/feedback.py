"""单进程独立反馈JSON仓库：锁内重读、版本裁决与同目录原子替换。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from lighttrail.contracts.feedback import FeedbackRecord

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SOURCE_FIELDS = (
    "session_id",
    "result_id",
    "version",
    "kind",
    "created_at",
    "request",
    "text",
    "card",
    "plan",
    "history",
    "pipeline",
    "trace",
)


class FeedbackError(Exception):
    """反馈错误只携带可公开状态，不包含路径或正文。"""

    def __init__(self, status_code: int, message: str) -> None:
        """初始化协议错误。

        Args:
            status_code: 对应HTTP状态。
            message: 安全中文说明。
        """
        super().__init__(message)
        self.status_code = status_code


def validate_feedback_id(value: str) -> None:
    """验证目录片段。

    Args:
        value: 用户、会话或结果标识。
    Raises:
        FeedbackError: 路径片段不安全。
    """
    if not _SAFE_ID.fullmatch(value):
        raise FeedbackError(404, "结果不存在")


def json_digest(value: Any) -> str:
    """固定UTF-8、排序键与紧凑JSON的v1摘要。

    Args:
        value: 已完成JSON序列化的值。
    Returns:
        带算法前缀的摘要。
    """
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def source_fingerprint(source: dict[str, Any]) -> str:
    """只取固定v1字段，不因未来模型默认值扩大指纹。

    Args:
        source: 已落盘结果的原始字典或legacy映射。
    Returns:
        原结果v1摘要。
    """
    return json_digest({key: source.get(key) for key in _SOURCE_FIELDS})


class FeedbackStore:
    """不建立读取缓存的独立仓库；不创建目录直到真正保存。"""

    def __init__(self, data_dir: str | Path) -> None:
        """建立仓库共享锁，不写盘。

        Args:
            data_dir: 应用数据根目录。
        """
        self._root = Path(data_dir).resolve() / "users"
        self._lock = threading.RLock()

    def read(self, user_id: str, session_id: str, result_id: str) -> FeedbackRecord | None:
        """只读最新记录，不创建文件或触发会话缓存。

        Args:
            user_id: 已核验的请求身份。
            session_id: 已核验归属的会话ID。
            result_id: 结果ID。
        Returns:
            当前记录，缺文件时为None。
        """
        with self._lock:
            return self._read(
                self._path(user_id, session_id, result_id), user_id, session_id, result_id
            )

    def transact(
        self,
        user_id: str,
        session_id: str,
        result_id: str,
        *,
        expected_revision: int,
        mutation_id: UUID,
        request_digest: str,
        change: Callable[[FeedbackRecord | None], FeedbackRecord | None],
    ) -> FeedbackRecord | None:
        """锁内去重、CAS与业务裁决，替换成功后才返回。

        Args:
            user_id: 已核验身份。
            session_id: 会话ID。
            result_id: 结果ID。
            expected_revision: 客户端当前版本。
            mutation_id: 本次本人操作ID。
            request_digest: 包含动作及全部请求字段的摘要。
            change: 对最新文档的纯裁决；None或原对象表示无操作。
        Returns:
            已保存记录或无记录。
        Raises:
            FeedbackError: 冲突、损坏或写入失败。
        """
        with self._lock:
            path = self._path(user_id, session_id, result_id)
            previous = self._read(path, user_id, session_id, result_id)
            if previous is not None and previous.last_mutation.id == mutation_id:
                if previous.last_mutation.request_digest != request_digest:
                    raise FeedbackError(409, "同一操作ID不能用于不同内容")
                return previous
            if expected_revision != (previous.revision if previous else 0):
                raise FeedbackError(409, "记录已改变，请只读刷新后核对，不能覆盖新版本")
            updated = change(previous)
            if updated is None or updated is previous:
                return previous
            # 再校验完整文档，杜绝model_copy跳过字段一致性。
            updated = FeedbackRecord.model_validate(updated.model_dump(mode="json"))
            self._write(path, updated)
            return updated

    def _path(self, user_id: str, session_id: str, result_id: str) -> Path:
        """解析路径并检查目录边界。

        Args:
            user_id: 用户标识。
            session_id: 会话标识。
            result_id: 结果标识。
        Returns:
            指定用户下的绝对路径。
        """
        for value in (user_id, session_id, result_id):
            validate_feedback_id(value)
        base = (self._root / user_id / "result-feedback").resolve()
        if not base.is_relative_to(self._root.resolve()):
            raise FeedbackError(404, "结果不存在")
        path = (base / session_id / f"{result_id}.json").resolve()
        if not path.is_relative_to(base):
            raise FeedbackError(404, "结果不存在")
        return path

    def _read(
        self, path: Path, user_id: str, session_id: str, result_id: str
    ) -> FeedbackRecord | None:
        """校验磁盘记录及归属，损坏不得解释为无反馈。

        Args:
            path: 已核验路径。
            user_id: 预期用户。
            session_id: 预期会话。
            result_id: 预期结果。
        Returns:
            已校验记录或None。
        Raises:
            FeedbackError: 文件不可读、损坏、未知版本或身份不一致。
        """
        try:
            record = FeedbackRecord.model_validate_json(path.read_bytes())
        except FileNotFoundError:
            return None
        except (OSError, ValidationError, ValueError) as exc:
            raise FeedbackError(503, "反馈记录不可读或格式不支持，未覆盖旧记录") from exc
        if (record.user_id, record.target.session_id, record.target.result_id) != (
            user_id,
            session_id,
            result_id,
        ):
            raise FeedbackError(503, "反馈记录归属不一致，未覆盖旧记录")
        return record

    def _write(self, path: Path, record: FeedbackRecord) -> None:
        """写独占临时文件并替换；失败不返回成功。

        Args:
            path: 目标路径。
            record: 完整当前文档。
        Raises:
            FeedbackError: 建目录、写入或替换失败。
        """
        temporary = path.with_name(f".{path.stem}.{uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("x", encoding="utf-8") as stream:
                stream.write(record.model_dump_json(indent=2))
                stream.flush()
                os.fsync(stream.fileno())
            self._replace(temporary, path)
        except OSError as exc:
            raise FeedbackError(500, "保存失败，未确认已保存；原记录保留") from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # 只尝试清理本次临时文件，不覆盖保存结果。

    def _replace(self, temporary: Path, path: Path) -> None:
        """按现有Windows约定有限重试原子替换。

        Args:
            temporary: 本次独占临时文件。
            path: 当前记录路径。
        """
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.01 * (attempt + 1))
