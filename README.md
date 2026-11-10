# 多租户 RAG 知识库系统 · v1.1

面向团队的私有知识库问答系统：自助注册、各自空间、上传文档、带引用回答。支持**三层可插拔 Provider**（向量 / 生成 / 重排），可在「本地语义检索 + 远程大模型」与「全离线」之间一键切换。

- 当前版本：**1.1.0**（[1.0 → 1.1 变更说明](docs/reports/2026-11-10-v1.0-vs-v1.1.md)）
- 设计文档：`docs/specs/2026-11-08-multi-tenant-rag-design.md`
- 实施与加固计划：`docs/plans/`
- 质量基线：**320 项测试通过**、ruff 全清、CI 覆盖后端 + 前端 + 检索评测

---

## 核心能力

| 领域 | 能力 |
|---|---|
| 多租户 | JWT 内嵌租户 → 仓储层过滤 → PostgreSQL RLS 兜底 → 对象存储按租户前缀，四道防线 |
| 检索 | pgvector 余弦召回 + `pg_trgm` 三元组召回，RRF 融合，可选重排；无答案阈值可标定 |
| 生成 | SSE 流式输出、引用上标可点击、Markdown 渲染、停止生成、重新回答 |
| 离线模式 | 零外呼：字符袋词法检索 + 原文摘录回答，断网也能演示全链路 |
| 会话 | 新建 / 删除（二次确认）/ 标题搜索 / 按时间升降序 / 滚轮翻找 / 导出 Markdown |
| 质量闭环 | 点赞点踩（附带问题、回答、引用快照）/ 会话导出 / 检索评测与阈值回归 |
| 运维 | `/metrics` 指标、健康检查双端点、审计日志、限流、备份脚本、CI 评测任务 |
| 安全 | 密码强度、登录/注册/问答三级限流、RLS、审计留痕、生产启动护栏 |

三个 Provider 相互独立，可任意组合：

```
EMBEDDING_PROVIDER = local | api | fake    # 本地 bge-m3 / 远程向量 / 字符袋
LLM_PROVIDER       = api   | fake          # OpenAI 兼容 / 原文摘录
RERANKER_PROVIDER  = api   | fake
```

> 无 API Key 时可用 `fake` 跑通全链路；要真实语义效果，向量与生成至少各配一个真实服务。
> 推荐组合：`local`（本机 bge-m3）+ `api`（任意 OpenAI 兼容对话服务），检索不出网、生成走远程。

## 快速开始

### 方式一：全离线（无需任何 Key，适合先看效果）

```bash
cp .env.example .env          # 默认 fake，可直接用
docker compose up -d postgres redis
uv sync                       # 安装主依赖（不含本地向量）
uv run alembic upgrade head
uv run uvicorn app.main:app --port 8000        # 终端 1
uv run python -m worker.main                   # 终端 2
cd web && npm install && npm run dev           # 终端 3
```

打开 <http://localhost:5173>（请用 `localhost`，Vite 只监听 IPv6 回环）。

### 方式二：本地语义检索（推荐日常使用）

```bash
# 约 2.5GB 依赖；模型权重另需约 2.2GB
uv sync --extra local-embedding

# 从 ModelScope 下载 bge-m3（国内比 hf-mirror 稳定）
uv run python -c "from modelscope import snapshot_download; print(snapshot_download('BAAI/bge-m3', cache_dir='data/models'))"
```

在 `.env` 中配置（`EMBEDDING_MODEL` 填上面的绝对路径）：

```ini
EMBEDDING_PROVIDER=local
LLM_PROVIDER=api
LLM_BASE_URL=https://你的服务/v1
LLM_API_KEY=...
LLM_MODEL=...
```

首次启动会有 3~16 秒模型预热（后台进行，不阻塞健康检查）。

### 方式三：容器整体启动

```bash
docker compose up -d --build
```

| 服务 | 地址 | 说明 |
|---|---|---|
| web | <http://localhost:8080> | nginx 静态站 + `/api` 反向代理（SSE 已关缓冲）|
| api | <http://localhost:8000> | 启动时自动执行迁移 |
| worker | — | outbox 轮询 + 定时维护 |
| postgres / redis | 5432 / 6379 | |
| minio | 9000 / 9001 | 仅 `--profile s3` |

## 关键配置

| 变量 | 默认 | 说明 |
|---|---|---|
| `RETRIEVAL_NO_ANSWER_THRESHOLD` | 0.55 | 在线语义检索的拒答线，**换嵌入模型必须重标** |
| `RETRIEVAL_NO_ANSWER_THRESHOLD_FAKE` | 0.10 | 离线词法打分的拒答线（量纲不同，不能混用）|
| `RETRIEVAL_SPARSE_ANSWER_THRESHOLD` | 0.35 | 稀疏通道单独门槛，兜住精确编号类查询 |
| `RETRIEVAL_RERANK_ENABLED` | false | 假重排会按字面覆盖率打乱语义排序，默认关闭 |
| `CHAT_RATE_LIMIT_PER_MINUTE` | 5 | 问答按用户限流 |
| `LOGIN_RATE_LIMIT_PER_MINUTE` / `REGISTER_RATE_LIMIT_PER_MINUTE` | 20 / 5 | 按 IP 限流 |
| `INGEST_MAX_CONCURRENT` | 2 | 同时处理的入库文档数（bge-m3 抢 torch 线程）|
| `OFFLINE_SCAN_LIMIT` | 2000 | 离线检索单次扫描块数上限 |
| `QUOTA_WARN_RATIO` | 0.8 | 用量超过该比例时告警并标黄 |
| `METRICS_ENABLED` | true | `/metrics` 开关（建议在网关层限制来源）|

## 常用命令

```bash
uv run pytest -q                                  # 全量测试（320 项）
uv run pytest tests/isolation -v                  # 租户隔离（CI 阻断项）
uv run ruff check app/ worker/ scripts/ tests/    # 静态检查
uv run alembic upgrade head                       # 迁移
uv run python -m scripts.smoke                    # 全链路冒烟
uv run python -m scripts.calibrate_threshold      # 标定拒答阈值
uv run python -m scripts.calibrate_threshold --check   # CI 用：退化即失败
uv run python -m scripts.reembed_chunks --tenant <slug> # 换嵌入模型后重建向量
uv run python -m scripts.backup_db --keep 7       # pg_dump 备份（保留 7 份）
uv run python -m scripts.toggle_online --status   # 查看/切换运行模式
```

## 架构要点

**四道租户防线**：JWT 内嵌 `tenant_id` → 仓储层过滤 → PostgreSQL RLS 兜底 → 对象存储按租户前缀。

两个已固定下来的细节：

1. `SET` 是会话级、`SET LOCAL` 是事务级。用 `SET` 会让连接池复用时串租户，统一用 `set_config(name, value, true)`。
2. 事务提交后 `SET LOCAL` 失效，入库状态机每阶段提交后都重新绑定租户，否则后续 UPDATE 会被 RLS 过滤成 0 行。

**入库用 outbox 表**而非直接投 Redis：让「写库」与「投消息」同事务，牺牲 1 秒轮询延迟换取不丢任务；Redis 挂掉时入库仍可用。

**检索的两条路径**：

```
在线：query → embedding(可缓存) → pgvector 余弦 + pg_trgm → RRF → (可选)重排 → 组装上下文
离线：query → 逐块字符袋打分（余弦 × 覆盖率）→ 组装上下文     # 不碰向量，索引来自哪个模型都无所谓
```

**问答链路不占连接池**：向量计算放在事务外，数据库事务只包查询与写入（连接池 15 条，推理动辄 1~2 秒）。

## 数据库角色

| 角色 | 用途 | 权限 |
|---|---|---|
| `postgres` | 仅迁移与运维脚本 | 超级用户，**绕过 RLS** |
| `rag_app` | 应用连接 | `NOSUPERUSER NOBYPASSRLS`，受 RLS 约束 |

应用若误用超级用户，隔离测试会假绿、上线后串数据——这是最需要守住的一条。

## 运维与安全

**健康检查**——两个端点语义不同，探针不要混用：

| 端点 | 判定 | 用途 |
|---|---|---|
| `GET /healthz` | 进程能响应即 200，不查依赖 | liveness |
| `GET /readyz` | 执行 `SELECT 1` 并 ping Redis，失败返回 503 | readiness |

**指标** `GET /metrics`：请求数/延迟（路由按 UUID 归一化）、问答结果分布（正常/拒答/中断）、outbox 积压、解析中文档数。指标**不带租户标签**，避免泄露客户规模。

**审计**：`kb.*`、`document.delete`、`member.add/update/remove`、`auth.login`、`mode.switch`。日志与业务同事务写入，业务回滚则日志一起消失。

**限流**：登录按 IP 20/min、注册按 IP 5/min、问答按用户 5/min，超限 `429` + `Retry-After`。

**生产启动护栏**（不满足直接失败）：

- `SECRET_KEY` 必须显式配置
- 禁止 `EMBEDDING_PROVIDER=fake`（假向量无语义能力）
- 禁止 `STORAGE_BACKEND=local`（多副本会写读不同机器）
- 非本地环境拒绝切到离线模式
- 检索调试路由即使 `ENABLE_DEBUG_API=true` 也不挂载

**资源上限**：单文件 50 MB；zip 容器解压上限 200 MB（防解压炸弹）；单文档入库超时 300 s；SSE 每 15 s 心跳。

## 目录结构

```
app/
  api/v1/            路由聚合与依赖注入（每请求复核成员关系）
  core/              配置、DB、RLS、限流、指标、模式切换、HTTP 客户端
  modules/           iam / tenancy / knowledge / ingestion / retrieval / generation
                     conversation / quota / audit / system
  providers/         embedding(local|api|fake) / llm(api|fake) / reranker(api|fake) / storage
worker/              outbox 轮询、卡住任务自愈、用量计数重建
web/                 React 19 + Vite + Tailwind v4
tests/               unit / integration / security / isolation / eval
migrations/          Alembic（0001~0007）
scripts/             冒烟、标定、重建向量、备份、模式切换、模拟服务
docs/                specs / plans / reports
```

## 已知限制

- 离线模式是**字面匹配**：同义改写（「汽车」vs「轿车」）召回不到，排序弱于语义检索，仅适合演示与联调。
- 本地向量依赖较重（约 2.5GB），未放进主依赖，需 `uv sync --extra local-embedding`。
- 前端 token 存 `localStorage`（XSS 面更大），后续应迁到 httpOnly cookie + CSRF。
- 找回密码目前**只有界面**，验证码与重置接口未接入。
- 成员必须先在系统注册，暂不支持邮件邀请；无 API Key / Webhook。
- 多模态解析、OCR、外部数据源连接器、Agent、支付不在本期范围。
