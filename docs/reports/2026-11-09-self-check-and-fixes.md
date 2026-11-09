# 自检与修复报告

- 日期：2026-11-09
- 范围：多租户 RAG SaaS（Phase 1–7 全量）
- 环境：Windows 11 + Python 3.12 + 本地 PostgreSQL 16/pgvector + Redis 7（Docker）
- 结论：**221 个用例全部通过，静态检查清零，覆盖率 87%，实机全链路冒烟通过**

## 一、总体结论

1. 项目可运行、可演示：注册 → 建库 → 上传 → 入库 → 检索 → 带引用回答，实机跑通（`scripts.smoke` 输出 `SMOKE OK`）。
2. 上一轮自检列出的 19 项问题（P0 4 项、P1 9 项、P2 6 项）已全部修复，其中 18 项有回归用例，1 项（启动脚本）以实机启动验证。
3. 本轮补充了修复项中唯一缺失回归的「租户 slug 字符集校验」测试（8 个用例），避免该防护在后续重构中被静默改坏。
4. 剩余限制集中在生产化细节（见第五节），不影响单机试用与功能验收。

## 二、测试覆盖范围与方式

用例分布（`pytest --collect-only` 实测）：

| 层次 | 目录 | 用例数 | 覆盖内容 |
|---|---|---|---|
| 单元 | `tests/unit` | 95 | 分块、引用定位、上下文预算、RRF 融合、权限矩阵、文档解析与限额、Provider 重试语义、配额月切、日志脱敏、S3 Provider |
| 集成 | `tests/integration` | 71 | 认证与租户、知识库 CRUD、ACL、上传、入库状态机、入库超时、dense/sparse 召回、SSE 心跳、会话、配额接口、健康检查、存储、worker 维护 |
| 隔离 | `tests/isolation` | 18 | API 越权、数据库 RLS、连接池复用串租户、知识库级隔离、并发下的租户不串号 |
| 安全 | `tests/security` | 37 | 登录/换票/锁定、登录限流、配置护栏、调试路由暴露、审计日志、注册入参校验 |
| 合计 | | **221** | |

方式与取舍：

- **不 mock 数据库**。用例直连真实 PostgreSQL（`rag_test` 库）与 Redis，RLS、`set_config(..., is_local=true)`、`FOR UPDATE SKIP LOCKED` 这些真正决定隔离成败的机制必须在真实引擎上验证，mock 只会得到假绿。
- **Provider 用 fake 实现**（字符袋向量），保证离线、确定性、可重复；真实语义质量另由评测脚本衡量（第四节）。
- **每个用例前清表并清空 `rl:*` 限流计数**，否则限流用例会与登录用例互相污染。
- 数据库不可用时 `@pytest.mark.db` 用例显式 skip 并说明原因，不伪装通过。

## 三、问题清单与修复

### P0（越权 / 隔离失效）

| # | 问题 | 影响 | 修复 | 回归 |
|---|---|---|---|---|
| 1 | 角色与成员关系只在签发 token 时计算 | token 有效期内被降权或移出空间仍可操作 | 每个请求从库复核 membership，角色以库为准 | `tests/isolation/test_api_isolation.py` |
| 2 | 会话接口未校验归属 | 任意登录用户可读写他人会话 | 查询与写入都带 `user_id` 过滤，越权返回 404 | `tests/integration/test_conversations.py` |
| 3 | 登录失败计数只 `flush` 未 `commit` | 异常路径回滚，锁定永不生效，可无限爆破 | 失败路径显式提交计数 | `tests/security/test_auth_flows.py` |
| 4 | 检索调试路由仅凭配置开关挂载 | 配置误带到生产即暴露 chunk 内容与各路召回分数 | 生产环境硬性不挂载，无视 `ENABLE_DEBUG_API` | `tests/security/test_debug_route_exposure.py` |

### P1（可用性 / 资源 / 正确性）

| # | 问题 | 影响 | 修复 | 回归 |
|---|---|---|---|---|
| 5 | 上传只校验压缩包大小 | docx 解压炸弹可打满磁盘 | 按 zip 中心目录声明的解压体积拦截（默认 200 MB） | `tests/unit/test_parse_limits.py` |
| 6 | 单文档入库无总超时 | 一个卡死的文档拖住唯一的 worker 轮询循环 | `asyncio.wait_for` 总超时 300 s，超时标记 `failed` 并写中文原因 | `tests/integration/test_ingest_timeout.py` |
| 7 | 入库重试非幂等 | 重复投递导致重复解析与重复计费 | 已有在途事件时不再投递 | `tests/integration/test_ingest_flow.py` |
| 8 | embedding 返回向量数与输入不一致仍重试 | 向量与文本错位入库，检索结果被永久污染 | 数量不一致直接上抛，不重试 | `tests/unit/test_providers_api.py` |
| 9 | `/healthz` 同时检查 DB 与 Redis | 依赖抖动会让编排系统批量重启实例，故障被放大 | 拆成 `/healthz`（存活）与 `/readyz`（依赖，失败 503） | `tests/integration/test_health_endpoints.py` |
| 10 | SSE 无心跳 | 网关空闲超时切流，长回答被截断 | 每 15 s 发送心跳帧 | `tests/integration/test_sse_keepalive.py` |
| 11 | 配额按 UTC 切月 | 中国区用户月初额度错位 | `QUOTA_TIMEZONE` 可配，默认 `Asia/Shanghai` | `tests/unit/test_quota_month.py` |
| 12 | 错误响应丢弃 `headers` | 429 缺 `Retry-After`，客户端无法正确退避 | 异常响应透传 `exc.headers` | `tests/security/test_login_rate_limit.py` |
| 13 | 租户 slug 无字符集校验 | 非法值进入 URL 与日志，属潜在注入面 | 白名单 `^[a-z0-9][a-z0-9-]{2,63}$`（超长由 schema 先行拦截） | `tests/security/test_auth_flows.py`（本轮补齐） |

### P2（工程完备度）

| # | 问题 | 影响 | 修复 |
|---|---|---|---|
| 14 | 缺少审计留痕 | 无法追溯谁删了库、谁改了 ACL | `audit_logs` 表（含 RLS）+ `GET /api/v1/audit-logs`，与业务同事务写入，覆盖 9 个动作 |
| 15 | `api_keys` 表无任何接口使用 | 死代码，且是一块无人看管的密钥面 | 迁移 `0006` 删除表与模型 |
| 16 | 登录无来源限流 | 换邮箱即可绕过账号锁定 | Redis 固定窗口 20 次/分钟，超限 429 |
| 17 | Windows 缺 `tzdata` | `ZoneInfo` 直接抛错，配额与时间逻辑不可用 | 显式依赖 `tzdata` |
| 18 | `start.bat` 依赖 node 在 PATH，`curl` 走系统代理，健康检查端点不对 | 一键启动在干净机器上失败 | node 四级探测、`--noproxy "*"`、改用 `/readyz` |
| 19 | Vite dev proxy 指向 `localhost` | IPv6 优先的机器解析失败 | 改为 `127.0.0.1` |

## 四、大规模知识库构建与校验

**构建方式**：`scripts/build_eval_corpus.py` 按模板生成 300 篇互不相同的中文制度/流程文档（含表格、长段落与唯一事实句），经真实 HTTP 上传接口进入完整流水线（解析 → 分块 → 向量化 → 落库），不使用任何直连插入的捷径。

**校验方式**：`scripts/scale_check.py` 统计入库吞吐、分块质量，并用带答案的问题集测召回率与延迟，同时验证规模下租户隔离仍然成立。

**实测结果**（fake provider，本地单机）：

| 指标 | 数值 |
|---|---|
| 文档数 | 300 篇，全部 `ready` |
| 分块数 | 1666 |
| 入库吞吐 | 9.22 篇/秒 |
| 检索延迟 | p50 52 ms |

**必须说明的边界**：fake provider 的向量是字符袋，召回数字只证明「链路通、吞吐够、隔离没破」，**不代表语义质量**。真实语义质量需配置 API Key 后运行 `scripts.eval`（recall@5 / MRR / no_answer 准确率）并用 `scripts/calibrate_threshold.py` 校准无答案阈值。

## 五、仍存在的限制与后续清单

按优先级排列，均不影响本地验收：

1. **前端 token 存 localStorage**，XSS 面偏大，应迁到 httpOnly cookie + CSRF token。
2. **中文稀疏召回用 `pg_trgm`**，未接 zhparser 分词，中文长尾词的 BM25 效果未验证。
3. **无分布式限流与配额预扣**，配额在请求后统计，极端并发下存在轻微超发窗口。
4. **评测集偏小**（单一领域模板语料），上线前应补真实业务语料与人工标注。
5. **无灰度/回滚编排**，迁移只能手工 `alembic downgrade`。
6. **审计日志无归档与导出**，长期运行需要按时间分区或转储。

## 六、复现命令

```bash
uv run alembic upgrade head
uv run pytest -q --cov=app            # 221 passed，覆盖率 87%
uv run ruff check app tests scripts worker
uv run python -m scripts.smoke --base http://127.0.0.1:8000
uv run python -m scripts.build_eval_corpus && uv run python -m scripts.eval
uv run python -m scripts.scale_check  # 300 篇规模校验
```
