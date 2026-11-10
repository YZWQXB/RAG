"""问答编排与 SSE 事件流。

事务策略：不在流式过程中持有数据库会话。
- Phase A（短事务）：检索 + 落用户消息
- Phase B（无事务）：流式生成
- Phase C（短事务）：落助手消息 + 引用 + 用量
这样长连接不会占用连接池，也避免"生成一半时事务超时"。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import AsyncIterator

from app.core.config import settings
from app.core.context import get_trace_id
from app.core.db import session_scope
from app.core.errors import AppError, PermissionDeniedError
from app.core.logging import get_logger
from app.core.metrics import CHAT_OUTCOME
from app.core.security import Principal
from app.core.tokens import count_tokens
from app.modules.conversation import service as conversation_service
from app.modules.generation.citations import extract_citations
from app.modules.generation.prompt import build_messages, prompt_tokens
from app.modules.iam.permissions import filter_readable_kbs
from app.modules.quota.service import record_usage
from app.modules.retrieval.embedding_cache import embed_cached
from app.modules.retrieval.rewrite import normalize_query
from app.modules.retrieval.service import answerable, retrieve
from app.modules.retrieval.types import RetrievalResult
from app.providers import get_llm_provider
from app.providers.llm.base import LLMError

log = get_logger("generation")

NO_ANSWER_TEXT = "现有资料未涵盖该问题，建议换个说法或补充相关文档"


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _prepare(principal: Principal, payload) -> tuple[uuid.UUID, RetrievalResult, list[dict]]:
    """准备会话上下文与检索结果。

    刻意拆成两个事务：向量推理是 CPU 密集操作（本地 bge-m3 单条 1~2 秒），
    如果放在事务里，一个问答就占着连接池的一条连接直到推理结束。
    连接池只有 15 条，十几个并发问答就会开始排队。
    """
    async with session_scope(tenant_id=principal.tenant_id) as session:
        readable = set(await filter_readable_kbs(session, principal))
        effective = [kb_id for kb_id in payload.kb_ids if kb_id in readable]
        if not effective:
            raise PermissionDeniedError("没有可访问的知识库")

        # 配额拦截在 router 层做（要返回真正的 429），这里只负责计数
        await record_usage(session, principal.tenant_id, "queries_per_month", 1)

        conversation = await conversation_service.get_or_create(
            session, principal, payload.conversation_id, effective
        )
        history: list[dict] = []
        if payload.conversation_id is not None:
            previous = await conversation_service.get_messages(session, principal, conversation.id)
            history = [
                {"role": item.role, "content": item.content}
                for item in previous
                if item.role in {"user", "assistant"}
            ]

        await conversation_service.add_message(
            session,
            tenant_id=principal.tenant_id,
            conversation_id=conversation.id,
            role="user",
            content=payload.query,
        )
        await conversation_service.maybe_set_title(session, conversation, payload.query)
        conversation_id = conversation.id

    # 事务外做向量计算；离线模式走词法召回，不产生查询向量
    vector = (
        None
        if settings.embedding_provider == "fake"
        else await embed_cached(normalize_query(payload.query))
    )

    async with session_scope(tenant_id=principal.tenant_id) as session:
        result = await retrieve(session, principal, effective, payload.query, vector=vector)

    return conversation_id, result, history


async def _persist_assistant(
    principal: Principal,
    conversation_id: uuid.UUID,
    content: str,
    *,
    status: str,
    token_in: int,
    token_out: int,
    latency_ms: int,
    citations: list[dict] | None = None,
) -> uuid.UUID:
    async with session_scope(tenant_id=principal.tenant_id) as session:
        message = await conversation_service.add_message(
            session,
            tenant_id=principal.tenant_id,
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            status=status,
            token_in=token_in,
            token_out=token_out,
            latency_ms=latency_ms,
            trace_id=get_trace_id(),
        )
        if citations:
            await conversation_service.add_citations(
                session,
                tenant_id=principal.tenant_id,
                message_id=message.id,
                citations=citations,
            )
        if token_in or token_out:
            await record_usage(session, principal.tenant_id, "llm_token_in", token_in)
            await record_usage(session, principal.tenant_id, "llm_token_out", token_out)
        return message.id


async def stream_answer(principal: Principal, payload) -> AsyncIterator[str]:
    started = time.perf_counter()

    try:
        conversation_id, result, history = await _prepare(principal, payload)
    except AppError as exc:
        yield sse("error", {"code": exc.code, "message": exc.message})
        return

    blocks = result.context.blocks
    # 稠密与稀疏两路都算数：只看稠密会把「字面命中但语义分低」的查询误拒
    below_threshold = not answerable(result.top_dense_score, result.top_sparse_score)

    if not blocks or below_threshold:
        message_id = await _persist_assistant(
            principal,
            conversation_id,
            NO_ANSWER_TEXT,
            status="no_answer",
            token_in=0,
            token_out=0,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        CHAT_OUTCOME.labels(outcome="no_answer").inc()
        yield sse("no_answer", {"message": NO_ANSWER_TEXT})
        yield sse("done", {"message_id": str(message_id), "used_citations": []})
        return

    yield sse(
        "citations",
        {
            "citations": [
                {
                    "index": block.index,
                    "document_id": str(block.document_id),
                    "chunk_id": str(block.chunk_id) if block.chunk_id else None,
                    "title": block.title,
                    "heading_path": block.heading_path,
                    "page_from": block.page_from,
                }
                for block in blocks
            ]
        },
    )

    messages = build_messages(payload.query, blocks, history)
    token_in = prompt_tokens(messages)
    parts: list[str] = []

    try:
        provider = get_llm_provider()
        # 用生产者 + 队列的方式消费上游流，而不是直接 async for：
        # 这样才能在"上游迟迟不吐 token"的空闲窗口里插入心跳帧，
        # 否则 nginx / 企业代理会按空闲超时切断连接，用户看到回答突然消失。
        queue: asyncio.Queue = asyncio.Queue()

        async def _produce() -> None:
            try:
                async for delta in provider.stream(messages):
                    await queue.put(("delta", delta))
                await queue.put(("end", None))
            except LLMError as exc:
                await queue.put(("error", exc))
            except Exception:
                await queue.put(("error", LLMError("生成失败")))

        task = asyncio.create_task(_produce())
        try:
            while True:
                try:
                    kind, payload = await asyncio.wait_for(
                        queue.get(), timeout=settings.sse_keepalive_seconds
                    )
                except TimeoutError:
                    yield ": ping" + chr(10) + chr(10)
                    continue

                if kind == "end":
                    break
                if kind == "error":
                    raise payload
                if payload.text:
                    parts.append(payload.text)
                    yield sse("token", {"t": payload.text})
        finally:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
    except (asyncio.CancelledError, GeneratorExit):
        # 用户点「停止」/ 浏览器断开：把已生成的部分落库，否则刷新后这段回答凭空消失，
        # 但 token 已经花了。shield 是为了在取消风暴中仍给持久化一次机会。
        partial = "".join(parts)
        with contextlib.suppress(Exception):
            await asyncio.shield(
                _persist_assistant(
                    principal,
                    conversation_id,
                    partial,
                    status="incomplete",
                    token_in=token_in,
                    token_out=count_tokens(partial),
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            )
        CHAT_OUTCOME.labels(outcome="incomplete").inc()
        raise

    except LLMError as exc:
        # 保留已生成内容，标记为不完整，便于前端"生成中断 + 重试"
        partial = "".join(parts)
        latency = int((time.perf_counter() - started) * 1000)
        message_id = await _persist_assistant(
            principal,
            conversation_id,
            partial,
            status="incomplete",
            token_in=token_in,
            token_out=count_tokens(partial),
            latency_ms=latency,
        )
        yield sse(
            "error",
            {
                "code": "LLM_ERROR",
                "message": exc.args[0] if exc.args else "生成失败",
                "message_id": str(message_id),
            },
        )
        return

    answer = "".join(parts)
    citation_result = extract_citations(answer, blocks)
    latency = int((time.perf_counter() - started) * 1000)

    message_id = await _persist_assistant(
        principal,
        conversation_id,
        answer,
        status="normal",
        token_in=token_in,
        token_out=count_tokens(answer),
        latency_ms=latency,
        citations=[
            {
                "document_id": citation.document_id,
                "chunk_id": citation.chunk_id,
                "title": citation.title,
                "heading_path": citation.heading_path,
                "page_from": citation.page_from,
            }
            for citation in citation_result.citations
        ],
    )

    CHAT_OUTCOME.labels(outcome="normal").inc()
    yield sse(
        "done",
        {
            "message_id": str(message_id),
            "conversation_id": str(conversation_id),
            "used_citations": citation_result.used,
        },
    )
    log.info(
        "answer_done",
        citations=len(citation_result.used),
        latency_ms=latency,
        tokens_out=count_tokens(answer),
    )
