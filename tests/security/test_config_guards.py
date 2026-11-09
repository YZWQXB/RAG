"""生产环境的启动期约束。

这些约束在 config 校验阶段生效，路由与引擎都还没建起来，
因此用子进程按真实环境变量装配一次来验证，避免被单例缓存干扰。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

BASE_PROD = {
    "ENV": "production",
    "SECRET_KEY": "x" * 40,
    "APP_BASE_URL": "https://example.com",
    "EMBEDDING_PROVIDER": "api",
    "LLM_PROVIDER": "api",
    "RERANKER_PROVIDER": "api",
}


def _boot(overrides: dict[str, str]) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(BASE_PROD)
    env.update(overrides)
    return subprocess.run(
        [sys.executable, "-c", "import app.core.config as c; c.get_settings()"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def test_production_rejects_local_storage():
    result = _boot({"STORAGE_BACKEND": "local"})
    assert result.returncode != 0
    assert "存储" in result.stderr or "storage" in result.stderr.lower()


def test_production_accepts_s3_storage():
    result = _boot({"STORAGE_BACKEND": "s3"})
    assert result.returncode == 0, result.stderr


def test_production_rejects_default_secret():
    result = _boot({"STORAGE_BACKEND": "s3", "SECRET_KEY": "dev-only-secret-change-in-production"})
    assert result.returncode != 0
