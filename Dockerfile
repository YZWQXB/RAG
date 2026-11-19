FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
# EMBEDDING_LOCAL=1 时安装本地向量依赖（torch 等，约 2.5GB）。
# 默认不装：只调远程 embedding API 的部署不需要为此付出镜像体积。
# 构建示例：docker compose build --build-arg EMBEDDING_LOCAL=1 api worker
ARG EMBEDDING_LOCAL=0
RUN if [ "$EMBEDDING_LOCAL" = "1" ]; then uv sync --frozen --no-dev --extra local-embedding; else uv sync --frozen --no-dev; fi

COPY app ./app
COPY worker ./worker
COPY migrations ./migrations
COPY alembic.ini ./
COPY docker ./docker

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:8000/readyz || exit 1

# 单机场景：启动时串行执行迁移。上 K8s 时改为独立 initContainer。
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2"]
