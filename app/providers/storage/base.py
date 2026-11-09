"""对象存储抽象。

本地开发用 LocalStorage（文件系统，零依赖），生产用 S3Storage（MinIO/OSS/S3）。
业务代码只依赖 StorageProvider 协议。
"""

from __future__ import annotations

import re
from typing import Protocol
from urllib.parse import quote


class StorageProvider(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    async def presign_get(self, key: str, expires: int = 300) -> str: ...


def doc_key(tenant_id: object, kb_id: object, doc_id: object, version: int, filename: str) -> str:
    """对象 key 的唯一构造入口。

    文件名只作为 key 的末段且经过清洗，不参与任何路径解析，避免路径穿越。
    """
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[-80:] or "file"
    # 去掉所有点，避免 ".." 构成路径穿越片段
    safe = safe.replace(".", "_").lstrip("_")
    return f"tenant/{tenant_id}/kb/{kb_id}/{doc_id}/v{version}/{safe}"


def url_quote(key: str) -> str:
    return quote(key, safe="")
