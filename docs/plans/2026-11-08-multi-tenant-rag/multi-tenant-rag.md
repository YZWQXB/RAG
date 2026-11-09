# 多租户 RAG 知识库系统 实施计划

**Created:** 2026-11-08
**Status:** Done
**Source Spec:** `docs/specs/2026-11-08-multi-tenant-rag-design.md` (v1.0)

## Overview

**Goal:** 交付一个对外 SaaS 形态的多租户 RAG 知识库系统，客户可自助注册、上传文档、基于文档问答并获得带页码引用的答案。

**Architecture:** 模块化单体（FastAPI）+ 独立 worker 进程，共享同一代码库与数据模型。多租户隔离采用「共享表 + PostgreSQL RLS」四道防线，租户上下文从 JWT 解析后经 `SET LOCAL` 绑定到数据库事务。入库走 outbox 表轮询（保证与文档记录同事务），检索走 pgvector dense + pg_trgm 双路召回 + RRF 融合 + rerank。

**Tech Stack:**

| 层 | 选型 | 版本策略 |
|---|---|---|
| 语言 | Python 3.12 | `uv python pin 3.12`，容器 `python:3.12-slim` |
| Web | FastAPI + uvicorn | 精确锁在 `uv.lock` |
| ORM | SQLAlchemy 2.0 (async) + asyncpg + Alembic | 同上 |
| 数据库 | PostgreSQL 16 + pgvector（镜像 `pgvector/pgvector:pg16`） | 镜像 tag 固定 |
| 缓存/计数 | Redis 7 | 镜像 tag 固定 |
| 对象存储 | MinIO（S3 协议） | 镜像 tag 固定 |
| 队列 | ARQ（Redis） | 仅承载定时任务 |
| 解析 | PyMuPDF / python-docx / markdown-it-py | 精确锁 |
| 前端 | React + TypeScript + Vite + Tailwind | 精确锁 |
| 包管理 | uv（清华 TUNA 索引） | 生成 `uv.lock` |

## Files and Responsibilities

| 文件/模块 | 动作 | 职责 | 备注 |
|---|---|---|---|
| `pyproject.toml` / `uv.lock` | 创建 | 依赖与版本锁、pytest/alembic 配置 | 索引指向 TUNA |
| `docker-compose.yml` | 创建 | PG/Redis/MinIO 本地编排；Phase 7 追加 api/worker | 卷持久化 |
| `docker/postgres-init.sql` | 创建 | 建非超级用户 `rag_app`、建 `rag_test` 库 | **RLS 生效的前提** |
| `app/core/config.py` | 创建 | pydantic-settings 配置 | 读 `.env` |
| `app/core/db.py` | 创建 | async engine / session factory | 应用与迁移 URL 分离 |
| `app/core/tenant.py` | 创建 | 租户 ContextVar、`bind_tenant()`（`SET LOCAL`） | 隔离核心 |
| `app/core/security.py` | 创建 | Argon2 哈希、JWT 签发与校验 | JWT 内嵌 `tenant_id` |
| `app/core/errors.py` | 创建 | 统一错误体与错误码 | 中文文案 |
| `app/core/logging.py` | 创建 | structlog 配置 | 脱敏 processor |
| `app/modules/*/` | 创建 | 按域分包，模块内 `router/service/repository/models/schemas` | 跨模块只调 service |
| `app/providers/*/` | 创建 | Embedding/LLM/Reranker/Storage 四类抽象与实现 | 业务层只依赖接口 |
| `worker/main.py` | 创建 | outbox 轮询器 + ARQ 消费者 | 独立进程 |
| `migrations/versions/` | 创建 | 5 个迁移：0001 初始 / 0002 知识库 / 0003 文档与块 / 0004 会话 / 0005 配额与用量 | 必须可 `downgrade` |
| `tests/isolation/` | 创建 | 四类隔离测试 | **CI 阻断项** |
| `tests/eval/` | 创建 | 评测集与指标脚本 | Recall@5 回归 |
| `web/` | 创建 | 最小控制台（4 个界面） | 视觉设计另立 spec |
| `scripts/seed.py` | 创建 | 两租户种子数据 | 隔离验证用 |
| `scripts/smoke.py` | 创建 | 全链路冒烟 | 上传→提问 |

## Interface Implementation Map

设计文档 §17 定义了 4 个界面的信息结构与状态。视觉体系（配色、字体、间距令牌）在设计文档中明确为 Non-Goal，本计划只实现结构与状态，组件层保持最小可用。

| 设计决策 | 来源 | 实现落点 | 验证方式 |
|---|---|---|---|
| 注册/登录（邮箱+密码，注册即建租户并成 owner） | spec §17 | `web/src/pages/{Register,Login}.tsx`、`app/api/v1/auth.py` | Phase 2 单测 + Phase 7 手工流程 |
| 校验失败/邮箱已存在/密码强度不足三态 | spec §17 | `web/src/components/FormField.tsx` 错误态、后端 400 错误码 | Phase 7 逐态截图检查 |
| 知识库列表：卡片（名称/文档数/更新时间）+ 空态引导 + 加载骨架 + 无权限态 | spec §17 | `web/src/pages/KbList.tsx` | Phase 7 空态与有数据态各一张截图 |
| 文档管理：上传区 + 状态表格 | spec §17 | `web/src/pages/KbDocuments.tsx` | Phase 3 状态机 + Phase 7 轮询验证 |
| 文档状态必须分阶段显示（解析中/切分中/向量化中），不能只有一个转圈 | spec §17 关键交互 | `documents.status` 枚举 + `documents.stage` 字段，前端 2s 轮询 | Phase 3 集成测试断言阶段推进；Phase 7 观察三态文案 |
| 问答界面：左会话列表 / 中消息流 / 右引用面板 | spec §17 | `web/src/pages/Chat.tsx` | Phase 7 桌面与窄屏各检查一次 |
| 流式 token 渲染 | spec §17 | `web/src/lib/sse.ts` + `ReadableStream` 解析 | Phase 5 后端 SSE 测试 + Phase 7 手工观察 |
| 引用可点击跳原文并高亮页码 | spec §17 关键交互 | `web/src/components/CitationPanel.tsx` + 后端 `citations` SSE 事件 | Phase 5 引用映射测试 + Phase 7 点击验证 |
| 无答案兜底文案 | spec §17 / §11.6 | 后端 `no_answer` 分支 + 前端提示条 | Phase 5 单测 |
| 配额超限提示（剩余额度+升级入口） | spec §17 / §13 | 前端拦截 429 渲染提示条 | Phase 6 后端 429 + Phase 7 手工验证 |
| 网络中断重试 | spec §17 | 消息流底部重试按钮 | Phase 7 手工断网验证 |
| 所有错误文案为中文用户语言，不暴露堆栈 | spec §17 / §14 | `app/core/errors.py` 集中定义 | Phase 2 起每个接口测试断言 `message` 为中文 |

## Phases

### Phase 1: 工程骨架与租户隔离
**File:** [phase-1-foundation.md](phase-1-foundation.md)
**Goal:** 可运行的 FastAPI 骨架、数据库与 RLS 生效、租户注册登录可用、四类隔离测试全绿
**Tasks:** 5
**Status:** Done
**Completion:** √

### Phase 2: 身份权限与知识库管理
**File:** [phase-2-iam-knowledge.md](phase-2-iam-knowledge.md)
**Goal:** RBAC 四角色 + 知识库级 ACL 判定收敛到单点，知识库 CRUD 与授权可用
**Tasks:** 5
**Status:** Done
**Completion:** √

### Phase 3: 文档上传与入库流水线
**File:** [phase-3-ingestion.md](phase-3-ingestion.md)
**Goal:** 上传 PDF/Word/MD/TXT 后经 outbox 驱动 worker 完成解析、分块、向量化，状态推进到 `ready`
**Tasks:** 7
**Status:** Done
**Completion:** √

### Phase 4: 检索
**File:** [phase-4-retrieval.md](phase-4-retrieval.md)
**Goal:** 查询改写、dense + trigram 双路召回、RRF 融合、重排、上下文组装，并提供内部检索调试接口
**Tasks:** 6
**Status:** Done
**Completion:** √

### Phase 5: 生成、引用与会话
**File:** [phase-5-generation.md](phase-5-generation.md)
**Goal:** Prompt 构建、LLM 流式输出、引用锚点映射、无答案兜底、会话持久化
**Tasks:** 5
**Status:** Done
**Completion:** √

### Phase 6: 配额、评测与观测
**File:** [phase-6-quota-eval.md](phase-6-quota-eval.md)
**Goal:** 配额硬拦截与用量记账、结构化日志与 trace_id、评测集与 Recall@5 回归脚本
**Tasks:** 4
**Status:** Done
**Completion:** √

### Phase 7: 最小控制台与一键部署
**File:** [phase-7-console-deploy.md](phase-7-console-deploy.md)
**Goal:** 4 个界面可用、`docker compose up` 一键起全栈、健康检查就位
**Tasks:** 5
**Status:** Done
**Completion:** √

## Dependencies

- **阶段顺序**：Phase N 依赖 Phase N-1 的完成。Phase 3 依赖 Phase 2 的权限判定；Phase 5 依赖 Phase 4 的检索结果结构；Phase 6 的配额钩子在 Phase 3 上传接口预留调用位（未实现时以空函数放行）。
- **外部服务**（Phase 3 起真实需要）：Embedding API（`BAAI/bge-m3`，1024 维）、LLM API（OpenAI 兼容，默认 DeepSeek `deepseek-chat`）、Reranker API（`BAAI/bge-reranker-v2-m3`）。三者均通过 `app/providers` 抽象，测试用 Fake 实现，**CI 不依赖网络**。
- **运行时假设**：Docker 29.x 可用；`uv` 可用；PostgreSQL 镜像自带 `pgvector` 与 `pg_trgm` 扩展；机器 4C16G。
- **Python 版本**：本地为 3.13，项目通过 `uv python pin 3.12` 锁定 3.12，容器用 `python:3.12-slim`。所有命令用 `uv run` 执行，自动使用锁定版本。
- **数据库角色**：迁移用超级用户（`postgres`），应用用非超级用户 `rag_app`。**这是 RLS 生效的必要条件**——超级用户始终绕过 RLS，若应用也用超级用户，隔离测试会假绿。

## Success Criteria

- [x] 四类隔离测试全绿：接口级 404、DB 级 RLS 隔离、向量级不跨租户召回、连接池交替 100 次无泄漏
- [x] 上传 ≤20 页 PDF 后 60 秒内 `documents.status = ready`，chunk 数与页面内容匹配（PDF 端到端用例已覆盖页码）
- [x] 问答返回带引用锚点的答案，引用可定位到 `document_id` + 页码
- [x] 迁移可 `alembic downgrade base` 回滚（0001→0005 全链往返验证通过）
- [x] `docker compose up -d` 后 api 与 worker 均健康，全链路可跑通 `scripts/smoke.py`（容器内 SMOKE OK）
- [x] 评测集 Recall@5 有基线数值并记录在 `tests/eval/baseline.json`（假向量基线，接真实 Embedding 后需重跑）
- [x] 无任何接口在错误时返回英文技术文案或堆栈（统一中文 error 体，用例断言）
- [x] 所有清单项通过，且 `pytest` 全绿（146 passed）

## Blocking Questions

无。所有影响实施的决策已在设计文档中确认；Python 版本差异已用 `uv pin` 解决。

## Deferred Decisions

| 待定项 | 为何不阻塞 | 默认实现 |
|---|---|---|
| 中文 BM25（zhparser）替代 trigram | 需实测数据支撑，且要自定义 PG 镜像 | 先用 `pg_trgm`；Phase 4 若 Recall@5 < 0.7 再立项 |
| 集成测试用 testcontainers 还是 compose 内测试库 | 两者隔离性等价，后者在 Windows 上启动快得多 | 用 compose 内的 `rag_test` 库，pytest fixture 每用例 TRUNCATE |
| 语义缓存（答案级） | 与正确性无关，纯成本优化 | 只做 embedding 缓存（同文本 24h 复用向量） |
| 租户 Web 识别方式（子域 vs 路径） | 首期单域名即可 | 用路径 `/t/{slug}`，JWT 内仍以 claim 为准 |
| 前端 UI 组件库 | 首期只交付结构与状态 | Tailwind + 手写最小组件，不引入 shadcn 初始化流程 |
| 租户数据删除保留期 | 首个合规客户提出前不阻塞 | 软删除 15 天，Phase 6 只做软删除，物理清除留脚本 |
| BYO API Key | 无客户诉求 | 不支持，统一走平台额度 |
