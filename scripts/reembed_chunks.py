"""重建已有分块的向量。

换 embedding 模型后必须执行：旧向量与新模型不在同一向量空间，
余弦相似度会退化成随机值，检索等于失效，但文档内容本身没问题，
所以只需要重算向量、不需要重新上传。

用法：
    python -m scripts.reembed_chunks                 # 全部租户
    python -m scripts.reembed_chunks --tenant yzwqxb-3823
    python -m scripts.reembed_chunks --dry-run       # 只统计不写入
"""

from __future__ import annotations

import argparse
import asyncio
import time

from sqlalchemy import select, text, update

from app.core.db import admin_engine, dispose_engines, session_scope
from app.modules.ingestion.models import Chunk
from app.providers import get_embedding_provider


async def _tenant_ids(slug: str | None) -> list:
    async with admin_engine.connect() as conn:
        if slug:
            row = (
                await conn.execute(text("select id from tenants where slug = :s"), {"s": slug})
            ).scalar()
            if row is None:
                raise SystemExit(f"未找到租户 slug={slug}")
            return [row]
        return list((await conn.execute(text("select id from tenants"))).scalars().all())


async def _reembed_tenant(tenant_id, provider, batch_size: int, dry_run: bool) -> int:
    async with session_scope(tenant_id=tenant_id) as session:
        rows = (
            await session.execute(
                select(Chunk.id, Chunk.content)
                .where(Chunk.tenant_id == tenant_id)
                .order_by(Chunk.document_id, Chunk.chunk_index)
            )
        ).all()

    if not rows:
        return 0
    if dry_run:
        print(f"  tenant={tenant_id} 待处理 {len(rows)} 块（dry-run 未写入）")
        return len(rows)

    done = 0
    started = time.perf_counter()
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        vectors = await provider.embed([row.content for row in batch])
        async with session_scope(tenant_id=tenant_id) as session:
            for row, vector in zip(batch, vectors, strict=True):
                await session.execute(
                    update(Chunk).where(Chunk.id == row.id).values(embedding=vector)
                )
        done += len(batch)
        elapsed = time.perf_counter() - started
        speed = done / elapsed if elapsed else 0
        print(
            f"  tenant={tenant_id} {done}/{len(rows)} {speed:.1f} 块/秒",
            flush=True,
        )
    return done


async def main() -> None:
    parser = argparse.ArgumentParser(description="重建分块向量")
    parser.add_argument("--tenant", help="只处理指定租户 slug")
    parser.add_argument("--batch", type=int, default=16, help="每批编码条数")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写入")
    args = parser.parse_args()

    provider = get_embedding_provider()
    print(f"向量 Provider: {type(provider).__name__}")
    total = 0
    for tenant_id in await _tenant_ids(args.tenant):
        got = await _reembed_tenant(tenant_id, provider, args.batch, args.dry_run)
        total += got
        if got:
            print(f"  -> 完成 {got} 块")
    print(f"总计 {total} 块")
    await dispose_engines()


if __name__ == "__main__":
    asyncio.run(main())
