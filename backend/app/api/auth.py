"""邀请码登录与会话（阶段 6B；阶段 6.6 加入邀请码管理）。

线上（``APP_ENV=prod`` 或 ``REQUIRE_AUTH=true``）强制登录；本地开发默认不强制，
这样既不影响既有测试，也方便没有邀请码时本地调试。

强制登录时：

- 除 ``/auth/login`` 与 ``/config`` 外，所有接口都需要 ``Authorization: Bearer <token>``；
- 任务按会话隔离：别的会话的任务一律 403（不是 404，避免"猜 id"式的探测）；
- 管理接口（生成 / 查看 / 停用邀请码）只认 token 里的管理员身份。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request

from backend.app.core.errors import AppError
from backend.app.schemas.task import (
    CreateInviteCodeResponse,
    CreateInviteCodesRequest,
    InviteCodeListResponse,
    InviteCodeSummary,
    InviteDefaults,
    LoginRequest,
    SessionResponse,
)
from backend.app.services.invite_service import InviteCode, InviteService, mask_code, parse_time


router = APIRouter(prefix="/api/v1", tags=["auth"])


def _invite_service(request: Request) -> InviteService:
    return request.app.state.invite_service


def _summary(item: InviteCode, *, now: datetime | None = None) -> InviteCodeSummary:
    """把一个邀请码整理成接口返回的行（来源、状态、剩余次数都算好）。"""
    now = now or datetime.now().astimezone()
    role = "admin" if item.source == "env" else "user"
    expires_at = parse_time(item.expires_at)
    expired = expires_at is not None and expires_at <= now
    remaining = item.remaining
    active = not item.revoked_at and not expired and (remaining is None or remaining > 0)
    return InviteCodeSummary(
        code=item.code,
        masked=mask_code(item.code),
        source=item.source,
        role=role,
        max_uses=item.max_uses,
        uses=item.uses,
        remaining=remaining,
        created_at=item.created_at or None,
        expires_at=expires_at,
        note=item.note,
        revoked_at=parse_time(item.revoked_at),
        active=active,
    )


def _session_response(request: Request, session) -> SessionResponse:
    service = _invite_service(request)
    return SessionResponse(
        token=session.token,
        expires_at=session.expires_at,
        is_admin=session.is_admin,
        remaining_uses=service.remaining_uses(session.code),
        code_masked=mask_code(session.code),
        unlimited=session.is_admin or service.is_unlimited(session.code),
    )


@router.post("/auth/login", response_model=SessionResponse)
def login(request: Request, payload: LoginRequest) -> SessionResponse:
    """用邀请码换取会话 token。管理员码不限次、永久有效。"""
    session = _invite_service(request).login(payload.code)
    return _session_response(request, session)


@router.get("/auth/me", response_model=SessionResponse)
def me(request: Request) -> SessionResponse:
    """校验当前 token；失效时 401（前端据此跳回登录页）。"""
    session = request.state.session
    if session is None:
        raise AppError("AUTH_REQUIRED", "登录已过期，请重新输入邀请码", status_code=401)
    return _session_response(request, session)


@router.post("/admin/invite-codes", response_model=CreateInviteCodeResponse)
def create_invite_codes(
    request: Request, payload: CreateInviteCodesRequest
) -> CreateInviteCodeResponse:
    """生成一个普通邀请码（默认 20 次 / 30 天；0 表示不限次数 / 不过期）。仅管理员可用。"""
    session = request.state.session
    if session is None or not session.is_admin:
        raise AppError("ADMIN_REQUIRED", "这个功能只有管理员可用", status_code=403)
    created = _invite_service(request).create_codes(
        uses=payload.uses,
        days=payload.days,
        note=payload.note,
        created_by=session.code,
    )
    return CreateInviteCodeResponse(code=_summary(created[0]))


@router.get("/admin/invite-codes", response_model=InviteCodeListResponse)
def list_invite_codes(request: Request) -> InviteCodeListResponse:
    """查看所有邀请码（含管理员码首行）与已用次数。仅管理员可用。"""
    session = request.state.session
    if session is None or not session.is_admin:
        raise AppError("ADMIN_REQUIRED", "这个功能只有管理员可用", status_code=403)
    service = _invite_service(request)
    rows: list[InviteCodeSummary] = []
    admin_entry = service.admin_entry()
    if admin_entry is not None:
        rows.append(_summary(admin_entry))
    rows.extend(_summary(item) for item in service.list_codes())
    defaults = service.defaults
    return InviteCodeListResponse(
        codes=rows,
        defaults=InviteDefaults(uses=defaults["uses"], days=defaults["days"]),
    )


@router.post("/admin/invite-codes/{code}/revoke", response_model=CreateInviteCodeResponse)
def revoke_invite_code(request: Request, code: str) -> CreateInviteCodeResponse:
    """停用一个生成的邀请码。环境变量里的码不能停用。仅管理员可用。"""
    session = request.state.session
    if session is None or not session.is_admin:
        raise AppError("ADMIN_REQUIRED", "这个功能只有管理员可用", status_code=403)
    service = _invite_service(request)
    service.revoke_code(code)
    target = next((item for item in service.list_codes() if item.code == code.strip().upper()), None)
    if target is None:
        # 停用后仍应能查到记录；查不到说明并发下被删了（当前不会发生）
        raise AppError("INVITE_CODE_INVALID", "没有找到这个邀请码", status_code=404)
    return CreateInviteCodeResponse(code=_summary(target))
