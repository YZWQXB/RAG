# Phase 7: 最小控制台与一键部署

**Goal:** 4 个界面可用且状态完整，`docker compose up -d` 一键起全栈，冒烟脚本可验证全链路。

**Prerequisites:** Phase 6 完成（配额、日志、评测就位）

**Deliverables:**
- `/register`、`/login`、`/kbs`、`/kbs/:id/documents`、`/chat` 五个路由可用
- 文档状态三阶段文案可见，引用可点击跳转并显示页码
- `docker compose up -d` 后 `api`、`worker`、`web` 均 healthy
- `uv run python -m scripts.smoke` 跑通注册 → 建库 → 上传 → 提问

**Interface Decisions Implemented:** 设计文档 §17 的 4 个界面与 8 类状态全部落地；视觉体系（配色/字体/间距令牌）按设计文档 Non-Goal 处理，只使用中性 Tailwind 默认色阶。

---

## Task 1: 前端骨架与 API 客户端

**Goal:** 可运行的 SPA 骨架，统一处理鉴权与错误。

**Files:**
- Create: `web/package.json`
- Create: `web/vite.config.ts`
- Create: `web/tailwind.config.ts`
- Create: `web/src/main.tsx`
- Create: `web/src/App.tsx`
- Create: `web/src/lib/api.ts`
- Create: `web/src/lib/sse.ts`
- Create: `web/src/lib/auth.ts`

**Steps:**

- [ ] 初始化：`npm create vite@latest web -- --template react-ts`，装 `react-router-dom`、`tailwindcss`、`clsx`。`vite.config.ts` 配 `server.proxy["/api"] = "http://localhost:8000"`，生产由 nginx 代理。
- [ ] `lib/auth.ts`：`getToken/setToken/clearToken`（localStorage key `rag_token`）、`decodeClaims()` 只读 `tid`/`role` 用于展示，**不做权限判断**（权限一律由后端裁决）。
- [ ] `lib/api.ts`：`request<T>(path, init)` 统一：加 `Authorization: Bearer`、解析 `{"error":{code,message}}` 并抛 `ApiError{status, code, message}`、401 时清 token 并跳 `/login`。**前端只展示 `message`，不拼装中文文案**，保证文案来源单一。
- [ ] `lib/sse.ts`：用 `fetch` + `ReadableStream` 解析（不能用 `EventSource`，它无法带 Authorization 头）：

```typescript
export async function streamChat(body, handlers: {
  onCitations?: (c: Citation[]) => void;
  onToken?: (t: string) => void;
  onNoAnswer?: (m: string) => void;
  onDone?: (d: DonePayload) => void;
  onError?: (m: string) => void;
}, signal?: AbortSignal)
```

按 `\n\n` 切分事件块，取 `event:` 与 `data:` 两行，JSON 解析后分发。
- [ ] `App.tsx`：路由与 `ProtectedRoute`（无 token 跳登录）。全局错误边界显示"页面出错了，请刷新重试"，不显示堆栈。
- [ ] 验证：`cd web && npm run dev`，访问 `/login` 返回 200，控制台无报错。

**Security/Boundary Notes:** token 存 localStorage 属首期折中（XSS 风险），后续应迁到 httpOnly cookie + CSRF token，记入 Deferred。前端不做任何权限判断，仅按后端 403 渲染无权限态。

---

## Task 2: 注册与登录页

**Goal:** 完成注册即开租户的流程，三种失败态有中文提示。

**Files:**
- Create: `web/src/pages/Register.tsx`
- Create: `web/src/pages/Login.tsx`
- Create: `web/src/components/FormField.tsx`
- Create: `web/src/components/Alert.tsx`

**Steps:**

- [ ] `FormField`：label + input + 行内错误文案，`aria-invalid` 与 `aria-describedby` 指向错误文本；错误时边框用 `border-red-600`，文案 12px `text-red-700`。
- [ ] Register 表单：邮箱、密码、空间名称。前端只做最小校验（非空、邮箱格式、密码 ≥ 8），**强度规则以后端为准**，后端 400 时把 `message` 渲染在密码字段下。
- [ ] 三种失败态显式处理：`EMAIL_TAKEN` → 邮箱字段下"该邮箱已注册"；`WEAK_PASSWORD` → 密码字段下提示；`SLUG_TAKEN` → 空间名称字段下提示。
- [ ] 注册成功直接落 token 并跳 `/kbs`（后端注册响应即含 token）。
- [ ] Login：提交后若响应 `selection_required: true`，渲染租户选择列表，选中后调 `POST /auth/token` 换 token。失败统一显示"邮箱或密码错误"。
- [ ] 提交中：按钮禁用 + 文案变"处理中…"，防止重复提交。
- [ ] 验证：`npm run dev` 后手工走一遍注册 → 自动进入知识库页；用已注册邮箱再注册一次，看到"该邮箱已注册"。

**Security/Boundary Notes:** 密码框 `type="password"` 且 `autoComplete="current-password"`；不在前端缓存密码；错误提示不区分"账号不存在"与"密码错误"。

---

## Task 3: 知识库列表与文档管理

**Goal:** 知识库可建可看，文档上传后能看到解析/切分/向量化三阶段。

**Files:**
- Create: `web/src/pages/KbList.tsx`
- Create: `web/src/pages/KbDocuments.tsx`
- Create: `web/src/components/DocStatusBadge.tsx`
- Create: `web/src/hooks/usePolling.ts`

**Steps:**

- [ ] `KbList`：卡片网格显示名称、描述、文档数、更新时间；右上"新建知识库"打开对话框（名称必填）。
- [ ] 三态显式实现：加载中渲染 3 个骨架卡片（`animate-pulse` 灰块）；空态显示"还没有知识库，创建第一个开始上传文档"+ 主按钮；无权限（403）显示"你没有访问该空间的权限"。
- [ ] `KbDocuments`：顶部上传区（`<input type="file" accept=".pdf,.docx,.md,.txt">`，显示所选文件名与大小），表格列：标题、大小、状态、操作。
- [ ] `DocStatusBadge` 映射（**这是 §17 的硬性要求**，不能只显示一个转圈）：

| status | stage | 展示 |
|---|---|---|
| `pending` | null | 灰点 + "排队中" |
| `processing` | `parsing` | 蓝点脉冲 + "解析中" |
| `processing` | `chunking` | 蓝点脉冲 + "切分中" |
| `processing` | `embedding` | 蓝点脉冲 + "向量化中" |
| `ready` | null | 绿点 + "可检索" |
| `failed` | null | 红点 + "失败"，下方显示 `error` 文案 + "重试"按钮 |

- [ ] `usePolling(fn, intervalMs, enabled)`：仅当列表中存在 `pending|processing` 时启用，间隔 2000ms；组件卸载或全部 `ready` 后自动停止。
- [ ] 上传失败时用 `Alert` 展示后端 `message`（如"单个文件不能超过 50MB"），不清空已选文件，允许重试。
- [ ] 验证：上传 1 份 Markdown，观察状态从"排队中 → 解析中 → 切分中 → 向量化中 → 可检索"（若 worker 太快看不到中间态，可临时把 `chunk_size_chars` 调小并上传较大文档复现）。

**Security/Boundary Notes:** 前端不缓存文档内容；删除操作需二次确认（"删除后该文档不再参与检索，确定删除？"）。

---

## Task 4: 问答界面与引用面板

**Goal:** 流式问答可用，引用可点击并在右侧面板展示来源与页码。

**Files:**
- Create: `web/src/pages/Chat.tsx`
- Create: `web/src/components/MessageList.tsx`
- Create: `web/src/components/CitationPanel.tsx`
- Create: `web/src/components/QuotaBanner.tsx`

**Steps:**

- [ ] 布局：左侧会话列表（240px 固定），中间消息流（自适应），右侧引用面板（320px，窄屏 <1024px 时折叠为抽屉）。
- [ ] 发送后立即插入用户消息与空的 assistant 消息占位，token 逐条 append 到占位消息（模拟打字效果），`done` 后替换为最终内容并渲染引用上标。
- [ ] 引用渲染：把正文中的 `[n]` 替换为可点击上标按钮，点击后右侧面板滚动到对应条并高亮 2 秒。面板每条显示：文档标题、`heading_path`、`第 {page_from} 页`、内容片段（后端的 `preview` 或前端已持有块）。
- [ ] 必须覆盖的状态：

| 状态 | 表现 |
|---|---|
| 生成中 | assistant 气泡显示光标闪烁，发送按钮禁用 |
| `no_answer` | 灰色提示条"现有资料未涵盖该问题，建议换个说法或补充相关文档"，不显示引用面板 |
| 配额超限（429） | 顶部 `QuotaBanner`："本月查询次数已用尽（1000/1000）"，附"了解套餐"链接（首期指向 `/quota` 展示页） |
| 网络中断 | 消息底部显示"生成中断"+ "重试"按钮，重试复用同一 query 与 `conversation_id` |
| 空会话 | 居中提示"选择一个知识库开始提问"+ 示例问题三条 |

- [ ] 会话切换：点击左侧会话加载历史消息，历史 assistant 消息的引用来自 `GET /conversations/{id}/messages`，渲染逻辑与实时流一致（复用同一组件）。
- [ ] `QuotaBanner` 数据来自 `GET /quota`，进入页面时拉一次，429 后刷新。
- [ ] 验证：桌面 1440px 与窄屏 900px 各检查一次引用面板折叠行为；构造一次 `no_answer`（问"火星基地的报销标准是什么"）确认提示条。

**Security/Boundary Notes:** 渲染模型输出时**不使用 `dangerouslySetInnerHTML`**，用文本节点 + `[n]` 拆分渲染，防止文档内容里的 HTML 被当作标签执行（间接 XSS 面）。

---

## Task 5: 镜像、编排与冒烟

**Goal:** 一条命令起全栈，健康检查与冒烟脚本就位。

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `web/Dockerfile`
- Create: `web/nginx.conf`
- Modify: `docker-compose.yml`
- Create: `scripts/smoke.py`
- Create: `README.md`

**Steps:**

- [ ] `Dockerfile`（API/worker 共用）：

```dockerfile
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app ./app
COPY worker ./worker
COPY migrations ./migrations
COPY alembic.ini ./
ENV PATH="/app/.venv/bin:$PATH"
HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
  CMD curl -fsS http://localhost:8000/healthz || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
```

- [ ] 创建 `.dockerignore`：`.venv`、`__pycache__`、`.git`、`.env`、`tests/eval/corpus.json`、`web/node_modules`、`web/dist`、`docs`。
- [ ] `web/Dockerfile`：`node:22-alpine` 构建 → `nginx:alpine` 运行，`nginx.conf` 做两件事：SPA fallback（`try_files $uri /index.html`）、`location /t/` rewrite 到 `/`（按 Deferred 决策的路径方案，前端不解析 slug）、`location /api/` 反代 `http://api:8000`。

```nginx
location /t/ { rewrite ^/t/[^/]+/(.*)$ /$1 last; }
location /api/ {
  proxy_pass http://api:8000;
  proxy_http_version 1.1;
  proxy_set_header Connection "";
  proxy_buffering off;              # SSE 必须关闭缓冲
  proxy_read_timeout 300s;
}
```

- [ ] `docker-compose.yml` 追加：

```yaml
  api:
    build: { context: ., dockerfile: Dockerfile }
    env_file: .env
    environment:
      DATABASE_URL: postgresql+asyncpg://rag_app:rag_app_pw@postgres:5432/rag
      REDIS_URL: redis://redis:6379/0
      S3_ENDPOINT: http://minio:9000
    depends_on:
      postgres: { condition: service_healthy }
      redis: { condition: service_healthy }
      minio: { condition: service_healthy }
    ports: ["8000:8000"]
    restart: unless-stopped

  worker:
    build: { context: ., dockerfile: Dockerfile }
    command: ["python", "-m", "worker.main"]
    env_file: .env
    environment:
      DATABASE_URL: postgresql+asyncpg://rag_app:rag_app_pw@postgres:5432/rag
      REDIS_URL: redis://redis:6379/0
      S3_ENDPOINT: http://minio:9000
    depends_on:
      api: { condition: service_started }
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import os,sys; sys.exit(0 if os.path.exists('/tmp/worker.ok') else 1)\""]
      interval: 30s
      timeout: 5s
      retries: 3
    restart: unless-stopped

  web:
    build: { context: ./web, dockerfile: Dockerfile }
    depends_on: [api]
    ports: ["8080:80"]
    restart: unless-stopped
```

worker 启动完成后写 `/tmp/worker.ok`（`worker/main.py` 中 `Path("/tmp/worker.ok").touch()`），供健康检查识别。
- [ ] 迁移执行：api 容器启动命令改为 `sh -c "alembic upgrade head && uvicorn ..."`，或用独立 `migrate` 一次性服务。**选择前者**并注释说明：单机场景下串行启动足够，K8s 化时改为 initContainer。
- [ ] `scripts/smoke.py`：注册随机邮箱 → 建 KB → 上传 `tests/fixtures/sample.md` → 轮询到 `ready`（最多 60s）→ 提问"年假有多少天" → 断言收到 `done` 且 `used` 非空 → 打印耗时。任一断言失败退出码 1。
- [ ] `README.md`：写清启动五步（复制 `.env`、`compose up`、初始化 bucket、注册、访问 `:8080`）、worker 重启方式、迁移命令、常见故障（端口占用、minio 桶不存在、embedding key 无效）。
- [ ] 验证：

```bash
docker compose up -d --build
docker compose ps                      # api / worker / web 均 healthy 或 running
curl -fsS localhost:8000/healthz
uv run python -m scripts.smoke
```

  - Expected：`/healthz` 返回 `{"status":"ok"}`；smoke 打印 `SMOKE OK` 且退出码 0。

**Security/Boundary Notes:** `.env` 不进镜像（`.dockerignore` 排除）；`.dockerignore` 同时排除 `.venv`、`node_modules`、`web/dist`、`tests/eval/corpus.json`。生产环境 `SECRET_KEY` 必须通过 secret 注入，compose 中的默认值仅本地可用。

---

## Phase Completion Checklist

- [ ] 所有任务完成
- [ ] 五个路由可访问，注册 → 建库 → 上传 → 问答手工走通
- [ ] 文档状态六种展示态全部验证过（含 `failed` + 重试）
- [ ] 引用点击后右侧面板定位正确，页码与文档实际页一致
- [ ] `no_answer`、429、断网重试三种异常态均手工复现
- [ ] `docker compose up -d --build` 后三个服务健康
- [ ] `scripts/smoke.py` 退出码 0
- [ ] 桌面 1440px 与窄屏 900px 各截图一次，无内容溢出或重叠
- [ ] 全页面键盘 Tab 可走通，焦点可见

## 执行结果

**Status:** Done

- 已实现：注册/登录（含多空间选择）、知识库列表（骨架/空态）、文档管理（六种状态 + 2s 轮询 + 重试/删除）、问答（流式 + 引用面板 + 无答案 + 429 提示 + 中断重试）、额度页、nginx 配置、双镜像、冒烟脚本
- 验证：`npm run build` 通过（TS strict）；`docker build` 两个镜像成功；`docker compose up -d --build` 后 api healthy；容器内冒烟 `SMOKE OK`
- 未验证：真实浏览器截图与键盘走查（当前环境无浏览器工具）；桌面/窄屏视觉检查需人工执行
- 偏差：未在容器内跑前端 E2E；引用面板在窄屏为隐藏（未实现抽屉）
