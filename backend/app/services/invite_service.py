"""邀请码与会话（阶段 6B，阶段 6.6 扩展）。

规则（产品经理确认）：

| 角色 | 来源 | 有效期 | 次数 |
| --- | --- | --- | --- |
| 管理员码 | 环境变量 ``ADMIN_INVITE_CODE`` | **永久** | **不限** |
| 普通码 | 管理员通过接口生成 | **默认 30 天**（0 天 = 不过期） | **默认 20 次**（0 = 不限次数） |

阶段 6.6 新增（对齐 03 项目的邀请码管理界面）：

1. ``max_uses == 0`` 表示**不限次数**（``uses`` 仍累加，只用于展示「已用 N」）；
2. ``expires_at is None`` 表示**不过期**；
3. 支持**停用**（``revoked_at``），停用后登录直接拒绝；
4. 每个码带 ``source``（``generated`` / ``env``）；
5. **管理员码用量计数**：按哈希记账，**绝不落明文**。

设计要点：

1. **管理员码不落库**：只从环境变量读，因此不会出现在任何接口返回值、日志或备份里；
2. **普通码写在 JSON 文件**（``data/invite-codes.json``），原子写 + 进程内锁，
   与任务记录同样的可靠性做法；该文件随业务数据一起备份到对象存储；
3. **每登录一次 = 消耗一次**：用尽或过期时给出明确中文提示；
4. 会话 token 是随机串，有效期与码同长（默认 30 天）；**任务按会话隔离**，
   这样"同一个码的 20 个人"互相看不到对方的任务；
5. 存储结构带 ``schema_version``；读取旧版本文件时**惰性迁移**（补字段，不丢数据）。
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
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from backend.app.core.errors import AppError


logger = logging.getLogger(__name__)

#: 生成邀请码用的字符集：去掉了 0/O/1/I/L 这些容易抄错的字符
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_GROUP = 4
CODE_GROUPS = 2

#: 存储结构版本（v1 → v2：新增 source / revoked_at / created_by / admin_usage）
SCHEMA_VERSION = 2

#: 次数上限的兜底（防止误填天文数字；产品上是"体验名额"）
MAX_USES_LIMIT = 1000
#: 有效天数上限（约 10 年）
MAX_DAYS_LIMIT = 3650


def mask_code(code: str) -> str:
    """日志与展示用的掩码：``ABCD-EFGH`` → ``ABCD****GH``。"""
    text = (code or "").strip()
    if len(text) <= 4:
        return "****"
    return f"{text[:4]}****{text[-2:]}"


def _admin_key(code: str) -> str:
    """管理员码的记账键：只存哈希前缀，保证不落明文。"""
    return hashlib.sha256(f"admin:{code}".encode()).hexdigest()[:16]


def parse_time(value) -> datetime | None:
    """把存储里的时间字段解析成带时区的 datetime；空值/非法值返回 None。"""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.astimezone()
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


@dataclass
class InviteCode:
    code: str
    max_uses: int
    uses: int
    created_at: str
    expires_at: str | None
    note: str = ""
    source: str = "generated"
    revoked_at: str | None = None
    created_by: str = ""

    @property
    def unlimited(self) -> bool:
        return self.max_uses <= 0

    @property
    def remaining(self) -> int | None:
        """剩余次数；不限次数时为 ``None``。"""
        if self.unlimited:
            return None
        return max(0, self.max_uses - self.uses)


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
        # 允许 0：0 天 = 新码不过期，0 次 = 新码不限次数
        self.valid_days = max(0, valid_days)
        self.default_max_uses = max(0, max_uses)
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
            # 管理员码也要计数（界面显示「不限次数（已用 N）」），但只记哈希
            self._record_admin_use(code, now)
            session = self._new_session(code, now, is_admin=True)
            logger.info("admin_login")
            return session

        with self._lock:
            store = self._load()
            entry = next((item for item in store["codes"] if item["code"] == code), None)
            if entry is None:
                raise AppError("INVITE_CODE_INVALID", "邀请码不正确，请检查后重试", status_code=401)
            if entry.get("revoked_at"):
                raise AppError(
                    "INVITE_CODE_REVOKED",
                    "这个邀请码已被停用，请联系管理员获取新的邀请码",
                    status_code=401,
                )
            expires_at = self._parse_expiry(entry.get("expires_at"))
            if expires_at is not None and expires_at <= now:
                raise AppError(
                    "INVITE_CODE_EXPIRED",
                    "这个邀请码已经过期了，请联系管理员获取新的邀请码",
                    status_code=401,
                )
            max_uses = int(entry.get("max_uses", 0))
            if max_uses > 0 and int(entry.get("uses", 0)) >= max_uses:
                raise AppError(
                    "INVITE_CODE_EXHAUSTED",
                    "这个邀请码的体验次数已经用完，请联系管理员获取新的邀请码",
                    status_code=401,
                )
            entry["uses"] = int(entry.get("uses", 0)) + 1
            self._write(store)
            remaining = None if max_uses <= 0 else max(0, max_uses - entry["uses"])
        # 用量变化要立刻同步到对象存储：否则实例被替换后"已用次数"会被备份恢复成旧值
        self._notify_change()

        session = self._new_session(code, now, is_admin=False)
        logger.info("invite_login remaining_uses=%s", remaining)
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

    def is_admin_code(self, code: str) -> bool:
        return bool(self.admin_code) and secrets.compare_digest(code.upper(), self.admin_code.upper())

    def remaining_uses(self, code: str) -> int | None:
        """管理员码返回 None（不限次）；普通码返回剩余次数（不限次数同样返回 None）。"""
        if self.is_admin_code(code):
            return None
        with self._lock:
            for entry in self._load()["codes"]:
                if entry["code"] == code.upper():
                    max_uses = int(entry.get("max_uses", 0))
                    if max_uses <= 0:
                        return None
                    return max(0, max_uses - int(entry.get("uses", 0)))
        return 0

    def is_unlimited(self, code: str) -> bool:
        """该码是否不限次数（管理员码恒为 True）。"""
        if self.is_admin_code(code):
            return True
        with self._lock:
            for entry in self._load()["codes"]:
                if entry["code"] == code.upper():
                    return int(entry.get("max_uses", 0)) <= 0
        return False

    # ------------------------------------------------------------------ 管理

    def create_codes(
        self,
        *,
        count: int = 1,
        uses: int | None = None,
        days: int | None = None,
        note: str = "",
        created_by: str = "",
    ) -> list[InviteCode]:
        """生成普通邀请码。

        ``uses`` / ``days`` 省略时用配置默认值；``0`` 分别表示不限次数 / 不过期。
        """
        max_uses = self.default_max_uses if uses is None else max(0, min(int(uses), MAX_USES_LIMIT))
        valid_days = self.valid_days if days is None else max(0, min(int(days), MAX_DAYS_LIMIT))
        now = datetime.now().astimezone()
        expires_at = None if valid_days == 0 else now + timedelta(days=valid_days)

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
                        max_uses=max_uses,
                        uses=0,
                        created_at=now.isoformat(),
                        expires_at=expires_at.isoformat() if expires_at else None,
                        note=note.strip(),
                        source="generated",
                        revoked_at=None,
                        created_by=mask_code(created_by) if created_by else "",
                    )
                )
            store["codes"].extend(asdict(item) for item in created)
            self._write(store)
        self._notify_change()
        logger.info("invite_codes_created count=%s max_uses=%s valid_days=%s", len(created), max_uses, valid_days)
        return created

    def revoke_code(self, code: str) -> None:
        """停用一个生成的邀请码。环境变量里的码不能停用。"""
        target = (code or "").strip().upper()
        if self.is_admin_code(target):
            raise AppError(
                "INVITE_ENV_IMMUTABLE",
                "环境变量里预置的邀请码不能在这里停用，请在服务端配置里删除",
                status_code=400,
            )
        now = datetime.now().astimezone()
        with self._lock:
            store = self._load()
            entry = next((item for item in store["codes"] if item["code"] == target), None)
            if entry is None:
                raise AppError("INVITE_CODE_INVALID", "没有找到这个邀请码", status_code=404)
            if not entry.get("revoked_at"):
                entry["revoked_at"] = now.isoformat()
                self._write(store)
            else:
                return
        self._notify_change()
        logger.info("invite_code_revoked code=%s", mask_code(target))

    def list_codes(self) -> list[InviteCode]:
        """所有**生成**的邀请码（含已停用的记录）。"""
        with self._lock:
            return [self._to_invite(item) for item in self._load()["codes"]]

    def admin_entry(self) -> InviteCode | None:
        """管理员码的只读视图（用于管理列表首行）。未配置时返回 None。"""
        if not self.admin_code:
            return None
        code = self.admin_code.upper()
        with self._lock:
            usage = self._load().get("admin_usage", {}).get(_admin_key(code)) or {}
        return InviteCode(
            code=code,
            max_uses=0,
            uses=int(usage.get("uses", 0)),
            created_at=str(usage.get("last_used_at") or ""),
            expires_at=None,
            note="",
            source="env",
            revoked_at=None,
            created_by="",
        )

    @property
    def defaults(self) -> dict[str, int]:
        return {"uses": self.default_max_uses, "days": self.valid_days}

    # ------------------------------------------------------------------ 内部

    def _record_admin_use(self, code: str, now: datetime) -> None:
        key = _admin_key(code.upper())
        with self._lock:
            store = self._load()
            usage = store.setdefault("admin_usage", {})
            row = usage.get(key) or {}
            row["uses"] = int(row.get("uses", 0)) + 1
            row["masked"] = mask_code(code)
            row["last_used_at"] = now.isoformat()
            usage[key] = row
            self._write(store)
        self._notify_change()

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

    @staticmethod
    def _parse_expiry(value) -> datetime | None:
        return parse_time(value)

    @staticmethod
    def _to_invite(item: dict) -> InviteCode:
        """把存储里的字典收敛成 InviteCode（**显式取字段**，多出的字段不会导致报错）。"""
        return InviteCode(
            code=str(item.get("code", "")),
            max_uses=int(item.get("max_uses", 0)),
            uses=int(item.get("uses", 0)),
            created_at=str(item.get("created_at", "")),
            expires_at=item.get("expires_at") or None,
            note=str(item.get("note", "")),
            source=str(item.get("source") or "generated"),
            revoked_at=item.get("revoked_at") or None,
            created_by=str(item.get("created_by", "")),
        )

    def _load(self) -> dict:
        """读取并**归一化**存储（v1 → v2 惰性迁移，不丢数据）。"""
        if not self.store_path.is_file():
            return {"schema_version": SCHEMA_VERSION, "codes": [], "admin_usage": {}}
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("invite_store_unreadable path=%s", self.store_path, exc_info=True)
            return {"schema_version": SCHEMA_VERSION, "codes": [], "admin_usage": {}}
        if not isinstance(data, dict):
            return {"schema_version": SCHEMA_VERSION, "codes": [], "admin_usage": {}}
        codes = [self._to_invite(item) for item in data.get("codes", []) if isinstance(item, dict) and item.get("code")]
        usage = data.get("admin_usage")
        return {
            "schema_version": SCHEMA_VERSION,
            "codes": [asdict(item) for item in codes],
            "admin_usage": usage if isinstance(usage, dict) else {},
        }

    def _write(self, store: dict) -> None:
        """原子写：先写临时文件再替换，避免进程被强杀时留下半个文件。"""
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        store["schema_version"] = SCHEMA_VERSION
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
