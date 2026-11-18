# 代码审查报告 · 2026-11-17

**范围**：app/（core + 9 个业务模块 + providers）、worker/、web/src、tests、CI、脚本
**方法**：逐文件阅读核心链路（认证/隔离/检索/生成/入库/调度），ruff 全量检查，pytest 收集 342 项全部通过导入
**结论**：整体质量**高**。无阻断性缺陷；发现 1 个一致性 bug（低危）、3 个可改进点。

---

## 1. 总体评价

| 维度 | 评价 |
|---|---|
| 可读性 | ★★★★★ 注释一律解释"为什么"（如 `SET LOCAL` vs `SET` 的连接池串号风险） |
| 架构 | ★★★★★ 模块边界清晰：core 无业务、modules 不碰基础设施、providers 全部可替换 |
| 安全 | ★★★★☆ 四道租户防线 + 每请求复核成员关系 + 404/403 语义区分；扣分项见下 |
| 测试 | ★★★★☆ 342 项、五层分层（unit/integration/isolation/security/eval）；隔离测试专门盯 RLS |
| 运维 | ★★★★☆ 双健康探针、metrics、审计、备份脚本、CI 评测回归 |

---

## 2. 亮点（值得学习的实现）

### 2.1 事务级 RLS 绑定与"提交后重绑"
`app/core/tenant.py` 用 `SELECT set_config('app.tenant_id', :tid, true)` 而非 `SET`：
第三个参数 true 使其事务级生效，连接归还池后无残留，杜绝跨租户串号。
`ingestion/pipeline/runner.py _commit_and_rebind()` 进一步处理了"多阶段提交后 GUC 失效"
的隐蔽坑——commit 结束事务后 RLS 会过滤所有行，必须重新绑定。

### 2.2 SSE 流式生成的心跳设计
`generation/service.py`：用生产者协程 + `asyncio.Queue` 消费上游流，空闲窗口插入
`: ping` 注释帧，防止 nginx / 企业代理按空闲超时切断长连接。用户点「停止」时
（`GeneratorExit`）用 `asyncio.shield` 保住已生成部分的落库——token 已花不能白丢。

### 2.3 事务边界与连接池意识
问答编排刻意拆成三段（检索短事务 → 无事务流式 → 落库短事务），向量推理这种
CPU 密集操作放到事务外——本地 bge-m3 单条 1~2 秒，放进事务会占死 15 条连接池。

### 2.4 查询改写的三道闸
`retrieval/rewrite.py`：门控（短句+指代词才改写）、超时（10s 到点放弃）、缓存
（hash 键，不留原文）。任何异常一律回退原句——"宁可检索不到，不能检索错"。
且只在原句检索失败后才触发，常规提问零额外延迟。

### 2.5 配置层生产护栏
`core/config.py _guard_production`：生产环境用默认 SECRET_KEY / fake embedding /
local storage 直接启动失败——把运行时事故前移到启动时。

### 2.6 令牌语义的正确理解
`api/v1/deps.py`：JWT 只证明"曾经是成员"，每请求复核成员关系并以库中角色为准，
成员被移除/降级后旧令牌立即失效。

---

## 3. 发现的问题

### P2 · 月度配额计数器的月键口径不一致
- **位置**：`worker/tasks/aggregate.py:76`（`rebuild_usage_counters`）vs `app/modules/quota/service.py:43`（`_month_key`）
- **问题**：前者用 UTC `strftime('%Y%m')`，后者用业务时区（Asia/Shanghai）。
  北京时间月底 16:00–24:00（UTC 已进入下月）窗口内执行重建，会把计数器写到
  **下一个月键**，导致当月用量瞬间"清零"、用户可超额。
- **建议**：重建逻辑复用 `quota.service._month_key`（可将其提为公共函数），并对
  `usage_records` 的 occurred_at 同样按业务时区归月。

### P3 · 声明了 arq 依赖但未使用
- **位置**：`pyproject.toml` dependencies 含 `arq`；`worker/main.py` 注释明确"刻意不引入 ARQ"。
- **影响**：无功能影响，但误导读者以为队列语义在 arq 上；也多装一个包。
- **建议**：从依赖移除，或注释指向 outbox 设计说明。

### P3 · Provider 工厂每次调用都构造新实例
- **位置**：`app/providers/__init__.py`
- **影响**：`OpenAICompatLLM` / `ApiBgeReranker` 只是读 settings 的轻量对象，可接受。
  但 `LocalBgeEmbedding` 靠模块级 `_loaded_models` 字典做模型缓存而非实例缓存，
  每次新实例的 `calls` 计数器形同虚设；若未来 provider 持有连接池，重复构造会成为问题。
- **建议**：工厂加按 (provider 类型) 的实例缓存；或至少删掉无意义的 `calls` 计数。

### P3 · SSE 前端解析的多行 data 拼接
- **位置**：`web/src/lib/sse.ts dispatch()`：`dataLine += line.slice(5).trim()`
- **问题**：SSE 规范允许一个事件多条 `data:` 行（应按换行连接）；`.trim()` 还会吞掉
  首尾空白。当前后端 `json.dumps` 单行输出不受影响，属于潜在兼容性问题而非缺陷。
- **建议**：按规范以 `\n` 连接多行 data。

### P4 · 检索两路召回顺序执行
- **位置**：`retrieval/service.py` 顶部注释已自我说明：AsyncSession 非并发安全，
  当前顺序执行本地 <100ms。属于**已知的合理取舍**，记录在案即可。

---

## 4. 安全审查清单

| 项 | 状态 | 说明 |
|---|---|---|
| 密码哈希 | ✅ | Argon2id（argon2-cffi 默认参数） |
| JWT | ✅ | HS256 + 最小声明；每请求复核成员关系 |
| 租户隔离 | ✅ | 四道防线；isolation 测试目录专测 RLS/池复用/并发 |
| 限流 | ✅ | 登录/注册按 IP，问答按 user_id，先限流后查库 |
| 上传防护 | ✅ | 50MB 大小 + 200MB 解压上限 + 类型白名单 |
| 404/403 语义 | ✅ | 跨租户 404（不泄露存在性），无权 403 |
| 日志脱敏 | ✅ | Redis 缓存键全哈希，不留查询原文 |
| metrics | ✅ | 只聚合量；生产可关 |
| 调试接口 | ✅ | enable_debug_api 开关，生产默认关 |
| 生产护栏 | ✅ | 默认 SECRET / fake / local storage 启动即拒 |
| 依赖供应链 | ⚠️ | uv.lock 已锁定；建议 CI 加 dependabot/审计步骤（未配置） |

---

## 5. 垃圾文件清理记录（2026-11-17）

| 已删除 | 说明 |
|---|---|
| `nul` | Windows 重定向残留（34 字节，保留名文件，需 `\\?\` 前缀删除） |
| `.pytest_cache/` | pytest 缓存 |
| `.ruff_cache/` | ruff 缓存 |
| `.tmp-test-storage/`（0.8MB，68 个租户目录） | 测试运行时临时存储 |
| `web/tsconfig.tsbuildinfo` | tsc 增量构建缓存 |
| 25 个 `__pycache__/` 目录（排除 .venv） | Python 字节码缓存 |
| `data/logs/*.log` 内容清空 | 运行日志（文件保留，进程续写） |

**保留项（不是垃圾）**：
- `data/models/`（2.2GB）— bge-m3 模型权重，删了要重新下载 2.2GB
- `data/storage/` — 用户上传的真实文档（业务数据）
- `web/dist/` — 前端构建产物（docker compose web 服务直接用）
- `.env.mock-backup` / `.env.offline-backup` — 可切换的配置档案（已被 .gitignore 覆盖，不会入库）
- `.workbuddy/` — 本地 AI 助手工作区（已 gitignore）

所有删除目标均确认在 .gitignore 覆盖范围内（`git status --ignored` 验证），git 跟踪文件零改动。
