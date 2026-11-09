"""本地种子数据：两个租户，各一个 owner 账号。重复执行幂等。

用法：uv run python -m scripts.seed
"""

from __future__ import annotations

import asyncio

from app.core.db import dispose_engines
from app.core.errors import ConflictError
from app.modules.tenancy.service import register_tenant

ACCOUNTS = [
    ("demo-a@example.com", "Passw0rd!", "租户 A 空间", "demo-a"),
    ("demo-b@example.com", "Passw0rd!", "租户 B 空间", "demo-b"),
]


async def main() -> None:
    for email, password, name, slug in ACCOUNTS:
        try:
            result = await register_tenant(email, password, name, slug)
            print(f"created tenant {slug} ({result.tenant_id})")
        except ConflictError:
            print(f"skip existing tenant {slug}")
    await dispose_engines()


if __name__ == "__main__":
    asyncio.run(main())
