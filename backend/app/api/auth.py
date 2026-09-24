"""邀请码登录与会话（阶段 6B）。

线上（``APP_ENV=prod`` 或 ``REQUIRE_AUTH=true``）强制登录；本地开发默认不强制，
这样既不影响既有测试，也方便没有邀请码时本地调试。

强制登录时：

- 除 ``/auth/login`` 与 ``/config`` 外，所有接口都需要 ``Authorization: Bearer <token>``；
- 任务按会话隔离：别的会话的任务一律 403（不是 404，避免"猜 id"式的探测）。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from backend.app.core.errors import AppError
from backend.app.schemas.task import (
    CreateInviteCodesRequest,
    CreateInviteCodesResponse,
    InviteCodeSummary,
    LoginRequest,
    SessionResponse,
)


router = APIRouter(prefix="/api/v1", tags=["auth"])


def _invite_service(request: Request):
    return request.app.state.invite_service


@router.post("/auth/login", response_model=SessionResponse)
def login(request: Request, payload: LoginRequest) -> SessionResponse:
    """用邀请码换取会话 token。管理员码不限次、永久有效。"""
    service = _invite_service(request)
    session = service.login(payload.code)
    return SessionResponse(
        token=session.token,
        expires_at=session.expires_at,
        is_admin=session.is_admin,
        remaining_uses=service.remaining_uses(session.code) if not session.is_admin else None,
    )


@router.get("/auth/me", response_model=SessionResponse)
def me(request: Request) -> SessionResponse:
    """校验当前 token；失效时 401（前端据此跳回登录页）。"""
    session = request.state.session
    if session is None:
        raise AppError("AUTH_REQUIRED", "登录已过期，请重新输入邀请码", status_code=401)
    return SessionResponse(
        token=session.token,
        expires_at=session.expires_at,
        is_admin=session.is_admin,
        remaining_uses=None if session.is_admin else _invite_service(request).remaining_uses(session.code),
    )


@router.post("/admin/invite-codes", response_model=CreateInviteCodesResponse)
def create_invite_codes(
    request: Request, payload: CreateInviteCodesRequest
) -> CreateInviteCodesResponse:
    """生成普通邀请码（默认 30 天 / 20 次）。仅管理员可用。"""
    session = request.state.session
    if session is None or not session.is_admin:
        raise AppError("ADMIN_REQUIRED", "只有管理员可以生成邀请码", status_code=403)
    created = _invite_service(request).create_codes(count=payload.count, note=payload.note)
    return CreateInviteCodesResponse(
        codes=[
            InviteCodeSummary(
                code=item.code,
                max_uses=item.max_uses,
                uses=item.uses,
                created_at=item.created_at,
                expires_at=item.expires_at,
                note=item.note,
            )
            for item in created
        ]
    )


@router.get("/admin/invite-codes", response_model=CreateInviteCodesResponse)
def list_invite_codes(request: Request) -> CreateInviteCodesResponse:
    """查看所有普通邀请码与已用次数。仅管理员可用。"""
    session = request.state.session
    if session is None or not session.is_admin:
        raise AppError("ADMIN_REQUIRED", "只有管理员可以查看邀请码", status_code=403)
    items = _invite_service(request).list_codes()
    return CreateInviteCodesResponse(
        codes=[
            InviteCodeSummary(
                code=item.code,
                max_uses=item.max_uses,
                uses=item.uses,
                created_at=item.created_at,
                expires_at=item.expires_at,
                note=item.note,
            )
            for item in items
        ]
    )
