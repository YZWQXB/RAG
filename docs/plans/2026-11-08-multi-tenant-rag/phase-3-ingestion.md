# Phase 3: 文档上传与入库流水线

**Goal:** 上传 PDF/Word/Markdown/TXT 后，经 outbox 驱动独立 worker 完成解析、分块、向量化，文档状态推进到 `ready`，且重传同一文件不产生重复数据。

**Prerequisites:** Phase 2 完成（KB 与权限判定可用）

**Deliverables:**
- 上传接口返回 202 与 `document_id`，可按 id 轮询状态与阶段
- worker 独立进程，可 `uv run python -m worker.main` 启动
- `documents.status` 与 `documents.stage` 正确推进，失败可重试
- 金标准测试：Markdown 与 PDF 的分块数量、`heading_path`、页码断言通过

**Interface Decisions Implemented:**
- 文档状态三阶段文案（解析中/切分中/向量化中）→ 后端暴露 `stage` 字段（`parsing` / `chunking` / `embedding`），供 Phase 7 轮询渲染
- 上传失败态 → `400 UNSUPPORTED_TYPE` / `400 FILE_TOO_LARGE` / `failed` + `error` 原因

---

## Task 1: 文档、版本、块与任务表

**Goal:** 建出 `documents`、`document_versions`、`chunks`、`ingestion_jobs`、`outbox_events` 五表及索引。

**Files:**
- Create: `app/modules/ingestion/models.py`
- Create: `migrations/versions/0003_documents_chunks.py`

**Steps:**

- [ ] 定义模型。`chunks` 使用 `pgvector.sqlalchemy.Vector(settings.embedding_dim)`：

```python
class Document(Base):
    __tablename__ = "documents"
    id, tenant_id, kb_id(FK, index), title(String(255)), mime(String(128))
    size_bytes(BigInteger), content_hash(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")   # pending|processing|ready|failed
    stage: Mapped[str | None] = mapped_column(String(16))                # parsing|chunking|embedding
    error: Mapped[str | None] = mapped_column(Text)
    current_version_id, created_by, created_at, updated_at, deleted_at
    __table_args__ = (
        Index("idx_documents_scope", "tenant_id", "kb_id", "status"),
        Index("uq_doc_hash", "kb_id", "content_hash", unique=True,
              postgresql_where=text("deleted_at IS NULL")),
    )

class DocumentVersion(Base):
    __tablename__ = "document_versions"
    id, tenant_id, document_id(FK, index), version_no(Integer)
    storage_key(String(512)), content_hash(String(64)), status(String(16)), created_at
    __table_args__ = (UniqueConstraint("document_id", "version_no"),)

class Chunk(Base):
    __tablename__ = "chunks"
    id, tenant_id, kb_id, document_id(FK, index), version_id(FK, index)
    chunk_index(Integer), content(Text), token_count(Integer)
    heading_path(String(512), nullable), page_from(Integer), page_to(Integer)
    content_hash(String(64)), embedding: Mapped[list[float]] = mapped_column(Vector(1024))
    created_at
    __table_args__ = (
        UniqueConstraint("document_id", "version_id", "chunk_index", name="uq_chunks_pos"),
        Index("idx_chunks_tenant_kb", "tenant_id", "kb_id", "document_id"),
    )

class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    id, tenant_id, document_id, version_id, stage(String(16)), status(String(16))
    attempts(Integer, default=0), last_error(Text), available_at, started_at, finished_at

class OutboxEvent(Base):
    __tablename__ = "outbox_events"   # 无 tenant_id，跨租户调度表
    id, aggregate_type(String(32)), aggregate_id(UUID), event_type(String(64))
    payload(JSONB), status(String(16), default="pending"), attempts(Integer, default=0)
    available_at(DateTime), created_at
    __table_args__ = (Index("idx_outbox_pick", "status", "available_at"),)
```

- [ ] 迁移中除建表外，追加 HNSW 与 trigram 索引，并给四张租户表启用 `ENABLE`/`FORCE RLS` + 策略（`outbox_events` 不启用）：

```python
op.execute("CREATE INDEX idx_chunks_hnsw ON chunks USING hnsw (embedding vector_cosine_ops) "
           "WITH (m = 16, ef_construction = 64)")
op.execute("CREATE INDEX idx_chunks_trgm ON chunks USING gin (content gin_trgm_ops)")
```

- [ ] 运行：`uv run alembic upgrade head`
  - Expected：`\di idx_chunks_hnsw` 存在；`\d chunks` 显示 `embedding vector(1024)`。

**Security/Boundary Notes:** `outbox_events` 刻意不含 `tenant_id`，它只存 `aggregate_id`，worker 处理时从业务表反查归属，避免调度表成为越权入口。

---

## Task 2: 对象存储 Provider

**Goal:** 提供统一的 S3 存储接口，支持上传、下载、删除与短时签名 URL。

**Files:**
- Create: `app/providers/storage/base.py`
- Create: `app/providers/storage/s3.py`
- Create: `scripts/init_bucket.py`
- Test: `tests/integration/test_storage.py`

**Steps:**

- [ ] 确认 `scripts/__init__.py` 已存在（Phase 1 Task 5 创建），保证 `python -m scripts.xxx` 可用。

- [ ] 定义接口：

```python
class StorageProvider(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...
    async def presign_get(self, key: str, expires: int = 300) -> str: ...
```

- [ ] 实现 `S3StorageProvider`：`boto3.client` 同步调用统一包 `asyncio.to_thread`，`endpoint_url=settings.s3_endpoint`，`config=Config(signature_version="s3v4")`，私有桶。
- [ ] 提供 key 构造单点函数（唯一允许拼接路径的地方）：

```python
def doc_key(tenant_id: UUID, kb_id: UUID, doc_id: UUID, version: int, filename: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[-80:]
    return f"tenant/{tenant_id}/kb/{kb_id}/{doc_id}/v{version}/{safe}"
```

- [ ] 创建 `scripts/init_bucket.py`：建桶（不存在则建），设置私有 ACL，拒绝公开读。运行：`uv run python -m scripts.init_bucket`
  - Expected：MinIO 控制台可见 `rag-docs` 桶。
- [ ] 测试 `tests/integration/test_storage.py`：put → get 内容一致 → presign URL 含 `X-Amz-Signature` → delete 后 get 抛 `NoSuchKey`。

**Security/Boundary Notes:** 文件名不参与路径安全边界（已正则清洗）；签名有效期默认 300 秒，禁止在响应中返回永久 URL。

---

## Task 3: 上传接口与 outbox 写入

**Goal:** 接收文件，校验类型与大小，去重，落库并在同一事务写入 outbox 事件。

**Files:**
- Create: `app/modules/ingestion/schemas.py`
- Create: `app/modules/ingestion/service.py`
- Create: `app/modules/ingestion/repository.py`
- Create: `app/modules/ingestion/router.py`
- Modify: `app/api/v1/__init__.py`
- Test: `tests/integration/test_upload.py`

**Steps:**

- [ ] 定义常量与校验：`ALLOWED = {".pdf": "application/pdf", ".docx": ..., ".md": "text/markdown", ".txt": "text/plain"}`；单文件上限 `50 * 1024 * 1024`。超限 `400 FILE_TOO_LARGE`（"单个文件不能超过 50MB"），类型不符 `400 UNSUPPORTED_TYPE`（"仅支持 PDF、Word、Markdown、TXT"）。空文件 `400 EMPTY_FILE`。
- [ ] `POST /api/v1/kbs/{kb_id}/documents` 流程（严格顺序）：

```python
# 1) 权限：WRITE
await require_kb_permission(session, principal, kb_id, Action.WRITE)
# 2) 配额钩子（Phase 6 实现，此处为 no-op 函数）
await quota_guard(session, principal.tenant_id, "document", 1)
# 3) 读字节 + 校验 + 计算 hash
raw = await file.read()
digest = hashlib.sha256(raw).hexdigest()
# 4) 去重：同 kb 同 hash 且未删除 → 直接返回已有文档，duplicated=True
# 5) 先分配 doc_id 与 version=1，落库（documents + document_versions + outbox_events 同事务）
# 6) 事务提交后再上传对象存储（避免长事务占连接）
# 7) 返回 202 {document_id, status: "pending", duplicated}
```

**顺序不可调换**：先把字节存到对象存储再落库，一旦落库失败会留下孤儿对象；先落库再上传，失败时文档停在 `pending`，由 worker 的重试与超时清理兜底（可用性优先）。

- [ ] `GET /api/v1/documents/{doc_id}` 返回 `{id, title, status, stage, error, chunk_count, page_count, created_at}`，`stage` 在 `status=processing` 时非空。
- [ ] `GET /api/v1/kbs/{kb_id}/documents?status=&page=&size=` 分页列表，默认按 `created_at desc`。
- [ ] `POST /api/v1/documents/{doc_id}/retry` 重置 `attempts=0`、`status=pending`、写新 outbox 事件，要求 WRITE 权限。
- [ ] `DELETE /api/v1/documents/{doc_id}` 软删除文档并写 outbox `doc.delete`（异步删 chunk 与对象）。
- [ ] 测试：上传 `.md` 返回 202；重复上传同文件返回 `duplicated=true` 且 `document_id` 相同；上传 `.exe` 返回 400；上传 51MB 返回 400；直接查库断言 `outbox_events` 恰有 1 行 `status=pending`。

**Security/Boundary Notes:** 文件名只用于展示与对象 key 末尾段，不参与任何路径解析。`document_id` 由服务端生成，不接受客户端指定。

---

## Task 4: 解析器

**Goal:** 四类格式统一解析为带页码与标题层级的块序列。

**Files:**
- Create: `app/modules/ingestion/pipeline/parse.py`
- Test: `tests/unit/test_parse.py`
- Create: `tests/fixtures/sample.md`

**Steps:**

- [ ] 定义中间结构：

```python
@dataclass(frozen=True)
class ParsedBlock:
    text: str
    page_from: int
    page_to: int
    heading_level: int      # 0 = 正文
    heading_path: str       # "员工手册 > 3 假期 > 3.2 年假"
    is_table: bool = False
    is_code: bool = False

@dataclass(frozen=True)
class ParsedDocument:
    blocks: list[ParsedBlock]
    page_count: int
```

- [ ] 实现 `parse_pdf(raw: bytes) -> ParsedDocument`：用 `fitz.open(stream=raw, filetype="pdf")`；逐页 `page.get_text("dict")`，按 `line["spans"]` 取字号，**以全文字号中位数为正文基准，字号 ≥ 基准 × 1.15 判为标题**，按字号聚类映射到 1–3 级；每块记录所属页；跨页段落按上下页首尾连接。
- [ ] 实现 `parse_docx`：`python-docx` 遍历 body，`paragraph.style.name` 匹配 `Heading (\d)` 取层级；表格转为 `| a | b |` 文本并置 `is_table=True`。
- [ ] 实现 `parse_markdown`：`markdown_it.MarkdownIt().parse(text)`，遍历 token，`heading_open` 维护层级栈，`fence`/`code_block` 置 `is_code=True`，`table_open` 段置 `is_table=True`。
- [ ] 实现 `parse_txt`：整篇为一个正文块，`page_from=page_to=1`。
- [ ] 标题栈维护统一在 `_HeadingTracker` 内：进入某级标题时截断栈到该级再压入，输出 `" > ".join(stack)`。
- [ ] 测试断言：
  - `sample.md` 解析出 3 个标题块，`heading_path` 为 `员工手册 > 3 假期 > 3.2 年假`
  - 生成的 2 页 PDF 中第 2 页文本块的 `page_from == 2`
  - 含表格的 docx 解析出 `is_table=True` 的块

**Security/Boundary Notes:** 解析在 worker 进程内进行，不执行文档内任何宏或脚本。对加密 PDF（`doc.needs_pass`）直接抛 `ParseError("文档已加密，无法解析")`。

---

## Task 5: 分块器

**Goal:** 按标题层级与长度双约束切块，保留上下文头，保证同一输入产出稳定分块。

**Files:**
- Create: `app/modules/ingestion/pipeline/chunk.py`
- Test: `tests/unit/test_chunk.py`

**Steps:**

- [ ] 实现 `chunk_document(parsed, title, config) -> list[ChunkDraft]`，`ChunkDraft{content, chunk_index, heading_path, page_from, page_to, token_count, content_hash}`。

算法（严格按此实现，保证可复现）：

1. 按 `heading_path` 分组，同组内串联块文本（表格与代码块不参与串联，单独成块）
2. 组内累加到 `chunk_size_chars`（默认 600）即切一刀，**切点优先选在 `。！？\n\n` 之后**，找不到才硬切
3. 相邻块保留 `overlap_chars`（默认 90）重叠，重叠内容取自上一块尾部
4. `is_table` / `is_code` 块独立成块，上限 `max_chars`（默认 1200），超限则按行切分并各自重复表头/语言标记
5. 若 `prepend_context_header`，每块正文前置 `[文档: {title} | 章节: {heading_path}]`
6. `token_count` 用 `tiktoken.get_encoding("cl100k_base")` 计算
7. `content_hash = sha256(规范化正文)`，规范化 = 去首尾空白 + 连续空白压为单空格

- [ ] 说明与设计文档的换算关系（写进模块 docstring，避免后人误改）：设计文档的"400–600 tokens"按中文约 1.5 字/token 换算为 600–900 字，本实现取 `chunk_size_chars=600`（约 400 token）作为**保守下界**，宁可块偏小以保证引用定位精度；调参通过 `chunk_config` 覆盖，不改代码。
- [ ] 测试断言：
  - 同一输入运行两次，`content_hash` 序列完全一致（可复现）
  - 单个 chunk 字符数 ≤ `chunk_size_chars + overlap_chars`
  - 表格块不被切断，`is_table` 块字符数 ≤ 1200
  - 每块正文以 `[文档:` 开头（开启上下文头时）
  - `heading_path` 与解析结果一致

**Security/Boundary Notes:** 分块不改变原文语义顺序，禁止为凑长度重排内容（会破坏引用可信度）。

---

## Task 6: 向量化与块写入

**Goal:** 批量调用 Embedding API 并把块与向量幂等写入 `chunks`。

**Files:**
- Create: `app/providers/embedding/base.py`
- Create: `app/providers/embedding/api_bge.py`
- Create: `app/providers/embedding/fake.py`
- Create: `app/modules/ingestion/pipeline/embed.py`
- Test: `tests/unit/test_embed_writer.py`

**Steps:**

- [ ] 定义接口 `EmbeddingProvider.embed(texts: list[str]) -> list[list[float]]`，实现 `ApiBgeEmbedding`：`POST {base_url}/embeddings`，body `{"model": ..., "input": texts}`，解析 `data[*].embedding`；超时 30s，失败按 `1s/4s/16s` 重试 3 次，仍失败抛 `EmbeddingError`。
- [ ] 实现 `FakeEmbeddingProvider`：对每段文本 `sha256` 取种子生成 1024 维向量并 L2 归一化。**确定性**，保证测试可重复；同时保证语义无关（不用于质量评测，仅用于链路测试）。
- [ ] 实现批量写入：

```python
async def write_chunks(session, tenant_id, kb_id, doc_id, version_id, drafts, vectors):
    stmt = pg_insert(Chunk).values([...])
    stmt = stmt.on_conflict_do_update(
        constraint="uq_chunks_pos",
        set_={"content": stmt.excluded.content, "embedding": stmt.excluded.embedding,
              "content_hash": stmt.excluded.content_hash, "page_from": stmt.excluded.page_from},
    )
    await session.execute(stmt)
    # 清理上次残留的尾部块（重传后块数变少的情形）
    await session.execute(delete(Chunk).where(
        Chunk.document_id == doc_id, Chunk.version_id == version_id,
        Chunk.chunk_index >= len(drafts)))
```

- [ ] 批次大小 32，逐批 `embed` → 逐批 `write_chunks`；任一批失败则整版重跑（幂等保证不产生重复）。
- [ ] 测试：写入 5 块 → 再写入同样 5 块 → `count(*) == 5`；先写 5 块再写 3 块 → `count(*) == 3`（尾部清理生效）；向量维度断言 1024。

**Security/Boundary Notes:** Embedding API 的 key 不落日志；`FakeEmbeddingProvider` 仅用于测试环境，通过 `settings.embedding_provider` 切换，生产环境若误配为 fake 必须在启动时拒绝（`ENV != local` 且 provider 为 fake → 启动报错）。

---

## Task 7: Outbox 轮询、状态机与 worker 进程

**Goal:** worker 独立进程拉起轮询器，驱动文档状态机，失败按退避重试。

**Files:**
- Create: `worker/main.py`
- Create: `worker/tasks/outbox.py`
- Create: `worker/tasks/aggregate.py`
- Create: `app/modules/ingestion/pipeline/runner.py`
- Test: `tests/integration/test_ingest_flow.py`

**Steps:**

- [ ] 实现 `run_ingestion(doc_id, version_id)` 状态机：

```
pending → (stage=parsing)   parse    → 成功 → (stage=chunking)
        → (stage=chunking)  chunk    → 成功 → (stage=embedding)
        → (stage=embedding) embed    → 成功 → status=ready, stage=NULL
任一步失败 → attempts+=1；attempts<3 → available_at=now+backoff, status=pending
           attempts>=3 → status=failed, error=用户可读中文原因
```

每次阶段切换都要 `flush` 并提交，保证前端轮询能看到中间态（这一点是"必须显示阶段"需求的实现前提）。

- [ ] 实现 outbox 轮询器。**跨租户调度必须用 admin_engine**，这一点在文件头注释说明原因并标注为 RLS 的唯一合法逃生口：

```python
async def poll_outbox(stop: asyncio.Event) -> None:
    while not stop.is_set():
        async with admin_engine.begin() as conn:
            rows = (await conn.execute(text(
                "SELECT id, aggregate_type, aggregate_id, event_type FROM outbox_events "
                "WHERE status='pending' AND available_at <= now() "
                "ORDER BY id LIMIT 10 FOR UPDATE SKIP LOCKED"
            ))).mappings().all()
            for r in rows:
                await conn.execute(text("UPDATE outbox_events SET status='processing' WHERE id=:i"),
                                   {"i": r["id"]})
        for r in rows:
            await _dispatch(r)      # 内部按 aggregate_id 反查 tenant_id，再走租户会话
        await asyncio.sleep(1)
```

- [ ] `_dispatch` 支持 `doc.ingest`、`doc.delete` 两类事件；处理成功标 `done`，异常标 `pending` 并按 `attempts` 退避。
- [ ] `worker/main.py`：`async def main()` 同时启动 `poll_outbox` 任务与 ARQ `Worker`（承载 `aggregate_usage`、`rescan_stuck` 两个定时任务），监听 `SIGTERM` 优雅退出（设置 stop event 并等待当前任务完成）。命令行入口 `uv run python -m worker.main`。
- [ ] 编写 `rescan_stuck`：把 `status='processing'` 且 `updated_at < now() - interval '10 minutes'` 的文档重置为 `pending`（进程崩溃后的自愈）。
- [ ] 集成测试 `tests/integration/test_ingest_flow.py`：

```python
async def test_upload_to_ready(client_a, kb_a, monkeypatch):
    monkeypatch.setattr("app.providers.embedding.get_provider", lambda: FakeEmbeddingProvider())
    doc = await upload_md(client_a, kb_a, "tests/fixtures/sample.md")
    await run_worker_once()                       # 直接调用 poll_outbox 一轮，不起进程
    detail = await client_a.get(f"/api/v1/documents/{doc['id']}")
    assert detail.json()["status"] == "ready"
    assert detail.json()["chunk_count"] == 3      # 金标准：sample.md 切成 3 块
    assert detail.json()["stage"] is None
```

- [ ] 补充失败路径测试：把 `FakeEmbeddingProvider` 换成必抛异常的 stub，跑 3 轮后断言 `status == "failed"` 且 `error` 为中文；调用 retry 接口后恢复正常并最终 `ready`。
- [ ] 运行：`uv run pytest tests/integration/test_ingest_flow.py -v`
  - Expected：2 条通过。

**Security/Boundary Notes:** `admin_engine` 仅允许出现在 `worker/tasks/outbox.py`、`migrations`、`scripts` 与测试 fixture 中，其他位置出现即评审阻断。轮询器每轮最多 10 条，避免单次占用连接过久。

---

## Phase Completion Checklist

- [ ] 所有任务完成
- [ ] `uv run pytest tests/unit tests/integration -v` 全绿
- [ ] 上传 → ready 端到端通过，`stage` 三阶段可观测
- [ ] 重复上传同文件不产生第二份文档与重复 chunk
- [ ] 失败重试 3 次后进入 `failed`，错误文案为中文
- [ ] `rescan_stuck` 可在进程崩溃后自愈（手工 kill worker 后重启验证）
- [ ] `alembic downgrade -1` 可回滚 0003

## 执行结果

**Status:** Done

- 已实现：本地/S3 双存储后端、上传与去重、outbox、worker 轮询、四格式解析、标题感知分块、批量向量化与幂等 upsert、状态机与退避重试
- 验证：上传→ready 端到端；重复上传去重；3 次失败后 `failed` 且文案中文；重试恢复；重跑幂等（chunk 数不翻倍）
- 偏差：存储后端增加 `LocalStorage`（默认），MinIO 在当前网络不可拉取，S3 后端保留并可在 `--profile s3` 下启用
- 关键修复：状态机每阶段 `commit` 后重新绑定租户，否则 RLS 会把后续 UPDATE 过滤成 0 行（StaleDataError）
