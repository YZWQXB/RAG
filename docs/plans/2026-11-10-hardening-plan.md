# 2026-11-10 加固计划（四批）

依据 2026-11-10 代码审查结论整理。每批完成后跑全量测试，并记录到 `.workbuddy/memory`。

> 修正：审查中「JWT 角色不回查」一项为误判。`app/api/v1/deps.py::get_principal`
> 每请求复核 membership 并以库中角色为准，`tests/security/test_auth_hardening.py`
> 已覆盖「成员移除后旧令牌 401」「降权后按新角色放行」，该项不进入计划。

## 第一批：阻断上线（P0）

| 编号 | 事项 | 目标 | 涉及范围 | 验收 |
|---|---|---|---|---|
| 1.1 | 本地向量依赖未声明 | 新环境 `uv sync` 后切 `local` 不因 ImportError 崩溃，且报错可读 | `pyproject.toml`、`app/providers/embedding/local_bge.py` | 缺少依赖时抛 `EmbeddingError` 并提示安装 extras |
| 1.2 | 密码强度为零 + 注册无限流 | 任意字符串不能注册；批量注册被按 IP 限速 | `app/modules/iam/schemas.py`、`app/api/v1/auth.py`、`config.py` | 弱口令 400；同 IP 超限 429 且带 Retry-After |
| 1.3 | 拒答只看稠密分 | 稀疏命中不再被误拒；fake 向量用独立阈值 | `retrieval/types.py`、`retrieval/service.py`、`generation/service.py`、`config.py` | 稠密低但稀疏高的问答可作答；fake 下阈值 0.25 |

## 第二批：一致性与安全（P1）

| 编号 | 事项 | 目标 | 涉及范围 | 验收 |
|---|---|---|---|---|
| 2.1 | 停止生成不落库 | 客户端断开时保存已生成内容并标记 `incomplete` | `generation/service.py` | 关闭生成器后库中存在 incomplete 消息 |
| 2.2 | 事务内做 embedding | 一次问答不再把 DB 连接占用 1~2 秒 | `generation/service.py::_prepare`、`retrieval/service.py` | 嵌入在事务外完成，事务只含 DB 操作 |
| 2.3 | 模式切换无审计 | 记录谁在何时把系统切到离线/在线 | `modules/system/router.py`、`core/mode.py` | 审计表出现 `mode.switch` 记录 |

## 第三批：可运营（P1→P2）

| 编号 | 事项 | 目标 | 涉及范围 |
|---|---|---|---|
| 3.1 | 无指标端点 | 暴露 QPS/429/延迟/入库积压 | `app/main.py`、新增 `app/core/metrics.py` |
| 3.2 | 会话与消息无分页 | 列表与历史支持 limit/cursor | `conversation/router.py`、`service.py`、前端 |
| 3.3 | CI 不跑评测 | 换模型/改阈值能自动发现退化 | `.github/workflows/ci.yml`、`scripts/calibrate_threshold.py` |
| 3.4 | 审计覆盖不全 | 登录、成员变更、配额超限入审计 | `api/v1/auth.py`、`iam`、`quota` |

## 第四批：对外交付（P2）

| 编号 | 事项 | 目标 |
|---|---|---|
| 4.1 | 账号生命周期 | 邮箱验证、找回密码、refresh token |
| 4.2 | 会话正文搜索 | 搜索从标题扩展到消息内容 |
| 4.3 | bad case 视图 | 点踩数据可视化，支持导出 |
| 4.4 | 文档运维 | 替换/重命名/批量上传、扫描件 OCR |

## 执行顺序与优先级

1. 第一批 → 先跑通「新机器可部署 + 账号不可被刷 + 拒答不漏」
2. 第二批 → 修正确性与资源占用，成本低、收益直接
3. 第三批 → 出问题能看见；分页与指标优先于评测
4. 第四批 → 需产品决策，逐项确认后再做

每批完成标准：`pytest` 全绿、`ruff` 全清、前端 `tsc`/`build` 通过、实机验证关键路径。

## 执行状态

### 第一批 —— 已完成（2026-11-10）

| 编号 | 落地内容 | 验证 |
|---|---|---|
| 1.1 | `pyproject.toml` 新增 `local-embedding` extras（torch / sentence-transformers）；`uv lock` 已更新；报错文案改为 `uv sync --extra local-embedding` | `test_missing_dependency_message_is_actionable` |
| 1.2 | `security.validate_password_strength`（≥8 位、字母+数字、≥4 种字符）；注册按 IP 限流 5 次/分 | `tests/security/test_password_policy.py` 8 项；实机弱口令 400 / 强口令 201 |
| 1.3 | `RetrievalResult.top_sparse_score`；`retrieval.answerable()` 双通道判定；新增 `RETRIEVAL_NO_ANSWER_THRESHOLD_FAKE=0.25`、`RETRIEVAL_SPARSE_ANSWER_THRESHOLD=0.35` | `tests/unit/test_answer_gate.py` 5 项 |

### 第二批 —— 已完成（2026-11-10）

| 编号 | 落地内容 | 验证 |
|---|---|---|
| 2.1 | `stream_answer` 捕获 `CancelledError/GeneratorExit`，`asyncio.shield` 持久化 `incomplete` 消息 | `test_client_disconnect_persists_partial_answer` |
| 2.2 | `_prepare` 拆成两个短事务，向量计算移到事务外；`retrieve()` 新增 `vector` 参数 | 全量回归 + `test_chat_stream` |
| 2.3 | `POST /system/mode` 写 `mode.switch` 审计（记录 from/to） | `test_mode_switch_writes_audit` / viewer 403 |

结果：`pytest` 301 passed，`ruff` 全清；API 已重启并实机验证密码策略。

### 第三批 —— 待开始

3.1 指标端点 → 3.2 会话分页 → 3.3 CI 评测 → 3.4 审计补全。

### 第四批 —— 待开始（需产品确认）

4.1 账号生命周期 → 4.2 正文搜索 → 4.3 bad case 视图 → 4.4 文档运维。

### 第三批 —— 已完成（2026-11-10）

| 编号 | 落地内容 | 验证 |
|---|---|---|
| 3.1 | `app/core/metrics.py`：请求计数/延迟直方图（路由按 UUID 归一化）、问答结果分布、outbox 积压与解析中文档数；`GET /metrics`（`METRICS_ENABLED` 可关） | `tests/integration/test_metrics_endpoint.py` 4 项；实机抓取到 `rag_http_requests_total{route="/api/v1/kbs"}` |
| 3.2 | 会话列表 `limit/offset`（默认 100/上限 200）；消息改为「倒序取 N 再翻转」，默认最近 500 条 | 分页与顺序用例 2 项；实机 `limit=1` 返回 1 条 |
| 3.3 | `scripts.calibrate_threshold --check`（分离度 <0.85、阈值越界、与生效阈值偏离 >0.15 均失败）；CI 新增 `eval` 任务 | 本地以测试库 + 假向量跑通：accuracy=0.9524、drift=0、`[check] ok` |
| 3.4 | 登录成功写 `auth.login`；成员增改删分别写 `member.add/update/remove` | 审计用例 2 项 |

**未纳入本轮**：配额超限审计（需在 `assert_quota` 处注入 principal，改动面较大，随第四批一起做）。

## 第四批的已确认约束

- **找回密码**：界面已交付（`web/src/pages/ForgotPassword.tsx`，路由 `/forgot-password`），
  **暂未接入任何后端逻辑**；`获取验证码` 只做本地倒计时，任何 6 位数字可进入下一步，
  页面底部有「界面预览」提示。后续接入时的接口契约建议：
  `POST /auth/password/send-code`、`POST /auth/password/verify`、`POST /auth/password/reset`。
- **文档替换必须保留历史版本**（用户 2026-11-10 明确要求）：替换文档时旧版本不得物理删除，
  `document_versions` 保留全部版本，检索只使用当前版本，历史版本可在界面回溯与下载。
  实现前需确认：旧版本向量是否继续参与检索（建议不参与）、保留份数上限（建议不限但可手动清理）。

## 修订：1.3 拒答判定（2026-11-10 晚）

用户反馈「离线同样拒答」，实测确认**上一版没解决问题**：真正的病因不在阈值，而在向量空间。

实测（用户 958 块语料，`EMBEDDING_PROVIDER=fake`）：

| 查询 | 旧：dense | 旧：sparse | 旧判定 |
|---|---|---|---|
| 什么是递归 | 0.0528 | 0.0000 | 拒答 |
| 年假有多少天 | 0.0663 | 0.0000 | 拒答 |
| TCP 三次握手的过程 | 0.0521 | 0.1569 | 拒答 |

两个根因：
1. **库里分块是 bge-m3 向量，查询是字符袋向量**，两者不在同一空间，余弦恒在 0.05，阈值降到 0 都没用
2. **pg_trgm 对中文几乎无效**：trigram 需要连续三字重合，「年假有多少天」与「入职满一年 5 天年假」只共享 2 个字

新方案：**离线不碰向量**，新增 `app/modules/retrieval/lexical.py`，逐块比文本。
打分 = 字符袋余弦 × 查询覆盖率（剔除虚词、标点、数字）。
实测分离度：应作答 0.12~0.43，应拒答最高 0.026 → 离线阈值取 **0.10**。

| 查询 | 新分数 | 判定 |
|---|---|---|
| 什么是递归 | 0.261 | 作答 |
| TCP 三次握手的过程 | 0.419 | 作答 |
| 指针和引用有什么区别 | 0.453 | 作答 |
| 什么是多态 | 0.342 | 作答 |
| 红烧肉怎么做 | 0.028 | 拒答 |
| 今天天气怎么样 | 0.018 | 拒答 |

代价与边界：离线是**字面匹配**，同义改写（「汽车」vs「轿车」）召回不到；
排序质量不如语义检索；`offline_scan_limit=2000` 限制单次扫描块数，大库需要缩小范围。

## 离线回答格式约定（2026-11-10）

```
根据资料，「{问题}」的参考内容如下：

- {摘录} [1]
- {摘录} [2]

> 离线模式：以上为知识库原文摘录，未经模型归纳生成。
```

规则（实现见 `app/providers/llm/fake.py`）：
- 最多 3 条，按「与问题的字符覆盖率」筛选并排序，明显无关的参考块不展示
- 摘录剔除分块自带的「[文档: … | 章节: …]」头部、表格分隔行、空列表标记
- 引用编号沿用参考资料真实编号，前端可正常解析跳转
- 单条摘录上限 120 字，超长以 … 结尾
- 结尾固定「离线模式」声明，让用户能区分「原文摘录」与「模型生成」

## 引用原文的 Markdown 渲染（2026-11-10）

- 弹窗改为复用 `web/src/components/Markdown.tsx`，与回答同一套渲染规则
- 渲染前去掉分块头部与空标记行（`stripChunkHeader`）
- 新增 `remark-cjk-friendly`：中文 `**加粗：**正文` 按 CommonMark 不构成强调
  （闭合符前是中文标点、后是汉字，不满足 right-flanking），不加插件会显示成字面 `**`
