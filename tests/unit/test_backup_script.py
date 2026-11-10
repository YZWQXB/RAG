"""备份脚本的纯函数部分（不依赖 pg_dump 是否安装）。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from scripts.backup_db import backup_filename, connection_params, prune


def test_connection_params_strips_async_driver():
    params = connection_params("postgresql+asyncpg://rag_app:p%40ss@localhost:5433/rag")
    assert params == {
        "host": "localhost",
        "port": "5433",
        "user": "rag_app",
        "password": "p@ss",
        "dbname": "rag",
    }


def test_connection_params_defaults_port():
    params = connection_params("postgresql://u:p@db/rag")
    assert params["port"] == "5432"


def test_connection_params_rejects_garbage():
    with pytest.raises(ValueError):
        connection_params("sqlite:///tmp/x.db")


def test_backup_filename_has_timestamp():
    name = backup_filename("rag", datetime(2026, 11, 10, 12, 30, 5))
    assert name == "rag-20261110-123005.dump"


def test_prune_keeps_newest(tmp_path: Path):
    for index in range(5):
        path = tmp_path / f"rag-2026110{index}-000000.dump"
        path.write_bytes(b"x")
        # 用 mtime 排序，显式拉开差异
        import os

        os.utime(path, (1000 + index, 1000 + index))

    removed = prune(tmp_path, keep=2)

    remaining = sorted(p.name for p in tmp_path.glob("*.dump"))
    assert len(remaining) == 2
    assert remaining == ["rag-20261103-000000.dump", "rag-20261104-000000.dump"]
    assert len(removed) == 3
