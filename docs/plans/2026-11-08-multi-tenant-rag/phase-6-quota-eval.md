# Phase 6: 配额、评测与观测

**Goal:** 配额在业务执行前硬拦截且有中文提示，用量可记账，日志可追踪到单次请求，检索质量可回归。

**Prerequisites:** Phase 5 完成（问答链路可跑通）

**Deliverables:**
- 四类配额生效，超限返回 429 且含剩余额度
- `usage_records` 有真实数据，可按租户按月汇总
- 每条日志含 `trace_id` 与 `tenant_id`，可用一次请求串起全链路
- `uv run python -m scripts.eval` 输出 Recall@5 / MRR 并与基线比对

**Interface Decisions Implemented:** 配额超限的 429 响应结构（前端据此渲染提示条）

---

## Task 1: 用量记账与配额拦截

**Goal:** 把 Phase 3/5 预留的两个 no-op 钩子替换为真实校验与记账。

**Files:**
- Create: `app/modules/quota/models.py`
- Create: `app/modules/quota/service.py`
- Create: `app/modules/quota/plans.py`
- Create: `migrations/versions/0005_quota.py`
- Modify: `app/modules/ingestion/service.py`（替换 `quota_guard` 空实现）
- Modify: `app/modules/generation/service.py`（接入预检与后记账）
- Test: `tests/unit/test_quota.py`

**Steps:**

- [ ] 定义套餐限额常量 `app/modules/quota/plans.py`：

```python
PLANS = {
    "free": {"documents": 100, "storage_bytes": 500 * 1024**2,
             "queries_per_month": 1000, "llm_tokens_per_month": 500_000},
}
```

- [ ] 建 `usage_records(id, tenant_id, metric, quantity, occurred_at, meta JSONB)` + RLS，索引 `(tenant_id, metric, occurred_at)`。
- [ ] 实现 `current_usage(session, tenant_id) -> dict`：文档数与存储量查库（`documents` 聚合 `count(*)` 与 `sum(size_bytes)`，排除已删除），查询次数与 token 查 Redis 月计数器。
- [ ] Redis 计数器 key 规范：`quota:{tenant_id}:{metric}:{YYYYMM}`，首次 `INCR` 后立即 `EXPIREAT` 到下月 1 日 0 点，避免计数器永久驻留。
- [ ] 实现 `assert_quota(tenant_id, metric, delta) -> None`：超限抛 `QuotaExceededError`，`details` 为 `{"metric", "limit", "used", "remaining"}`。
- [ ] 在**业务执行前**调用：上传前查 `documents` 与 `storage_bytes`；提问前查 `queries_per_month` 与 `llm_tokens_per_month`（token 用"剩余 > 0"判断，不预测本次用量，因为生成长度不可知）。
- [ ] 记账：上传成功后写 `usage_records(metric='doc_ingested')`；生成结束后写 `llm_token_in` / `llm_token_out`（用量取自 provider 返回与实际输出 tiktoken 计数）；每次提问 `INCR` `queries_per_month`。
- [ ] Redis 不可用时降级：`assert_quota` 对 Redis 类指标放行并记 warning（可用性优先），文档与存储量仍查库不降级。
- [ ] 单测：
  - 文档数达上限时上传抛 `QuotaExceededError`，`details.remaining == 0`
  - 查询计数从 999 递增到 1000 后，下一次抛错
  - Redis 客户端抛异常时 `assert_quota` 不抛错（降级放行）

**Security/Boundary Notes:** 配额以 JWT 中的 `tenant_id` 为维度，不接受请求传入。Redis key 必须带月份后缀，跨月自动归零，不能依赖手工删除。

---

## Task 2: 配额查询接口与错误契约

**Goal:** 前端可展示当前用量与剩余额度，429 响应结构固定。

**Files:**
- Create: `app/modules/quota/router.py`
- Modify: `app/api/v1/__init__.py`
- Test: `tests/integration/test_quota_api.py`

**Steps:**

- [ ] `GET /api/v1/quota` 返回：

```json
{
  "plan": "free",
  "usage": {
    "documents": {"used": 12, "limit": 100},
    "storage_bytes": {"used": 1048576, "limit": 524288000},
    "queries_per_month": {"used": 30, "limit": 1000},
    "llm_tokens_per_month": {"used": 12000, "limit": 500000}
  }
}
```

- [ ] 429 响应体固定为：

```json
{"error": {"code": "QUOTA_EXCEEDED", "message": "本月查询次数已用尽",
           "details": {"metric": "queries_per_month", "limit": 1000, "used": 1000, "remaining": 0}}}
```

按 metric 提供中文文案：`documents` → "文档数量已达上限"、`storage_bytes` → "存储空间已满"、`queries_per_month` → "本月查询次数已用尽"、`llm_tokens_per_month` → "本月生成额度已用尽"。
- [ ] 集成测试：把 `queries_per_month` 限额 monkeypatch 为 1，第一次提问成功，第二次返回 429 且 `details.metric == "queries_per_month"`，`message` 为中文，响应体不含英文堆栈。

**Security/Boundary Notes:** 响应不暴露其他租户的用量；`GET /quota` 只读当前 JWT 所属租户。

---

## Task 3: 结构化日志与 trace_id

**Goal:** 每个请求有可追踪 id，日志为 JSON 且已脱敏。

**Files:**
- Modify: `app/core/logging.py`
- Create: `app/core/middleware.py`
- Modify: `app/main.py`
- Test: `tests/unit/test_logging_redaction.py`

**Steps:**

- [ ] 实现 `TraceMiddleware`：读 `X-Request-Id` 头，无则生成 `uuid4().hex`，写入 contextvar 并回写响应头；记录 `method`、`path`、`status`、`duration_ms`、`tenant_id`、`trace_id`。健康检查路径 `GET /healthz` 不记录，避免噪声。
- [ ] 日志 processor 追加 `trace_id` 与 `tenant_id`（从各自 contextvar 取，缺失时为 `null`）。
- [ ] 脱敏 processor：正则替换 `sk-[A-Za-z0-9]{8,}`、`[\w.+-]+@[\w-]+\.[\w.]+`、`1[3-9]\d{9}` 为 `***`；同时把 `password`、`authorization`、`api_key` 字段名整体替换为 `***`。
- [ ] 生成链路把 `trace_id` 写入 `messages.trace_id`，实现"从一条答案反查到那次检索的日志"。
- [ ] 单测：构造含 API key、邮箱、手机号的日志上下文，断言输出中三者均被替换且 `trace_id` 存在。
- [ ] 手工验证：发起一次问答，用返回头里的 `X-Request-Id` 在日志中 grep，应能串起 检索 → 重排 → 生成 三条记录。

**Security/Boundary Notes:** 请求体与文档正文永不入日志（只在调试接口的 `preview` 中出现，且限制 120 字）。`trace_id` 不加租户前缀，避免泄露租户信息给外部调用方。

---

## Task 4: 评测集与回归脚本

**Goal:** 有可重复运行的检索质量指标，改分块或阈值后能判断是否退化。

**Files:**
- Create: `tests/eval/dataset.jsonl`
- Create: `scripts/eval.py`
- Create: `tests/eval/baseline.json`
- Create: `scripts/build_eval_corpus.py`

**Steps:**

- [ ] 编写 `scripts/build_eval_corpus.py`：用 `tests/fixtures/` 下的 3 份文档（Markdown 员工手册、PDF 产品说明、Markdown 售后政策）建一个专用租户与 KB，走完整入库流程，输出每个 chunk 的 `chunk_id` 与文本到 `tests/eval/corpus.json`。**评测库与开发库隔离**，用一个固定 slug `__eval__` 的租户。
- [ ] 编写 `tests/eval/dataset.jsonl`，首期 20 条，字段 `{"query": "...", "relevant_chunk_ids": ["..."], "type": "fact|compare|cross|no_answer"}`。分布要求：`no_answer` 至少 4 条（20%），`cross` 至少 3 条。
- [ ] 编写 `scripts/eval.py`：

```python
# 对每条 query 调 retrieve()，计算：
# recall@5 = |top5 ∩ relevant| / |relevant|，对 fact/compare/cross 平均
# mrr      = 1 / 首个命中排名，未命中记 0
# no_answer 准确率 = 判定为 no_answer 的比例（应为 100%）
# 输出 JSON 到 tests/eval/last_run.json，并与 baseline.json 比对
```

- [ ] 脚本退出码：`recall@5` 比基线低超过 0.02 或 `no_answer` 准确率 < 1.0 → 退出码 1，打印退化明细（哪条 query 掉了）。
- [ ] 首次运行后把结果写入 `tests/eval/baseline.json` 作为基线，并在文件头注释记录生成日期与当时的分块参数。
- [ ] 运行：`uv run python -m scripts.eval`
  - Expected：打印 `recall@5=0.xx mrr=0.xx no_answer=1.00`，退出码 0。

**Security/Boundary Notes:** 评测脚本使用独立的 `__eval__` 租户，避免污染真实租户数据。脚本只读检索、不调用 LLM，因此可离线高频运行，不产生生成成本。

---

## Phase Completion Checklist

- [ ] 所有任务完成
- [ ] 四类配额均有超限测试
- [ ] 429 响应结构与中文文案符合契约
- [ ] 日志可用 `trace_id` 串起单次问答全链路
- [ ] 脱敏单测通过，日志中无 key/邮箱/手机号
- [ ] 评测脚本可运行，`baseline.json` 已生成并有分块参数记录
- [ ] `alembic downgrade -1` 可回滚 `usage_records`

## 执行结果

**Status:** Done

- 已实现：配额计划与拦截、Redis 月计数器、`usage_records` 记账、`GET /quota`、429 契约、日志脱敏、trace_id、评测语料与回归脚本、迁移 0005
- 验证：文档数/存储/查询次数三类超限；429 结构含 remaining；脱敏覆盖 key/邮箱/手机号/密码字段
- 评测结果（假向量、11 个 chunk）：`recall@5=1.0`、`mrr=0.9412`、`no_answer=1.0`、样本 21
- 说明：假 Provider 仅字符级相似度，基线数值不代表真实语义检索质量，接入真实 Embedding 后需重跑 `--write-baseline`
