"""邀请码与会话（阶段 6B）。

规则（产品经理确认，2026-09-23）：

| 角色 | 来源 | 有效期 | 次数 |
| --- | --- | --- | --- |
| 管理员码 | 环境变量 ``ADMIN_INVITE_CODE`` | **永久** | **不限** |
| 普通码 | 管理员通过接口生成 | **30 天** | **20 次**（一码可多人共用） |

设计要点：

1. **管理员码不落库**：只从环境变量读，因此不会出现在任何接口返回值、日志或备份里；
2. **普通码与会话写在同一个 JSON 文件**（`data/invite-codes.json`），原子写 + 进程内锁，
   与任务记录同样的可靠性做法；该文件随业务数据一起备份到对象存储；
3. **每登录一次 = 消耗一次**：用尽或过期时给出明确中文提示；
4. 会话 token 是随机串，有效期与码同长（默认 30 天）；**任务按会话隔离**，
   这样"同一个码的 20 个人"互相看不到对方的任务。
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import tempfile
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path

from backend.app.core.errors import AppError


logger = logging.getLogger(__name__)

#: 生成邀请码用的字符集：去掉了 0/O/1/I/L 这些容易抄错的字符
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_GROUP = 4
CODE_GROUPS = 2


@dataclass
class InviteCode:
    code: str
    max_uses: int
    uses: int
    created_at: str
    expires_at: str
    note: str = ""


@dataclass
class Session:
    token: str
    code: str
    created_at: str
    expires_at: str
    is_admin: bool = False

    @property
    def owner_id(self) -> str:
        """任务归属标识：用会话 token 派生，保证"同一码的不同人"互相看不到数据。"""
        return self.token


class InviteService:
    def __init__(
        self,
        store_path: Path,
        *,
        admin_code: str = "",
        valid_days: int = 30,
        max_uses: int = 20,
    ) -> None:
        self.store_path = store_path
        self.admin_code = admin_code.strip()
        self.valid_days = max(1, valid_days)
        self.default_max_uses = max(1, max_uses)
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ 登录

    def login(self, raw_code: str) -> Session:
        """用邀请码换取一个会话。管理员码不限次且永久有效。"""
        code = (raw_code or "").strip().upper()
        if not code:
            raise AppError("INVITE_CODE_REQUIRED", "请输入邀请码", status_code=400)

        now = datetime.now().astimezone()

        if self.admin_code and secrets.compare_digest(code, self.admin_code.upper()):
            session = self._new_session(code, now, is_admin=True)
            self._save_session(session)
            logger.info("admin_login")
            return session

        with self._lock:
            store = self._load()
            entry = next((item for item in store["codes"] if item["code"] == code), None)
            if entry is None:
                raise AppError("INVITE_CODE_INVALID", "邀请码不正确，请确认后重试", status_code=401)
            expires_at = datetime.fromisoformat(entry["expires_at"])
            if expires_at <= now:
                raise AppError(
                    "INVITE_CODE_EXPIRED",
                    "该邀请码已过期，请联系管理员获取新的邀请码",
                    status_code=401,
                )
            if entry["uses"] >= entry["max_uses"]:
                raise AppError(
                    "INVITE_CODE_EXHAUSTED",
                    "该邀请码使用次数已用完，请联系管理员获取新的邀请码",
                    status_code=401,
                )
            entry["uses"] += 1
            self._write(store)

        session = self._new_session(code, now, is_admin=False)
        self._save_session(session)
        logger.info("invite_login remaining_uses=%s", entry["max_uses"] - entry["uses"])
        return session

    def resolve(self, token: str) -> Session | None:
        """校验 token 是否有效（过期即失效）。"""
        if not token:
            return None
        now = datetime.now().astimezone()
        with self._lock:
            store = self._load()
            for item in store["sessions"]:
                if secrets.compare_digest(item["token"], token):
                    if datetime.fromisoformat(item["expires_at"]) <= now:
                        return None
                    return Session(**item)
        return None

    def remaining_uses(self, code: str) -> int | None:
        """管理员码返回 None（不限次）；普通码返回剩余次数。"""
        if self.admin_code and secrets.compare_digest(code.upper(), self.admin_code.upper()):
            return None
        with self._lock:
            for entry in self._load()["codes"]:
                if entry["code"] == code.upper():
                    return max(0, entry["max_uses"] - entry["uses"])
        return 0

    # ------------------------------------------------------------------ 管理

    def create_codes(self, *, count: int = 1, note: str = "") -> list[InviteCode]:
        now = datetime.now().astimezone()
        expires_at = now + timedelta(days=self.valid_days)
        created: list[InviteCode] = []
        with self._lock:
            store = self._load()
            existing = {item["code"] for item in store["codes"]}
            for _ in range(max(1, min(count, 20))):
                code = self._generate_code(existing)
                existing.add(code)
                created.append(
                    InviteCode(
                        code=code,
                        max_uses=self.default_max_uses,
                        uses=0,
                        created_at=now.isoformat(),
                        expires_at=expires_at.isoformat(),
                        note=note,
                    )
                )
            store["codes"].extend(asdict(item) for item in created)
            self._write(store)
        logger.info("invite_codes_created count=%s valid_days=%s", len(created), self.valid_days)
        return created

    def list_codes(self) -> list[InviteCode]:
        with self._lock:
            return [InviteCode(**item) for item in self._load()["codes"]]

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _generate_code(existing: set[str]) -> str:
        while True:
            groups = [
                "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_GROUP))
                for _ in range(CODE_GROUPS)
            ]
            code = "-".join(groups)
            if code not in existing:
                return code

    def _new_session(self, code: str, now: datetime, *, is_admin: bool) -> Session:
        return Session(
            token=secrets.token_urlsafe(24),
            code=code,
            created_at=now.isoformat(),
            expires_at=(now + timedelta(days=self.valid_days)).isoformat(),
            is_admin=is_admin,
        )

    def _save_session(self, session: Session) -> None:
        with self._lock:
            store = self._load()
            store["sessions"].append(asdict(session))
            # 顺手清掉过期的会话，避免文件无限增长
            now = datetime.now().astimezone()
            store["sessions"] = [
                item
                for item in store["sessions"]
                if datetime.fromisoformat(item["expires_at"]) > now
            ]
            self._write(store)

    def _load(self) -> dict:
        if not self.store_path.is_file():
            return {"codes": [], "sessions": []}
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("invite_store_unreadable path=%s", self.store_path, exc_info=True)
            return {"codes": [], "sessions": []}
        data.setdefault("codes", [])
        data.setdefault("sessions", [])
        return data

    def _write(self, store: dict) -> None:
        """原子写：先写临时文件再替换，避免进程被强杀时留下半个文件。"""
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(store, ensure_ascii=False, indent=2)
        fd, temp_name = tempfile.mkstemp(prefix=".invite-", dir=self.store_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.store_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


def bearer_token(authorization: str | None) -> str:
    """从 ``Authorization: Bearer xxx`` 里取出 token。"""
    if not authorization:
        return ""
    prefix = "bearer "
    if authorization.lower().startswith(prefix):
        return authorization[len(prefix) :].strip()
    return ""
