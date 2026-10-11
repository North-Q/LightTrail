"""U5普通JSON端点；先核验已落盘会话归属，再访问反馈仓库。"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from lighttrail.api.routes import get_request_context
from lighttrail.application.feedback import FeedbackAction
from lighttrail.contracts.context import LOCAL_USER_ID, RequestContext
from lighttrail.contracts.feedback import (
    ConfirmExperience,
    FeedbackMutation,
    FeedbackView,
    SaveExperience,
    SaveFeedback,
)
from lighttrail.infra.feedback import FeedbackError, validate_feedback_id

router = APIRouter(prefix="/api/sessions/{session_id}/results/{result_id}/feedback")


def _source(
    request: Request, context: RequestContext, session_id: str, result_id: str
) -> dict[str, Any] | None:
    """只读解析来源并沿用旧用户归属规则。

    Args:
        request: 服务端请求及依赖。
        context: 当前用户身份。
        session_id: 稳定会话ID。
        result_id: UUID或legacy。
    Returns:
        已落盘目标，目标删除时为None。
    Raises:
        HTTPException: 非法目标、未落盘会话或其他用户均404。
    """
    try:
        for value in (context.user_id, session_id, result_id):
            validate_feedback_id(value)
        if result_id != "legacy":
            UUID(result_id)
    except (FeedbackError, ValueError) as exc:
        raise HTTPException(404, "结果不存在") from exc
    session = request.app.state.deps.sessions.read_saved(session_id)
    if session is None or (session.user_id or LOCAL_USER_ID) != context.user_id:
        raise HTTPException(404, "会话不存在")
    return next(
        (result for result in session.saved_results() if result.get("result_id") == result_id), None
    )


def _mutate(
    request: Request,
    response: Response,
    context: RequestContext,
    session_id: str,
    result_id: str,
    action: FeedbackAction,
    body: FeedbackMutation,
) -> FeedbackView:
    """协议转发，所有失败显式拒绝保存确认。

    Args:
        request: 请求依赖。
        response: 设置不缓存响应。
        context: 已解析请求身份。
        session_id: 会话ID。
        result_id: 结果ID。
        action: 操作类型。
        body: 已校验请求。
    Returns:
        已保存当前状态。
    Raises:
        HTTPException: 冲突、损坏或保存失败。
    """
    response.headers["Cache-Control"] = "no-store"
    _source(request, context, session_id, result_id)
    try:
        return request.app.state.deps.services.feedback.mutate(
            context.user_id,
            session_id,
            result_id,
            action,
            body,
            lambda: _source(request, context, session_id, result_id),
        )
    except FeedbackError as exc:
        raise HTTPException(
            exc.status_code,
            {"saved": False, "message": str(exc)},
            headers={"Cache-Control": "no-store"},
        ) from exc


@router.get("", response_model=FeedbackView)
def read_feedback(
    request: Request,
    response: Response,
    session_id: str,
    result_id: str,
    context: Annotated[RequestContext, Depends(get_request_context)],
) -> FeedbackView:
    """只读恢复当前反馈，不创建任何反馈文件。"""
    source = _source(request, context, session_id, result_id)
    response.headers["Cache-Control"] = "no-store"
    try:
        return request.app.state.deps.services.feedback.read(
            context.user_id,
            session_id,
            result_id,
            source,
        )
    except FeedbackError as exc:
        raise HTTPException(
            exc.status_code,
            {"saved": False, "message": str(exc)},
            headers={"Cache-Control": "no-store"},
        ) from exc


@router.put("", response_model=FeedbackView)
def save_feedback(
    body: SaveFeedback,
    request: Request,
    response: Response,
    session_id: str,
    result_id: str,
    context: Annotated[RequestContext, Depends(get_request_context)],
) -> FeedbackView:
    """保存或修改原始反馈，使既有经验确认失效。"""
    return _mutate(request, response, context, session_id, result_id, "save", body)


@router.post("/withdraw", response_model=FeedbackView)
def withdraw_feedback(
    body: FeedbackMutation,
    request: Request,
    response: Response,
    session_id: str,
    result_id: str,
    context: Annotated[RequestContext, Depends(get_request_context)],
) -> FeedbackView:
    """撤回原始反馈并原子清空关联经验。"""
    return _mutate(request, response, context, session_id, result_id, "withdraw", body)


@router.put("/experience", response_model=FeedbackView)
def save_experience(
    body: SaveExperience,
    request: Request,
    response: Response,
    session_id: str,
    result_id: str,
    context: Annotated[RequestContext, Depends(get_request_context)],
) -> FeedbackView:
    """保存当前反馈版本的本人经验草稿。"""
    return _mutate(request, response, context, session_id, result_id, "experience", body)


@router.post("/experience/confirm", response_model=FeedbackView)
def confirm_experience(
    body: ConfirmExperience,
    request: Request,
    response: Response,
    session_id: str,
    result_id: str,
    context: Annotated[RequestContext, Depends(get_request_context)],
) -> FeedbackView:
    """独立确认当前草稿，不写入Agent记忆。"""
    return _mutate(request, response, context, session_id, result_id, "confirm", body)


@router.post("/experience/withdraw", response_model=FeedbackView)
def withdraw_experience(
    body: FeedbackMutation,
    request: Request,
    response: Response,
    session_id: str,
    result_id: str,
    context: Annotated[RequestContext, Depends(get_request_context)],
) -> FeedbackView:
    """仅撤回经验，保留原始反馈。"""
    return _mutate(request, response, context, session_id, result_id, "withdraw_experience", body)
