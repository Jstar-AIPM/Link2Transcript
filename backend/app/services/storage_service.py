"""对象存储抽象（阶段 6 上线适配）。

为什么需要：veFaaS 函数实例**除 `/tmp` 外只读**，而 `/tmp` 是临时的（实例回收即丢）。
所以业务数据（任务记录、片段、逐字稿产物）必须定期备份到对象存储，并在启动时恢复。

三种模式：

- `disabled`（默认）：不做任何备份，行为与本地上线前完全一致（本地开发用）；
- `local`：备份到本机某个目录 —— 用于**测试备份/恢复逻辑**，也可用于自建服务器的场景；
- `tos`：备份到火山引擎对象存储（S3 兼容协议）。

设计上刻意保持极小的接口面：只按「键 → 文件」存取，不做目录语义、不做版本管理。
备份范围由调用方（BackupService）决定。
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Protocol

from backend.app.core.errors import AppError


logger = logging.getLogger(__name__)


class StorageBackend(Protocol):
    """对象存储最小接口。所有实现都只处理「键」与「本地文件」的对应关系。"""

    @property
    def enabled(self) -> bool: ...

    def put_file(self, key: str, path: Path) -> None: ...

    def get_file(self, key: str, path: Path) -> bool:
        """下载到 ``path``；对象不存在时返回 ``False``（不抛错）。"""
        ...

    def list_keys(self, prefix: str = "") -> list[str]: ...

    def delete(self, key: str) -> None: ...


class DisabledStorage:
    """未配置备份：所有操作都是安全的空操作。"""

    enabled = False

    def put_file(self, key: str, path: Path) -> None:  # noqa: ARG002
        return None

    def get_file(self, key: str, path: Path) -> bool:  # noqa: ARG002
        return False

    def list_keys(self, prefix: str = "") -> list[str]:  # noqa: ARG002
        return []

    def delete(self, key: str) -> None:  # noqa: ARG002
        return None


class LocalStorage:
    """备份到本地目录：用于测试备份/恢复逻辑，或单机自建部署。"""

    enabled = True

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # 只允许写在 root 之内，防止键里出现 ../ 逃逸
        target = (self.root / key).resolve()
        if not target.is_relative_to(self.root.resolve()):
            raise AppError("STORAGE_INVALID_KEY", "备份路径非法", status_code=500)
        return target

    def put_file(self, key: str, path: Path) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)

    def get_file(self, key: str, path: Path) -> bool:
        source = self._path(key)
        if not source.is_file():
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, path)
        return True

    def list_keys(self, prefix: str = "") -> list[str]:
        keys: list[str] = []
        for item in self.root.rglob("*"):
            if item.is_file():
                keys.append(item.relative_to(self.root).as_posix())
        return sorted(key for key in keys if key.startswith(prefix))

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class TosStorage:
    """火山引擎对象存储（S3 兼容）。

    参数来自官方文档：endpoint ``https://tos-s3-cn-beijing.volces.com``，
    必须显式指定 ``signature_version="s3v4"`` 与 ``addressing_style="virtual"``。
    boto3 采用延迟导入：未使用 TOS 时不需要这个依赖被加载。
    """

    enabled = True

    def __init__(
        self,
        *,
        bucket: str,
        endpoint: str,
        region: str,
        access_key: str,
        secret_key: str,
        client=None,
    ) -> None:
        self.bucket = bucket
        self._client = client or self._build_client(
            endpoint=endpoint, region=region, access_key=access_key, secret_key=secret_key
        )

    @staticmethod
    def _build_client(*, endpoint: str, region: str, access_key: str, secret_key: str):
        import boto3
        from botocore.config import Config

        return boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region or "cn-beijing",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "virtual"},
            ),
        )

    def put_file(self, key: str, path: Path) -> None:
        self._client.upload_file(str(path), self.bucket, key)

    def get_file(self, key: str, path: Path) -> bool:
        from botocore.exceptions import ClientError

        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._client.download_file(self.bucket, key, str(path))
        except ClientError as exc:
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        return True

    def list_keys(self, prefix: str = "") -> list[str]:
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                keys.append(item["Key"])
        return keys

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)


def build_storage(settings) -> StorageBackend:
    """按配置构建存储后端；配置不完整时退化为 disabled（并记录告警）。"""
    provider = (settings.storage_provider or "disabled").strip().lower()
    if provider in {"", "disabled", "none", "off"}:
        return DisabledStorage()
    if provider == "local":
        if settings.storage_local_dir is None:
            logger.warning("storage_local_dir_missing fallback=disabled")
            return DisabledStorage()
        return LocalStorage(settings.storage_local_dir)
    if provider == "tos":
        missing = [
            name
            for name, value in (
                ("TOS_BUCKET", settings.tos_bucket),
                ("TOS_ENDPOINT", settings.tos_endpoint),
                ("TOS_ACCESS_KEY", settings.tos_access_key),
                ("TOS_SECRET_KEY", settings.tos_secret_key),
            )
            if not value
        ]
        if missing:
            logger.warning("storage_tos_incomplete missing=%s fallback=disabled", ",".join(missing))
            return DisabledStorage()
        return TosStorage(
            bucket=settings.tos_bucket,
            endpoint=settings.tos_endpoint,
            region=settings.tos_region,
            access_key=settings.tos_access_key,
            secret_key=settings.tos_secret_key,
        )
    logger.warning("storage_provider_unknown provider=%s fallback=disabled", provider)
    return DisabledStorage()
