# 多租户 RAG 知识库系统 · 架构与学习指南

> 本文档面向希望**深入学习本项目**的开发者：先看懂全局架构，再按知识点逐个击破。
> 项目是一个生产级的多租户 RAG（检索增强生成）知识库问答系统，后端 FastAPI + PostgreSQL(pgvector)，前端 React + Vite，异步 worker 处理文档入库。

---

## 1. 一句话架构

```
用户 → React 前端(SPA) → FastAPI(API 层) → 检索编排(召回+融合+重排) → LLM 流式生成(SSE)
                ↕                    ↕                        ↕
              nginx 反代         PostgreSQL + pgvector        Redis(缓存/限流/配额)
                                   ↑
                        worker(异步入库: 解析→分块→向量化)
```

---

## 2. 目录结构导览

```
RAG_ai_pro/
├── app/                    # 后端主应用
│   ├── main.py             # FastAPI 入口：异常处理/生命周期/健康检查/metrics
│   ├── api/v1/             # 路由层：auth.py 认证、deps.py 依赖注入(Principal/Session)
│   ├── core/               # 横切关注点（与业务无关的基础设施）
│   │   ├── config.py       # pydantic-settings 集中配置 + 生产护栏
│   │   ├── db.py           # 双引擎：业务引擎(RLS 受限) vs 管理引擎(跨租户)
│   │   ├── tenant.py       # ★ RLS 租户绑定(set_config 事务级)
│   │   ├── security.py     # Argon2 密码哈希 + JWT
│   │   ├── ratelimit.py    # Redis 滑动窗口限流
│   │   ├── mode.py         # 离线/在线模式热切换（内存+.env+缓存三同步）
│   │   └── middleware.py   # 纯 ASGI Trace 中间件（不用 BaseHTTPMiddleware）
│   ├── modules/            # 业务模块（每个: models/schemas/service/router）
│   │   ├── iam/            # 用户/成员/角色/权限
│   │   ├── tenancy/        # 租户管理
│   │   ├── knowledge/      # 知识库 CRUD + ACL
│   │   ├── ingestion/      # 文档上传与入库管道(pipeline/)
│   │   ├── retrieval/      # ★ 检索核心：dense/sparse/lexical/fusion/rerank/rewrite
│   │   ├── generation/     # 问答编排 + SSE 流 + 引用抽取
│   │   ├── conversation/   # 会话与消息
│   │   ├── quota/          # 配额与用量计数
│   │   └── audit/          # 审计日志
│   └── providers/          # 可插拔 Provider（工厂 + 三层实现）
│       ├── embedding/      # local(bge-m3 进程内) / api(远程) / fake(字符袋)
│       ├── llm/            # openai_compat(DeepSeek 等) / fake(原文摘录)
│       ├── reranker/       # api(bge-reranker) / fake
│       └── storage/        # local(文件系统) / s3(MinIO/OSS)
├── worker/                 # 独立进程：outbox 轮询入库 + 定时维护
├── web/                    # React 18 + Vite 前端
├── migrations/             # Alembic 迁移（7 个版本）
├── scripts/                # 运维/评测脚本（seed、eval、calibrate、backup）
├── tests/                  # unit / integration / isolation / security / eval 五类
├── docker/                 # postgres-init.sql（角色+RLS 策略）
└── docs/                   # 规格文档、实施计划、评测报告
```

---

## 3. 技术栈清单

| 层 | 技术 | 用途 |
|---|---|---|
| Web 框架 | FastAPI ≥0.115 | 异步 API、依赖注入、OpenAPI |
| ASGI 服务器 | uvicorn ≥0.32 | 生产运行 |
| ORM | SQLAlchemy 2.0 async ≥2.0.36 | 异步 ORM、DeclarativeBase |
| 驱动 | asyncpg ≥0.30 | PostgreSQL 异步协议 |
| 迁移 | Alembic ≥1.14 | schema 版本管理 |
| 校验 | Pydantic v2 + pydantic-settings | DTO 与配置 |
| 向量 | pgvector ≥0.3.6 | 余弦相似度检索 + HNSW 索引 |
| 认证 | PyJWT + argon2-cffi | JWT 签发/校验、密码哈希 |
| 缓存 | redis ≥5.2 | 查询向量缓存/限流/配额计数 |
| HTTP | httpx ≥0.27 | 异步调用 LLM/向量 API |
| 指标 | prometheus-client | /metrics |
| 对象存储 | boto3 | S3/MinIO |
| 文档解析 | pymupdf + python-docx | PDF/DOCX → 文本 |
| 分词 | tiktoken | token 估算 |
| 前端 | React 18 + TypeScript + Vite | SPA |
| 前端网络 | fetch + ReadableStream | SSE 流式解析（非 EventSource） |
| 包管理 | uv（后端）/ npm（前端） | 锁文件 uv.lock / package-lock.json |
| 本地向量 | torch + sentence-transformers（可选 extra） | bge-m3 进程内推理（~2.5GB） |

---

## 4. 核心架构决策（为什么这么设计）

### 4.1 多租户隔离：四道防线
1. **JWT 内嵌 tenant_id** → 请求上下文
2. **仓储层过滤**：所有查询显式 `WHERE tenant_id = :tid`
3. **PostgreSQL RLS 兜底**：`SET LOCAL app.tenant_id`（事务级，防连接池串号）
4. **对象存储按租户前缀**：`tenant/<uuid>/kb/...`

关键代码：`app/core/tenant.py`（必须用 `set_config(..., true)` 而非 `SET`，因为 SET 的会话级残留在连接归还后会跨租户泄露）。

### 4.2 检索管道：双路召回 + RRF 融合
```
query → normalize → [稠密召回 pgvector 余弦] ─┐
                  → [稀疏召回 pg_trgm 相似度] ─┴→ RRF 融合 → 可选重排 → 上下文组装
```
- 离线模式：稠密通道退化为**字符袋词法打分**（`lexical.py`），不碰向量列
- 拒答判定 `answerable()`：稠密、稀疏**两路阈值取或**，防"字面命中但语义分低"被误拒
- RRF：`score = Σ 1/(60+rank)`，无需把两路分数归一化到同一量纲

### 4.3 生成编排：三段事务策略（SSE 不持连接）
```
Phase A 短事务：检索 + 落用户消息 → 释放连接
Phase B 无事务：流式生成（SSE token 逐帧下发 + 心跳帧防代理断连）
Phase C 短事务：落助手消息 + 引用 + 用量
```
用户点「停止」时用 `asyncio.shield` 保住已生成部分落库（token 已花，不能白丢）。

### 4.4 异步入库：Outbox 模式（而非消息队列）
上传 API 只写 `outbox_events` 表（与业务同事务，保证不丢）；worker 每秒轮询，
`FOR UPDATE SKIP LOCKED` 领取，指数退避重试（1/4/16 秒），单文档超时 300s。
状态机：`pending → parsing → chunking → embedding → ready / failed`，每阶段提交一次，
前端轮询能看到真实进度。

### 4.5 可插拔 Provider（策略模式 + 工厂）
`get_embedding_provider() / get_llm_provider() / get_reranker_provider()`
按配置实例化 local/api/fake；测试通过 `set_xxx_provider()` 注入替身。
`local_bge.py` 内部：延迟加载 + 双检锁 + 线程池推理（torch 阻塞调用不进事件循环）。

### 4.6 离线/在线热切换（mode.py）
三件事同步：改内存（立刻生效）+ 重写 .env 三个 PROVIDER 行（重启保持）+ 清 Redis
查询向量缓存（防跨空间比较）。worker 通过 mtime 检测 .env 变化并同步（API 与 worker
是两个进程，不共享内存）。

---

## 5. 数据流走读（一次问答的完整链路）

1. 前端 `Chat.tsx` → `sse.ts streamChat()` POST `/api/v1/chat/stream`（fetch 流式，带 Bearer）
2. `generation/router.py _precheck`：限流 → KB 权限 → 配额（403/429 必须在流开始前抛）
3. `generation/service.py _prepare`：
   - 事务 A：权限过滤 kb_ids → 配额计数 → 会话/历史 → 落用户消息
   - **事务外**：`embed_cached` 算查询向量（CPU 密集不占连接池）
   - 事务 B：`retrieve()` 双路召回 + RRF + 重排
   - 若检索无依据 → `maybe_rewrite` 多轮指代消解改写后重试一次
4. `stream_answer` SSE 事件序列：`citations → token* → done`（或 `no_answer`/`error`）
5. 空闲超时窗口插入 `: ping` 心跳帧，防 nginx/代理切断
6. 完成后事务 C：落助手消息 + 引用映射 `extract_citations([n] → 块/页码)` + 用量
7. 前端把 `[n]` 渲染为可点击上标，点击定位到引用面板

### 一次文档入库的完整链路
1. 前端上传 → API 校验（大小 50MB、类型、解压炸弹 200MB 上限）→ 存 storage → 写 documents/versions + outbox 事件（同事务）
2. worker `_claim`（SKIP LOCKED）→ `run_ingestion` 状态机
3. `parse.py`（PDF/DOCX/MD/TXT → 带标题层级的块）→ `chunk.py`（600 字/90 重叠/句边界修正）→ `embed.py`（批量向量化）→ 写 chunks 表
4. 失败：退避重试 3 次 → failed + 中文原因；卡住 10 分钟 → `rescan_stuck` 自愈重排

---

## 6. 知识点地图（按学习顺序）

### 第一层：语言与框架基础
| 知识点 | 项目中的位置 | 掌握标准 |
|---|---|---|
| Python 3.12 类型注解（`X | None`） | 全部代码 | 能读懂并书写 |
| asyncio（Task/Queue/Lock/Event/wait_for/shield/gather） | `generation/service.py`、`local_bge.py`、`worker/main.py` | 理解为什么 CPU 密集要 `to_thread` |
| FastAPI 依赖注入（Annotated + Depends） | `api/v1/deps.py` | 能写自定义 Principal 依赖 |
| Pydantic v2（model_validator、SettingsConfigDict） | `core/config.py` | 能加一个带校验的配置项 |
| SQLAlchemy 2.0 async（select、session.begin） | `core/db.py`、各 models.py | 理解事务边界与连接池 |

### 第二层：数据与存储
| 知识点 | 位置 | 要点 |
|---|---|---|
| PostgreSQL RLS | `tenant.py`、`docker/postgres-init.sql` | `SET LOCAL` vs `SET`；双引擎绕行 |
| pgvector（cosine_distance、HNSW、ef_search） | `retrieval/dense.py` | 向量索引原理与调参 |
| pg_trgm（三元组相似度） | `retrieval/sparse.py` | 字面召回与 GIN 索引 |
| Alembic 迁移工作流 | `migrations/` | autogenerate 与手写修正 |
| Redis 数据结构选型 | cache/限流/配额三处 | string TTL、scan_iter、月计数器 |
| 连接池（pool_size/overflow/pre_ping/recycle） | `core/db.py` | 为什么 1800 秒回收 |

### 第三层：RAG 特有
| 知识点 | 位置 | 要点 |
|---|---|---|
| Embedding 与向量空间 | `providers/embedding/` | 归一化、维度校验、fake 与真实空间不可比 |
| 双路召回（dense + sparse） | `retrieval/service.py` | 互补性：语义 vs 字面 |
| RRF 融合 | `fusion.py` | 无量纲融合，k=60 |
| 重排（Reranker） | `rerank.py` | 交叉编码器 vs 双塔；假重排的反优化风险 |
| 拒答阈值与标定 | `answerable()`、`scripts/calibrate_threshold.py` | 阈值与模型强绑定，换模型必重标 |
| 查询改写（多轮指代消解） | `rewrite.py` | 门控（短句+指代词）、超时兜底、缓存、失败回退 |
| 分块策略 | `pipeline/chunk.py` | 标题层级优先 + 长度兜底 + 句边界 + 上下文头 |
| 引用抽取 | `citations.py` | `[n]` 映射回块，后端不改模型原话 |
| Prompt 组装与 token 预算 | `prompt.py`、`core/tokens.py` | blocks 截断到 budget |

### 第四层：工程化与生产
| 知识点 | 位置 | 要点 |
|---|---|---|
| SSE 流式协议（事件帧、心跳、X-Accel-Buffering） | `generation/service.py`、`web/src/lib/sse.ts` | 为什么不用 EventSource（无法带 Auth 头） |
| Outbox 模式 | `worker/tasks/outbox.py` | 与 MQ 的取舍：一表一轮询够用 |
| FOR UPDATE SKIP LOCKED | 同上 | 多 worker 安全领取 |
| 限流（按 IP vs 按 user） | `ratelimit.py` | 登录前不知身份按 IP，问答按 user |
| 可观测性（structlog JSON、Prometheus、trace_id） | `logging.py`、`metrics.py`、`middleware.py` | 结构化日志 + RED 指标 |
| 配置护栏（生产禁 fake/local storage） | `config._guard_production` | 启动即失败优于运行时事故 |
| 健康检查分层（liveness vs readiness） | `main.py` | 存活不查依赖，防故障放大 |
| CI 三件套（测试+前端+评测回归） | `.github/workflows/ci.yml` | 阈值也是回归对象 |
| 测试分层策略 | `tests/` 五目录 | unit 替身、integration 真库、isolation 专测 RLS |

### 第五层：安全
| 知识点 | 位置 | 要点 |
|---|---|---|
| Argon2 密码哈希 | `security.py` | 为什么不用 bcrypt/SHA |
| JWT 最小声明 + 每请求复核成员关系 | `deps.py` | 令牌只证明"曾是成员" |
| 404 vs 403 语义（防资源存在性泄露） | `permissions.py` | 跨租户 404，无权 403 |
| RBAC + ACL（角色通配 + 用户/角色主体） | `permissions.py` | read ⊂ write ⊂ manage 展开 |
| 解压炸弹防护 | config + parse | 50MB 压缩 / 200MB 解压上限 |
| 日志脱敏（不留用户查询原文） | `logging.py` + cache key 设计 | Redis 只存哈希 |

---

## 7. 建议学习路径（两周节奏）

- **第 1–2 天 · 跑起来**：`docker compose up -d postgres redis` → `uv sync` → `alembic upgrade head` → 起两个终端跑 api/worker → `cd web && npm i && npm run dev` → 注册 → 上传 `tests/fixtures/*.md` → 提问
- **第 3–4 天 · 读入口**：`main.py` → `deps.py` → 任一模块的 router→service→models 三件套（推荐 iam）
- **第 5–6 天 · 检索链路**：`retrieval/` 全目录 + `scripts/calibrate_threshold.py`，理解阈值标定
- **第 7–8 天 · 入库链路**：`ingestion/pipeline/` 四个文件 + `worker/`，画出状态机
- **第 9 天 · 生成与前端**：`generation/service.py`（SSE 三段事务）+ `web/src/lib/sse.ts`
- **第 10 天 · 隔离与安全**：`tenant.py` + `tests/isolation/` + `tests/security/`
- **第 11–12 天 · 运维面**：`mode.py`、`metrics.py`、`scripts/backup_db.py`、CI
- **第 13–14 天 · 动手改**：加一个配置项并写测试；给 retrieval 加一路新召回并接进 fusion；写一个运维脚本

---

## 8. 代码审查结论摘要（2026-11-17）

总体评价：**高质量代码库**。注释解释"为什么"而非"是什么"；事务边界、超时、重试、降级路径都有显式设计；342 项测试可收集，ruff 全清。

值得学习的亮点：
- 事务级 RLS 绑定 + commit 后重绑（`runner._commit_and_rebind`）
- SSE 心跳用生产者+队列而非直接 async for（防代理空闲断连）
- 查询改写的三道闸（门控/超时/缓存）+ 失败一律回退原句
- 生产护栏直接在配置层拦截（fake provider / local storage）

发现的改进点（均为低风险）：
1. `worker/tasks/aggregate.py` 的 `rebuild_usage_counters` 用 UTC `strftime('%Y%m')` 算月键，而 `quota/service._month_key` 用业务时区——**两处月键口径不一致**，月底 8 小时窗口内重建的计数器会写错月份。建议统一调用同一个月键函数。
2. `pyproject.toml` 依赖里含 arq 但 worker 刻意未用（自研 outbox）——建议移除依赖或注释说明，减少误解。
3. `get_llm_provider()` / `get_reranker_provider()` 每次调用都 new 新实例——当前对象轻量可接受；若未来 provider 持有连接池可加缓存。
4. `web/src/lib/sse.ts` 的 dispatch 对多行 `data:` 用隐式字符串拼接，SSE 规范允许 data 多行；当前后端单行 JSON 输出，不受影响。
