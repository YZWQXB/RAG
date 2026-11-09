"""初始化对象存储桶。仅 s3 后端需要；local 后端直接返回。

用法：uv run python -m scripts.init_bucket
"""

from __future__ import annotations

import asyncio

from app.core.config import settings


async def main() -> None:
    if settings.storage_backend != "s3":
        print(f"storage backend = {settings.storage_backend}, nothing to do")
        return

    from app.providers.storage.s3 import S3Storage

    storage = S3Storage()
    await storage.ensure_bucket()
    print(f"bucket ready: {settings.s3_bucket}")


if __name__ == "__main__":
    asyncio.run(main())
