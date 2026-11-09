from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Query, Response, UploadFile, status

from app.api.v1.deps import CurrentPrincipal, DbSession
from app.modules.ingestion import service
from app.modules.ingestion.schemas import DocumentListOut, DocumentOut, UploadOut

router = APIRouter(tags=["documents"])


@router.post(
    "/kbs/{kb_id}/documents",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=UploadOut,
)
async def upload_document(
    kb_id: uuid.UUID,
    principal: CurrentPrincipal,
    session: DbSession,
    file: UploadFile = File(...),
) -> UploadOut:
    raw = await file.read()
    return await service.upload_document(
        session, principal, kb_id, file.filename or "未命名文档", raw
    )


@router.get("/kbs/{kb_id}/documents", response_model=DocumentListOut)
async def list_documents(
    kb_id: uuid.UUID,
    principal: CurrentPrincipal,
    session: DbSession,
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
) -> DocumentListOut:
    items, total = await service.list_documents(
        session, principal, kb_id, status=status_filter, page=page, size=size
    )
    return DocumentListOut(items=items, total=total, page=page, size=size)


@router.get("/documents/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession
) -> DocumentOut:
    return await service.get_document(session, principal, document_id)


@router.post("/documents/{document_id}/retry", response_model=UploadOut)
async def retry_document(
    document_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession
) -> UploadOut:
    return await service.retry_document(session, principal, document_id)


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
async def delete_document(document_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession):
    await service.delete_document(session, principal, document_id)
