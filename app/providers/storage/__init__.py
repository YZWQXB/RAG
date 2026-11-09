"""存储后端工厂。业务代码只调用 get_storage()。"""

from __future__ import annotations

from app.core.config import settings
from app.providers.storage.base import StorageProvider


def get_storage() -> StorageProvider:
    if settings.storage_backend == "s3":
        from app.providers.storage.s3 import S3Storage

        return S3Storage()
    from app.providers.storage.local import LocalStorage

    return LocalStorage()
