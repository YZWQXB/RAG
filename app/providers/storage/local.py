"""文件系统存储实现。默认后端，零外部依赖，便于本地开发与 CI。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.config import settings
from app.core.errors import AppError, BadRequestError


class StorageObjectNotFound(AppError):
    code = "FILE_NOT_FOUND"
    status_code = 404
    message = "文件不存在"


class LocalStorage:
    def __init__(self, base_dir: str | None = None) -> None:
        self.base = Path(base_dir or settings.storage_local_dir).resolve()

    def _path(self, key: str) -> Path:
        candidate = (self.base / key).resolve()
        # 防止 key 中出现 ../ 造成越界写入
        if not str(candidate).startswith(str(self.base)):
            raise BadRequestError("非法的存储路径", code="INVALID_PATH")
        return candidate

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise StorageObjectNotFound()
        return await asyncio.to_thread(path.read_bytes)

    async def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            await asyncio.to_thread(path.unlink)

    async def presign_get(self, key: str, expires: int = 300) -> str:
        # 本地后端没有签名 URL 的概念；需要直链时请切换到 s3 后端。
        raise NotImplementedError("本地存储不支持签名 URL")
