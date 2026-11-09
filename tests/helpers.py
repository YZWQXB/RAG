"""测试辅助：精确控制向量，避免用例依赖模型语义质量。"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import session_scope
from app.modules.ingestion.models import Chunk, Document, DocumentVersion
from app.modules.knowledge.models import KnowledgeBase


def basis(index: int, dim: int = 1024) -> list[float]:
    """第 index 维为 1 的单位向量。余弦相似度可直接预期。"""
    vector = [0.0] * dim
    vector[index] = 1.0
    return vector


async def fake_vector(text: str) -> list[float]:
    """用 FakeEmbedding 对文本编码，保证与 chat 链路里的查询向量同一空间。"""
    from app.providers.embedding.fake import FakeEmbedding

    return (await FakeEmbedding().embed([text]))[0]


async def seed_document(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    chunks: list[tuple[str, list[float]]],
    *,
    title: str = "测试文档",
    pages: list[int] | None = None,
    kb_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    """创建 KB + 文档 + 块，返回 (kb_id, document_id, chunk_ids)。"""
    kb_id = kb_id or uuid.uuid4()
    doc_id = uuid.uuid4()
    version_id = uuid.uuid4()

    async with session_scope(tenant_id=tenant_id) as session:
        if kb_id is not None:
            exists = await session.get(KnowledgeBase, kb_id)
        if exists is None:
            session.add(
                KnowledgeBase(
                    id=kb_id,
                    tenant_id=tenant_id,
                    name="测试库",
                    chunk_config={},
                    created_by=user_id,
                )
            )
            # 必须先 flush：模型之间没有 relationship()，SQLAlchemy 的 flush 计划
            # 不会按外键排序，同一批 add 可能先插 documents 导致外键失败。
            await session.flush()
        session.add(
            Document(
                id=doc_id,
                tenant_id=tenant_id,
                kb_id=kb_id,
                title=title,
                mime="text/markdown",
                size_bytes=100,
                content_hash=uuid.uuid4().hex,
                status="ready",
                created_by=user_id,
            )
        )
        await session.flush()
        session.add(
            DocumentVersion(
                id=version_id,
                tenant_id=tenant_id,
                document_id=doc_id,
                version_no=1,
                storage_key=f"k/{doc_id}",
                content_hash="x",
                status="ready",
            )
        )
        await session.flush()

        chunk_ids: list[uuid.UUID] = []
        for index, (content, vector) in enumerate(chunks):
            chunk_id = uuid.uuid4()
            chunk_ids.append(chunk_id)
            session.add(
                Chunk(
                    id=chunk_id,
                    tenant_id=tenant_id,
                    kb_id=kb_id,
                    document_id=doc_id,
                    version_id=version_id,
                    chunk_index=index,
                    content=content,
                    token_count=max(1, len(content) // 2),
                    heading_path="测试 > 章节",
                    page_from=(pages or [])[index] if pages else index + 1,
                    page_to=(pages or [])[index] if pages else index + 1,
                    content_hash=uuid.uuid4().hex,
                    embedding=vector,
                )
            )
        await session.flush()

    return kb_id, doc_id, chunk_ids


async def add_chunk(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    kb_id: uuid.UUID,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    index: int,
    content: str,
    vector: list[float],
) -> uuid.UUID:
    chunk_id = uuid.uuid4()
    session.add(
        Chunk(
            id=chunk_id,
            tenant_id=tenant_id,
            kb_id=kb_id,
            document_id=document_id,
            version_id=version_id,
            chunk_index=index,
            content=content,
            token_count=max(1, len(content) // 2),
            page_from=1,
            page_to=1,
            content_hash=uuid.uuid4().hex,
            embedding=vector,
        )
    )
    await session.flush()
    return chunk_id


async def create_member_token(tenant_id: uuid.UUID, role: str, email: str) -> tuple[uuid.UUID, str]:
    """在指定租户下建一个指定角色的成员，返回 (user_id, token)。"""
    from app.core.db import admin_session
    from app.core.security import create_access_token, hash_password

    user_id = uuid.uuid4()
    async with admin_session() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, email, password_hash, name) "
                "VALUES (:id, :email, :pw, :name)"
            ),
            {"id": user_id, "email": email, "pw": hash_password("Passw0rd!"), "name": role},
        )
        await session.execute(
            text(
                "INSERT INTO memberships (id, tenant_id, user_id, role) "
                "VALUES (:id, :tid, :uid, :role)"
            ),
            {"id": uuid.uuid4(), "tid": tenant_id, "uid": user_id, "role": role},
        )
    return user_id, create_access_token(user_id, tenant_id, role)


def client_with_token(token: str):
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    return AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
