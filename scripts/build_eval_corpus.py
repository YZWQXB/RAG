"""构建评测语料：把 fixtures 下的文档入库到独立租户，输出 chunk 索引。

用法：uv run python -m scripts.build_eval_corpus
输出：tests/eval/corpus.json（已在 .gitignore 中，可随时重建）
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sqlalchemy import select

from app.core.db import dispose_engines, session_scope
from app.core.errors import ConflictError
from app.core.security import Principal
from app.modules.ingestion.models import Chunk, Document
from app.modules.ingestion.service import upload_document
from app.modules.knowledge.schemas import KbCreate
from app.modules.knowledge.service import create_kb, list_kbs
from app.modules.tenancy.service import register_tenant
from worker.tasks.outbox import poll_once

FIXTURES = Path("tests/fixtures")
# 语料要覆盖多个互不相关的主题，否则「召回不到」和「召回到错的主题」区分不开
DOCS = [
    "sample.md",
    "after_sales.md",
    "product.md",
    "it_security.md",
    "travel_expense.md",
    "onboarding.md",
]
EVAL_SLUG = "eval-lab"
EVAL_EMAIL = "eval@example.com"
OUTPUT = Path("tests/eval/corpus.json")


async def _ensure_tenant() -> tuple:
    try:
        return await register_tenant(EVAL_EMAIL, "Passw0rd!", "评测空间", EVAL_SLUG)
    except ConflictError:
        from sqlalchemy import text

        from app.core.db import admin_engine

        async with admin_engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        text(
                            "SELECT t.id, m.user_id FROM tenants t "
                            "JOIN memberships m ON m.tenant_id = t.id "
                            "JOIN users u ON u.id = m.user_id WHERE u.email = :e"
                        ),
                        {"e": EVAL_EMAIL},
                    )
                )
                .mappings()
                .one()
            )
        return type("Existing", (), {"tenant_id": row["id"], "user_id": row["user_id"]})()


async def main() -> None:
    account = await _ensure_tenant()
    tenant_id = account.tenant_id
    user_id = account.user_id
    principal = Principal(user_id=user_id, tenant_id=tenant_id, role="owner")

    async with session_scope(tenant_id=tenant_id) as session:
        existing = await list_kbs(session, principal)
        if existing:
            kb_id = existing[0].id
        else:
            kb = await create_kb(session, principal, KbCreate(name="评测知识库"))
            kb_id = kb.id

    async with session_scope(tenant_id=tenant_id) as session:
        for name in DOCS:
            raw = (FIXTURES / name).read_bytes()
            await upload_document(session, principal, kb_id, name, raw)

    while await poll_once(limit=50):
        pass

    async with session_scope(tenant_id=tenant_id) as session:
        rows = (
            await session.execute(
                select(Chunk.id, Chunk.content, Document.title)
                .join(Document, Document.id == Chunk.document_id)
                .order_by(Document.title, Chunk.chunk_index)
            )
        ).all()

    corpus = [{"chunk_id": str(row[0]), "content": row[1], "title": row[2]} for row in rows]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"corpus built: {len(corpus)} chunks -> {OUTPUT}")
    await dispose_engines()


if __name__ == "__main__":
    asyncio.run(main())
