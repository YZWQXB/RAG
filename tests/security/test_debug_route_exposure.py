"""路由装配层面的安全约束。

调试接口会返回 chunk 预览、各路召回分数与耗时，属于信息暴露面，
生产环境必须不存在，即使有人把 ENABLE_DEBUG_API 打开。
路由集合在模块导入时就固定了，所以用子进程按真实环境变量装配一次来验证。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

PROBE = (
    "from app.main import create_app;"
    "print('ROUTES:' + '|'.join(sorted(r.path for r in create_app().routes if hasattr(r, 'path'))))"
)


def _routes_with(env_overrides: dict[str, str]) -> str:
    env = dict(os.environ)
    env.update(env_overrides)
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return next(line for line in result.stdout.splitlines() if line.startswith("ROUTES:"))


def test_debug_route_mounted_in_local_env():
    routes = _routes_with({"ENV": "local", "ENABLE_DEBUG_API": "true"})
    assert "/api/v1/debug/retrieve" in routes


def test_debug_route_absent_in_production_even_when_enabled():
    routes = _routes_with(
        {
            "ENV": "production",
            "ENABLE_DEBUG_API": "true",
            "SECRET_KEY": "x" * 40,
            "APP_BASE_URL": "https://example.com",
            # 生产环境有 provider 白名单，必须显式给真实实现，否则启动即被拒
            "EMBEDDING_PROVIDER": "api",
            "LLM_PROVIDER": "api",
            "RERANKER_PROVIDER": "api",
            # 生产环境同样禁止本地存储，否则 config 校验会先一步失败
            "STORAGE_BACKEND": "s3",
        }
    )
    assert "/api/v1/debug/retrieve" not in routes, "生产环境不得暴露调试检索接口"
