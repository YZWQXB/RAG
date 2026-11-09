# Phase 4: 检索

**Goal:** 实现查询归一化、dense + trigram 双路召回、RRF 融合、重排与上下文组装，并提供内部检索调试接口供调参。

**Prerequisites:** Phase 3 完成（`chunks` 表有数据，Embedding Provider 可用）

**Deliverables:**
- `POST /api/v1/debug/retrieve` 返回每一阶段的中间结果（改写后 query、两路候选、融合后序、重排分数、最终上下文）
- 检索服务可被 Phase 5 直接调用：`retrieve(session, principal, kb_ids, query) -> RetrievalResult`
- 单测覆盖 RRF 数学正确性与租户过滤

**Interface Decisions Implemented:** 无（本阶段无界面；调试接口仅供开发与运营使用）

---

## Task 1: 查询归一化与向量缓存

**Goal:** 统一 query 形态，避免同一问题重复计费。

**Files:**
- Create: `app/modules/retrieval/rewrite.py`
- Create: `app/modules/retrieval/embedding_cache.py`
- Test: `tests/unit/test_rewrite.py`

**Steps:**

- [ ] 实现 `normalize_query(q: str) -> str`：全角转半角、连续空白压为单空格、去首尾语气词（"请问"、"帮我看看"、"我想知道"）、去首尾标点重复、长度上限 512 字符（超出截断并记 warning）。
- [ ] **不做** LLM 改写，也不做多查询扩展。原因：每次提问多一次 LLM 调用会显著抬高成本与延迟，且首期缺乏评测数据支撑收益。留 `settings.enable_llm_rewrite = False` 开关，Phase 6 评测后若有明确收益再打开。
- [ ] 实现 `embed_query_cached(text) -> list[float]`：Redis key `emb:{model}:{sha256(text)}`，命中直接返回，未命中调 provider 后 `SETEX 86400`。Redis 不可用时降级为直接调用（不阻断检索），记 warning。
- [ ] 测试：`normalize_query` 的 6 个输入输出用例；缓存命中时 provider 只被调用 1 次（用计数器 stub）。

**Security/Boundary Notes:** 缓存 key 含模型名，换模型不会命中旧向量。缓存值为向量本身，不含原文，避免在 Redis 中留存用户问题文本。

---

## Task 2: Dense 召回

**Goal:** 用 pgvector 余弦距离召回 top 40，租户与 KB 过滤在 SQL 内完成。

**Files:**
- Create: `app/modules/retrieval/types.py`
- Create: `app/modules/retrieval/dense.py`
- Test: `tests/integration/test_dense_recall.py`

**Steps:**

- [ ] 先建共享类型 `app/modules/retrieval/types.py`（后续所有模块只引用这里，不各自定义）：

```python
@dataclass
class Candidate:
    id: UUID
    document_id: UUID
    kb_id: UUID
    content: str
    heading_path: str | None
    page_from: int
    page_to: int
    chunk_index: int
    score: float = 0.0          # 单路原始分
    fused_score: float = 0.0    # RRF 分
    rerank_score: float = 0.0   # 重排分

@dataclass
class RetrievalResult:
    normalized_query: str
    candidates: list[Candidate]
    context: "ContextResult"
    timings_ms: dict[str, int]
```

- [ ] 实现：

```python
async def dense_recall(session, tenant_id, kb_ids, vec, top_k=40) -> list[Candidate]:
    await session.execute(text("SET LOCAL hnsw.ef_search = 100"))
    dist = Chunk.embedding.cosine_distance(vec)
    stmt = (select(Chunk.id, Chunk.document_id, Chunk.kb_id, Chunk.content,
                   Chunk.heading_path, Chunk.page_from, Chunk.page_to, Chunk.chunk_index,
                   (1 - dist).label("score"))
            .where(Chunk.tenant_id == tenant_id, Chunk.kb_id.in_(kb_ids))
            .order_by(dist).limit(top_k))
    ...
```

- [ ] 测试用**构造向量**而非真实语义（避免测试依赖模型质量）：`basis(i)` 返回第 i 维为 1 的 1024 维单位向量；写入 3 个块分别用 `basis(0)/basis(1)/basis(2)`，查询用 `basis(0)`，断言召回首位是该块且 `score ≈ 1.0`。
- [ ] 写隔离用例：租户 A 与 B 各写一个 `basis(0)` 块，A 查询时断言结果只含 A 的块（这条重复覆盖 Phase 1 的向量隔离要求，但在此处以真实检索路径验证）。
- [ ] 运行：`uv run pytest tests/integration/test_dense_recall.py -v`
  - Expected：2 条通过，`score` 误差 < 1e-6。

**Security/Boundary Notes:** `kb_ids` 必须来自 `filter_readable_kbs` 的结果，不能直接使用请求参数；服务层入口处若发现请求中的 `kb_id` 不在可读集合内，直接剔除并记审计（不报错，静默过滤，避免探测）。

---

## Task 3: 稀疏召回（trigram）

**Goal:** 用 `pg_trgm` 召回编号、专有名词类命中的块。

**Files:**
- Create: `app/modules/retrieval/sparse.py`
- Test: `tests/integration/test_sparse_recall.py`

**Steps:**

- [ ] 实现：

```python
async def sparse_recall(session, tenant_id, kb_ids, query, top_k=20) -> list[Candidate]:
    await session.execute(text("SET LOCAL pg_trgm.similarity_threshold = 0.1"))
    sim = func.similarity(Chunk.content, query)
    stmt = (select(..., sim.label("score"))
            .where(Chunk.tenant_id == tenant_id, Chunk.kb_id.in_(kb_ids),
                   Chunk.content.op("%")(query))
            .order_by(sim.desc()).limit(top_k))
```

`similarity_threshold` 降到 0.1 的原因写进注释：中文四字以上的短语在 trigram 下相似度天然偏低，默认 0.3 会过滤掉全部结果。

- [ ] 边界处理：`len(query) < 2` 直接返回空列表（trigram 对单字无效）；查询结果为空时不报错，由融合阶段自然退化为 dense-only。
- [ ] 测试：写入含 "SKU-8823 的退换货政策" 的块与一段无关文本，查询 "SKU-8823" 断言命中前者；查询 "退换" 断言至少 1 条；查询 "a" 断言返回空且不抛错。

**Security/Boundary Notes:** `query` 作为绑定参数传入，禁止字符串拼接进 SQL（trigram 查询是注入面）。

---

## Task 4: RRF 融合

**Goal:** 合并两路候选，输出统一排序，不依赖分数归一化。

**Files:**
- Create: `app/modules/retrieval/fusion.py`
- Test: `tests/unit/test_rrf.py`

**Steps:**

- [ ] 实现：

```python
def rrf_fuse(lists: list[list[Candidate]], k: int = 60, top_n: int = 20) -> list[Candidate]:
    scores: dict[UUID, float] = {}
    best: dict[UUID, Candidate] = {}
    for lst in lists:
        for rank, c in enumerate(lst, start=1):
            scores[c.id] = scores.get(c.id, 0.0) + 1.0 / (k + rank)
            best.setdefault(c.id, c)
    ordered = sorted(scores, key=lambda i: scores[i], reverse=True)[:top_n]
    return [replace(best[i], fused_score=scores[i]) for i in ordered]
```

- [ ] 单测：
  - 同一 id 在两路都排第 1 → 分数 `1/61 + 1/61`
  - 只在 dense 排第 1、sparse 为空 → 分数 `1/61`
  - 两路各 5 条有 2 条重叠 → 输出 8 条，重叠项排在前
  - `top_n` 截断生效
- [ ] 运行：`uv run pytest tests/unit/test_rrf.py -v`
  - Expected：4 条通过。

**Security/Boundary Notes:** 融合前对候选按 `id` 去重，避免同一块在两路重复计分导致虚高。

---

## Task 5: 重排

**Goal:** 用 cross-encoder 对融合结果精排，并在低收益场景跳过以控制延迟。

**Files:**
- Create: `app/providers/reranker/base.py`
- Create: `app/providers/reranker/api_bge.py`
- Create: `app/modules/retrieval/rerank.py`
- Test: `tests/unit/test_rerank_policy.py`

**Steps:**

- [ ] 接口 `RerankerProvider.rerank(query, documents: list[str]) -> list[float]`；实现 `ApiBgeReranker`：`POST {base_url}/rerank`，body `{"model": ..., "query": ..., "documents": [...], "top_n": n}`，超时 10s，**失败时降级为不重排并记 warning**（重排是质量增强，不应成为可用性单点）。
- [ ] 实现 `should_rerank(query, candidates) -> bool`，跳过条件（任一满足）：
  - `len(candidates) <= 3`
  - `len(query) <= 12` 且 `candidates[0].score >= 0.6`（简单事实型，dense 已足够自信）
- [ ] 实现 `rerank_top(candidates, query, top_n=6) -> list[Candidate]`，对 `content` 前 512 字符参与重排（控制输入长度）。
- [ ] 单测：上述两个跳过条件各一条；`len(query)=30` 且首条 score=0.4 时不跳过；provider 抛异常时返回原序且不抛错。

**Security/Boundary Notes:** 传给重排服务的内容是用户文档片段，属敏感数据；provider 必须配置为可信服务，且请求体不落日志。

---

## Task 6: 上下文组装与检索调试接口

**Goal:** 把重排结果压成可注入 Prompt 的上下文，并暴露全链路中间结果。

**Files:**
- Create: `app/modules/retrieval/context.py`
- Create: `app/modules/retrieval/service.py`
- Create: `app/modules/retrieval/schemas.py`
- Create: `app/modules/retrieval/router.py`
- Modify: `app/api/v1/__init__.py`
- Test: `tests/unit/test_context.py`

**Steps:**

- [ ] 实现 `assemble_context(candidates, budget_tokens=3000) -> ContextResult`：

1. 按 `(document_id, chunk_index)` 排序，**相邻块（index 差 1）合并**为一段，保留最小 `page_from` 与最大 `page_to`
2. 按 `rerank_score`（未重排时用 `fused_score`）降序排序，**降序第 1 条即引用编号 `[1]`**
3. 按上述顺序逐个装填，累计 token 超预算即停止（不截断单块内容）
4. 返回 `blocks: list[ContextBlock{index, document_id, title, heading_path, page_from, page_to, content}]` 与 `total_tokens`，`index` 从 1 连续递增且与上下文中的排列顺序一致

- [ ] 实现 `retrieve(session, principal, kb_ids, query) -> RetrievalResult`，串联：归一化 → 缓存向量 → 双路并行（`asyncio.gather`）→ RRF → 重排 → 组装。返回结构含每阶段计数与耗时，供调试与观测使用。
- [ ] 实现 `POST /api/v1/debug/retrieve`，body `{kb_ids, query, top_k?, rerank?}`，要求登录（任意角色，仅限自己的可读 KB），返回：

```json
{
  "normalized_query": "...",
  "dense": [{"chunk_id": "...", "score": 0.83, "preview": "..."}],
  "sparse": [...],
  "fused": [...],
  "reranked": [...],
  "context": {"blocks": [...], "total_tokens": 2480},
  "timings_ms": {"embed": 42, "dense": 18, "sparse": 31, "rerank": 180, "total": 271}
}
```

`preview` 截前 120 字，**不返回完整块内容**（调试接口不应成为批量导出文档的通道）。
- [ ] 单测：相邻块合并后块数减少且 `page_from/page_to` 取并集；预算 100 tokens 时只装入首块；`index` 从 1 连续递增。
- [ ] 手工验证：先跑 Phase 3 流程造 3 份文档，调调试接口观察两路召回差异（这是后续调分块与阈值的唯一依据）。

**Security/Boundary Notes:** 调试接口必须走完整权限校验（`filter_readable_kbs`），不得因为"内部使用"而跳过；生产环境通过 `settings.enable_debug_api` 控制是否挂载，默认在 `ENV=production` 时关闭。

---

## Phase Completion Checklist

- [ ] 所有任务完成
- [ ] RRF 数学正确性 4 条单测通过
- [ ] dense/sparse 均存在租户过滤测试
- [ ] 调试接口返回完整 6 段中间结果
- [ ] 单次检索（不含 LLM）本地 P95 < 300ms（用调试接口 `timings_ms.total` 观察 20 次）
- [ ] 重排服务不可用时检索仍可用（手工断开 API key 验证降级）

## 执行结果

**Status:** Done

- 已实现：查询归一化、向量缓存（key 含 provider 类型）、dense 召回、trigram 召回、RRF、重排与降级、上下文组装、`/api/v1/debug/retrieve`
- 验证：RRF 数学正确性 4 条；dense/sparse 租户隔离；调试接口返回 6 段中间结果
- 偏差：两路召回改为**顺序**执行——`AsyncSession` 非并发安全，同会话并发查询不安全；本地延迟 <100ms，收益不足以开多会话
- 偏差：LLM 查询改写默认关闭（`enable_llm_rewrite=false`），避免每次提问多一次模型调用
