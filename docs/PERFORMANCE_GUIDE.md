# 性能测试指南与基线报告（k6）

## 一、工具选型（为什么选 k6）

| 工具 | 语言 | 优势 | 劣势 | 适用 |
|---|---|---|---|---|
| **k6（Grafana）** | JavaScript | 阈值/阶梯加压/SSE 支持/CI 友好 | 无 GUI | ✅ 本项目首选 |
| JMeter | Java GUI | 老牌、插件全 | 重、脚本难维护 | 传统企业 |
| Locust | Python | 和后端同语言、易写复杂场景 | 单机并发弱于 k6 | Python 团队 |
| wrk / hey | C/Go | 极轻量、纯吞吐 | 无复杂场景编排 | 快速吞吐摸底 |
| Prometheus + Grafana | — | 持续观测（不是压测工具） | — | 生产监控标配 |

本项目特性（FastAPI 全异步 + SSE 流式 + 异步入库）决定选型：需要"阶梯加压找拐点"+ "流式响应判定" + "阈值断言"，k6 全覆盖，且脚本可进 git 进 CI。

## 二、安装与脚本（Windows）

winget/choco 均失败时直接下载二进制：

    Invoke-WebRequest -Uri "https://github.com/grafana/k6/releases/download/v1.4.0/k6-v1.4.0-windows-amd64.zip" -OutFile k6.zip
    Expand-Archive k6.zip -DestinationPath k6

脚本：`scripts/k6-rag-test.js`（已入 git）。

- setup 阶段为 5 个测试账号分别登录并绑定空间（处理 selection_required 多空间流程）
- 每个 VU 绑定一个账号（VU 序号取模分配），模拟真实多用户
- 混合负载：list_kbs（每轮）+ chat/stream（每 4 轮）
- 自定义指标：chat_full_duration、chat_success_rate

## 三、2026-11-21 基线数据（本机 Docker 全容器模式）

### 读路径（GET /api/v1/kbs）

| 并发 | p95 延迟 | 结论 |
|---|---|---|
| 5 VU | 19.7ms | 极快 |
| 10 VU | 13.1ms | 极快 |
| 20 VU | **9.16ms** | 反而更快（连接池热身后） |

**读路径结论**：20 并发下 p95 不到 10ms，瓶颈远未触及。读路径主要开销是每请求的成员关系复核（1 次索引查询）+ RLS 绑定，量级在个位数毫秒。

### 问答路径（POST /api/v1/chat/stream）

- 成功率受三重因素影响（都是**防护机制**而非故障）：
  1. `CHAT_RATE_LIMIT_PER_MINUTE=5` 按 user 限流——单账号压测必触发 429
  2. fake LLM 的 no_answer 也算成功，但 k6 对 SSE body 的判定需要流结束
  3. 拒答阈值（`RETRIEVAL_NO_ANSWER_THRESHOLD_FAKE=0.10`）会把弱相关查询判为拒答
- chat 全程延迟 avg 9.3ms（fake provider 无 LLM 网络耗时；真实 LLM 下延迟由上游决定）
- **问答路径的真正容量约束是：上游 LLM 的并发配额 + CHAT_RATE_LIMIT_PER_MINUTE**

### 系统稳定性

- 807 请求 / 20 VU / 2 分 20 秒：**0 次崩溃、0 次 5xx**
- 限流 429 表现为正常拒绝，连接池无泄漏（压测后 healthz 仍 200）

## 四、结论与容量评估

1. **读路径（浏览/检索列表）**：单机轻松扛 20+ 并发（p95 <10ms），瓶颈在数据库连接池（15 条），估算可支撑 50~100 并发用户
2. **问答路径**：容量 = min(上游 LLM 并发, 限流配置)。本地 fake 模式可扛全量；接真实 DeepSeek 后按其 RPS 配额评估
3. **入库路径**：受 `INGEST_MAX_CONCURRENT=2` 与 torch 推理限制，按文档大小每分钟约 1~3 篇（本地 bge-m3）

## 五、复现命令

    # 冒烟
    k6 run --vus 5 --duration 20s scripts/k6-rag-test.js
    # 完整阶梯（5→10→20 VU，2m20s）
    k6 run scripts/k6-rag-test.js --summary-export summary.json

## 六、下一步（可选）

- 接入 Grafana + Prometheus 持续观测（项目已有 /metrics 端点）
- 把 k6 加进 CI 做"性能回归门禁"（阈值不达标即失败）
- 用 `scripts/scale_check.py` 测入库吞吐与召回率（300~800 篇规模）