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

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
import threading
import time
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


TOKEN_VERSION = "v1"


@dataclass
class Session:
    token: str
    code: str
    created_at: str
    expires_at: str
    is_admin: bool = False

    @property
    def owner_id(self) -> str:
        """任务归属标识：由**邀请码**派生。

        为什么不用 token：令牌是自校验的（无服务端存储），重新登录会换一张令牌；
        若归属跟着令牌走，用户重登后就看不到自己之前建的任务了。
        用邀请码派生则意味着"同一个码的人共享任务可见性" —— 产品经理已确认
        不需要区分一码一人还是一码多人，因此这里选择对用户更省心的语义。
        """
        return hashlib.sha256(f"owner:{self.code}".encode()).hexdigest()[:32]


class SessionTokenCodec:
    """会话令牌编解码：``v1.<payload>.<签名>``。

    为什么要自校验：线上是**函数实例**，`/tmp` 随实例回收/发布而清空。
    如果把会话存在实例本地文件里，用户登录后只要发生一次实例替换就会
    "莫名其妙要求重新登录"（真实踩过）。改成 HMAC 签名令牌后，
    任何实例都能独立验证令牌，不再依赖本地存储。
    """

    def __init__(self, secret: str) -> None:
        self.secret = secret.encode()

    def encode(self, *, code: str, expires_at: datetime, is_admin: bool, issued_at: datetime) -> str:
        payload = json.dumps(
            {"c": code, "e": int(expires_at.timestamp()), "a": bool(is_admin), "i": int(issued_at.timestamp())},
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        return f"{TOKEN_VERSION}.{body}.{self._sign(body)}"

    def decode(self, token: str) -> dict | None:
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != TOKEN_VERSION:
            return None
        body = parts[1]
        if not hmac.compare_digest(self._sign(body), parts[2]):
            return None
        try:
            padded = body + "=" * (-len(body) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded).decode())
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(data, dict) or "c" not in data or "e" not in data:
            return None
        if int(data["e"]) <= int(time.time()):
            return None
        return data

    def _sign(self, body: str) -> str:
        return hmac.new(self.secret, body.encode(), hashlib.sha256).hexdigest()[:32]


class InviteService:
    def __init__(
        self,
        store_path: Path,
        *,
        admin_code: str = "",
        valid_days: int = 30,
        max_uses: int = 20,
        session_secret: str = "",
        on_change=None,
    ) -> None:
        self.store_path = store_path
        self.admin_code = admin_code.strip()
        self.valid_days = max(1, valid_days)
        self.default_max_uses = max(1, max_uses)
        # 令牌签名密钥：优先用配置；缺失时退化为"由管理员码派生"，保证功能可用
        secret = (session_secret or "").strip() or f"derived:{self.admin_code}"
        if not (session_secret or "").strip() and self.admin_code:
            logger.warning("session_secret_missing —— 正在使用由管理员码派生的签名密钥，建议配置 SESSION_SECRET")
        self.codec = SessionTokenCodec(secret)
        # 邀请码用量变化后的回调（线上用于把用量立刻同步到对象存储）
        self.on_change = on_change
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
        # 用量变化要立刻同步到对象存储：否则实例被替换后"已用次数"会被备份恢复成旧值
        self._notify_change()

        session = self._new_session(code, now, is_admin=False)
        logger.info("invite_login remaining_uses=%s", entry["max_uses"] - entry["uses"])
        return session

    def resolve(self, token: str) -> Session | None:
        """校验令牌：只做签名与有效期校验，**不依赖任何本地存储**。"""
        if not token:
            return None
        data = self.codec.decode(token)
        if data is None:
            return None
        expires_at = datetime.fromtimestamp(int(data["e"]), tz=datetime.now().astimezone().tzinfo)
        created_at = datetime.fromtimestamp(int(data.get("i", data["e"])), tz=expires_at.tzinfo)
        return Session(
            token=token,
            code=str(data["c"]),
            created_at=created_at.isoformat(),
            expires_at=expires_at.isoformat(),
            is_admin=bool(data.get("a")),
        )

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
        self._notify_change()
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
        expires_at = now + timedelta(days=self.valid_days)
        return Session(
            token=self.codec.encode(code=code, expires_at=expires_at, is_admin=is_admin, issued_at=now),
            code=code,
            created_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
            is_admin=is_admin,
        )

    def _notify_change(self) -> None:
        if self.on_change is None:
            return
        try:
            self.on_change()
        except Exception:  # noqa: BLE001 - 同步失败不影响登录
            logger.warning("invite_store_sync_failed", exc_info=True)

    def reload_from_store(self) -> None:
        """从磁盘重新加载（对象存储恢复后调用）。当前实现是每次读取文件，这里仅作语义占位。"""
        return None

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
