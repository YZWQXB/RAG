from __future__ import annotations

import pytest

from app.core.errors import AppError
from app.providers.storage.base import doc_key
from app.providers.storage.local import LocalStorage, StorageObjectNotFound


async def test_put_get_delete(tmp_path):
    storage = LocalStorage(str(tmp_path))
    key = doc_key("t1", "k1", "d1", 1, "报告 (终版).pdf")

    await storage.put(key, b"hello", "application/pdf")
    assert await storage.get(key) == b"hello"

    await storage.delete(key)
    with pytest.raises(StorageObjectNotFound):
        await storage.get(key)


def test_doc_key_sanitizes_filename():
    key = doc_key("t1", "k1", "d1", 2, "../../etc/passwd")
    assert ".." not in key
    assert key.startswith("tenant/t1/kb/k1/d1/v2/")


async def test_path_traversal_blocked(tmp_path):
    storage = LocalStorage(str(tmp_path))
    with pytest.raises(AppError, match="非法的存储路径"):
        await storage.get("../../secret.txt")


async def test_presign_not_supported_locally(tmp_path):
    storage = LocalStorage(str(tmp_path))
    with pytest.raises(NotImplementedError):
        await storage.presign_get("any/key")
