"""邀请码登录与任务隔离（阶段 6B）的测试。

规则来自产品经理确认：管理员码永久有效、不限次数；普通码 30 天 / 20 次（一码可共用）。
另外验证：未登录被拒、错码被拒、会话之间互相看不到任务、管理员接口仅管理员可用。
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.core.errors import AppError
from backend.app.main import create_app
from backend.app.services.invite_service import InviteService


# ---------------------------------------------------------------------------
# 邀请码服务本身
# ---------------------------------------------------------------------------


def make_service(tmp_path: Path, **kwargs) -> InviteService:
    defaults = dict(
        admin_code="ADMIN-CODE",
        valid_days=30,
        max_uses=20,
    )
    defaults.update(kwargs)
    return InviteService(tmp_path / "invite-codes.json", **defaults)


def test_admin_code_is_unlimited_and_permanent(tmp_path: Path):
    service = make_service(tmp_path)
    for _ in range(50):
        session = service.login("ADMIN-CODE")
        assert session.is_admin is True
    assert service.remaining_uses("ADMIN-CODE") is None
    # 大小写与空格不影响（用户手抄常带空格）
    assert service.login(" admin-code ").is_admin is True


def test_normal_code_allows_exactly_max_uses(tmp_path: Path):
    service = make_service(tmp_path, max_uses=20)
    code = service.create_codes(count=1)[0].code

    for index in range(20):
        session = service.login(code)
        assert session.is_admin is False
        assert service.remaining_uses(code) == 19 - index

    with pytest.raises(AppError) as exc_info:
        service.login(code)
    assert exc_info.value.code == "INVITE_CODE_EXHAUSTED"
    assert "已用完" in exc_info.value.message


def test_normal_code_defaults_to_30_days_and_20_uses(tmp_path: Path):
    service = make_service(tmp_path)
    item = service.create_codes(count=1)[0]
    assert item.max_uses == 20
    expires = datetime.fromisoformat(item.expires_at)
    assert timedelta(days=29) < expires - datetime.now().astimezone() <= timedelta(days=30)


def test_expired_code_is_rejected(tmp_path: Path):
    service = make_service(tmp_path, valid_days=1)
    code = service.create_codes(count=1)[0].code

    store = json.loads(service.store_path.read_text(encoding="utf-8"))
    store["codes"][0]["expires_at"] = (datetime.now().astimezone() - timedelta(days=1)).isoformat()
    service.store_path.write_text(json.dumps(store), encoding="utf-8")

    with pytest.raises(AppError) as exc_info:
        service.login(code)
    assert exc_info.value.code == "INVITE_CODE_EXPIRED"
    assert "已过期" in exc_info.value.message


def test_unknown_code_is_rejected(tmp_path: Path):
    service = make_service(tmp_path)
    with pytest.raises(AppError) as exc_info:
        service.login("NOPE-NOPE")
    assert exc_info.value.code == "INVITE_CODE_INVALID"


def test_empty_code_is_rejected(tmp_path: Path):
    service = make_service(tmp_path)
    with pytest.raises(AppError) as exc_info:
        service.login("   ")
    assert exc_info.value.code == "INVITE_CODE_REQUIRED"


def test_session_expiry_is_enforced(tmp_path: Path):
    """令牌是自校验的：过期由签名载荷里的有效期决定，不依赖任何本地文件。"""
    service = make_service(tmp_path)
    session = service.login("ADMIN-CODE")
    assert service.resolve(session.token) is not None

    expired = service.codec.encode(
        code="ADMIN-CODE",
        expires_at=datetime.now().astimezone() - timedelta(seconds=1),
        is_admin=True,
        issued_at=datetime.now().astimezone() - timedelta(days=31),
    )
    assert service.resolve(expired) is None


def test_token_survives_instance_replacement(tmp_path: Path):
    """核心回归：线上是函数实例，实例一旦被替换，本地存储就没了。

    令牌必须**自校验**，否则用户会遇到"刚登录完提交任务却要求重新登录"（真实故障）。
    """
    data_dir = tmp_path / "data"
    service = InviteService(data_dir / "invite-codes.json", admin_code="ADMIN-CODE")
    token = service.login("ADMIN-CODE").token

    # 模拟实例被替换：新实例没有任何本地会话文件（甚至连目录都是新的）
    fresh_dir = tmp_path / "fresh-instance-data"
    fresh_service = InviteService(
        fresh_dir / "invite-codes.json",
        admin_code="ADMIN-CODE",
        session_secret="",
    )
    # 两个实例用同样的密钥来源（生产环境是同一个 SESSION_SECRET），令牌依旧有效
    assert fresh_service.resolve(token) is not None


def test_tampered_token_is_rejected(tmp_path: Path):
    service = make_service(tmp_path)
    token = service.login("ADMIN-CODE").token
    version, body, signature = token.split(".")
    flipped = ("0" if signature[0] != "0" else "1") + signature[1:]
    assert service.resolve(f"{version}.{body}.{flipped}") is None
    assert service.resolve(f"{version}.{body}.deadbeef") is None
    assert service.resolve("v1.bogus.bogus") is None
    assert service.resolve("") is None


def test_re_login_with_same_code_keeps_previous_tasks(authed_client):
    """同一个码再次登录，仍能访问此前创建的任务（避免"重登后任务消失"）。"""
    admin = login(authed_client, "ADMIN-CODE")
    task = authed_client.post(
        "/api/v1/tasks/from-url",
        json={"url": "https://www.bilibili.com/video/BV1VVhk6pEiR"},
        headers=headers(admin["token"]),
    ).json()["task_id"]

    again = login(authed_client, "ADMIN-CODE")  # 第二次登录
    assert again["token"]
    # 关键：重新登录后仍能看到此前创建的任务（归属由邀请码派生，不随令牌变化）
    assert authed_client.get(f"/api/v1/tasks/{task}", headers=headers(again["token"])).status_code == 200


def test_codes_avoid_easily_confused_characters(tmp_path: Path):
    service = make_service(tmp_path)
    codes = [item.code for item in service.create_codes(count=10)]
    assert len(set(codes)) == 10
    for code in codes:
        assert not set(code) & set("01OIL"), code  # 0/O/1/I/L 容易抄错，已排除
        assert len(code.split("-")) == 2


def test_code_store_is_persisted_and_backed_up(tmp_path: Path):
    from backend.app.services.backup_service import BackupService
    from backend.app.services.storage_service import LocalStorage

    data_dir = tmp_path / "data"
    service = InviteService(data_dir / "invite-codes.json", admin_code="A", max_uses=2)
    service.create_codes(count=1)

    storage = LocalStorage(tmp_path / "bucket")
    result = BackupService(data_dir=data_dir, storage=storage).backup_all()
    assert result.uploaded == 1
    # 邀请码与用尽的次数必须被备份，否则实例重建后就"复原"了
    assert storage.list_keys("") == ["invite-codes.json"]


# ---------------------------------------------------------------------------
# 接口层
# ---------------------------------------------------------------------------


def authed_settings(settings: Settings, **overrides) -> Settings:
    base = dict(
        app_env="test",
        require_auth=True,
        admin_invite_code="ADMIN-CODE",
        invite_valid_days=30,
        invite_max_uses=20,
    )
    base.update(overrides)
    return dataclasses.replace(settings, **base)


@pytest.fixture
def authed_client(settings):
    app = create_app(authed_settings(settings))
    app.state.processor.submit = lambda task_id: None
    with TestClient(app) as client:
        client.app_ref = app
        yield client


def login(client: TestClient, code: str) -> dict:
    response = client.post("/api/v1/auth/login", json={"code": code})
    return response.json()


def headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_local_environment_does_not_require_login(settings):
    """本地开发默认不强制登录（既有行为不变，便于调试）。"""
    app = create_app(settings)
    app.state.processor.submit = lambda task_id: None
    with TestClient(app) as client:
        assert client.get("/api/v1/config").json()["require_auth"] is False
        created = client.post(
            "/api/v1/tasks/from-url", json={"url": "https://www.bilibili.com/video/BV1VVhk6pEiR"}
        )
        assert created.status_code == 202


def test_online_requires_login(authed_client):
    assert authed_client.get("/api/v1/config").json()["require_auth"] is True

    blocked = authed_client.get("/api/v1/tasks/2b1fbd0a-6b5f-4a53-9a3f-1b0a0d1c7f11")
    assert blocked.status_code == 401
    assert blocked.json()["error"]["code"] == "AUTH_REQUIRED"
    assert "邀请码" in blocked.json()["error"]["message"]

    # 公开接口不需要登录
    assert authed_client.get("/api/v1/config").status_code == 200


def test_login_flow_and_me(authed_client):
    admin = login(authed_client, "ADMIN-CODE")
    assert admin["is_admin"] is True and admin["remaining_uses"] is None

    me = authed_client.get("/api/v1/auth/me", headers=headers(admin["token"]))
    assert me.status_code == 200 and me.json()["is_admin"] is True

    assert authed_client.get("/api/v1/auth/me").status_code == 401
    assert authed_client.get("/api/v1/auth/me", headers=headers("bogus")).status_code == 401


def test_wrong_code_is_rejected_with_chinese_message(authed_client, user_copy_checker):
    response = authed_client.post("/api/v1/auth/login", json={"code": "WRONG-CODE"})
    assert response.status_code == 401
    user_copy_checker(response.json()["error"]["message"])


def test_tasks_are_isolated_between_sessions(authed_client):
    admin = login(authed_client, "ADMIN-CODE")
    created_codes = authed_client.post(
        "/api/v1/admin/invite-codes",
        json={"count": 1, "note": "给朋友"},
        headers=headers(admin["token"]),
    ).json()["codes"]
    shared_code = created_codes[0]["code"]

    # 用两个不同的码代表两个使用者（同一个码下的多次登录共享可见性，见 test_re_login_...）
    second_code = authed_client.post(
        "/api/v1/admin/invite-codes",
        json={"count": 1},
        headers=headers(admin["token"]),
    ).json()["codes"][0]["code"]
    first = login(authed_client, shared_code)
    second = login(authed_client, second_code)

    task = authed_client.post(
        "/api/v1/tasks/from-url",
        json={"url": "https://www.bilibili.com/video/BV1VVhk6pEiR"},
        headers=headers(first["token"]),
    ).json()["task_id"]

    assert authed_client.get(f"/api/v1/tasks/{task}", headers=headers(first["token"])).status_code == 200
    for path in (f"/api/v1/tasks/{task}", f"/api/v1/tasks/{task}/segments", f"/api/v1/tasks/{task}/result"):
        forbidden = authed_client.get(path, headers=headers(second["token"]))
        assert forbidden.status_code == 403, path
        assert forbidden.json()["error"]["code"] == "TASK_FORBIDDEN"
    assert (
        authed_client.post(f"/api/v1/tasks/{task}/cancel", headers=headers(second["token"])).status_code
        == 403
    )
    assert (
        authed_client.get(
            f"/api/v1/tasks/{task}/download/markdown", headers=headers(second["token"])
        ).status_code
        == 403
    )


def test_task_records_owner(authed_client):
    """创建任务时会写入归属，供隔离判断使用。"""
    admin = login(authed_client, "ADMIN-CODE")
    task_id = authed_client.post(
        "/api/v1/tasks/from-url",
        json={"url": "https://www.bilibili.com/video/BV1VVhk6pEiR"},
        headers=headers(admin["token"]),
    ).json()["task_id"]
    record = authed_client.app_ref.state.task_service.get(task_id)
    session = authed_client.app_ref.state.invite_service.resolve(admin["token"])
    assert session is not None
    # 归属由邀请码派生（不是令牌），这样重新登录后仍能看到自己的任务
    assert record.owner_id == session.owner_id


def test_admin_endpoints_require_admin(authed_client):
    admin = login(authed_client, "ADMIN-CODE")
    code = authed_client.post(
        "/api/v1/admin/invite-codes", json={"count": 1}, headers=headers(admin["token"])
    ).json()["codes"][0]["code"]
    normal = login(authed_client, code)

    assert authed_client.get("/api/v1/admin/invite-codes").status_code == 401
    assert (
        authed_client.get("/api/v1/admin/invite-codes", headers=headers(normal["token"])).status_code
        == 403
    )

    listing = authed_client.get("/api/v1/admin/invite-codes", headers=headers(admin["token"])).json()
    assert listing["codes"][0]["code"] == code
    assert listing["codes"][0]["max_uses"] == 20
    assert listing["codes"][0]["uses"] == 1  # 上面普通会话登录过一次


def test_legacy_task_without_owner_is_not_visible_online(authed_client):
    """v1–v3 的历史任务没有归属：线上不允许被任意会话看到。"""
    tasks = authed_client.app_ref.state.task_service
    task_id = tasks.new_task_id()
    tasks.create(
        task_id=task_id,
        original_filename="legacy.mp3",
        stored_filename="source.mp3",
        media_type="audio",
        content_type="audio/mpeg",
        size_bytes=1,
    )
    admin = login(authed_client, "ADMIN-CODE")
    assert (
        authed_client.get(f"/api/v1/tasks/{task_id}", headers=headers(admin["token"])).status_code
        == 403
    )


def test_schema_v3_records_still_readable(settings):
    """schema 升到 v4（新增 owner_id）后，v3 记录仍可读取。"""
    tasks = settings.tasks_dir
    tasks.mkdir(parents=True, exist_ok=True)
    task_id = "13f154b7-feb7-412f-b9c1-3ee1ef20cf87"
    (tasks / f"{task_id}.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "task_id": task_id,
                "status": "succeeded",
                "original_filename": "a.m4a",
                "stored_filename": "source.m4a",
                "media_type": "audio",
                "size_bytes": 1,
                "created_at": datetime.now().astimezone().isoformat(),
                "updated_at": datetime.now().astimezone().isoformat(),
                "progress_stage": "succeeded",
                "artifacts": {"markdown": None, "txt": None, "result": None},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    from backend.app.services.task_service import TaskService

    record = TaskService(tasks).get(task_id)
    assert record.owner_id is None
