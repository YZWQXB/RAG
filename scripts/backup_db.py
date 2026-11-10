"""PostgreSQL 备份。

用法：
    python -m scripts.backup_db                    # 备份到 data/backups，保留 7 份
    python -m scripts.backup_db --keep 14
    python -m scripts.backup_db --out D:/backup

要点：
- 用 `-Fc`（自定义格式）而不是纯 SQL：压缩率高，且支持 pg_restore 选择性恢复。
- 密码通过 `PGPASSWORD` 环境变量传给 pg_dump，**不出现在命令行**——命令行参数
  会被同机其他用户通过进程列表看到。
- 备份完立刻校验文件非空，避免"看起来成功、实际 0 字节"。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "data" / "backups"


def connection_params(database_url: str) -> dict[str, str]:
    """把 SQLAlchemy 风格 URL 拆成 pg_dump 需要的参数。"""
    raw = database_url.replace("postgresql+asyncpg://", "postgresql://")
    parsed = urlparse(raw)
    if not parsed.hostname or not parsed.path:
        raise ValueError("DATABASE_URL 格式无法识别")
    return {
        "host": parsed.hostname,
        "port": str(parsed.port or 5432),
        "user": unquote(parsed.username or "postgres"),
        "password": unquote(parsed.password or ""),
        "dbname": parsed.path.lstrip("/"),
    }


def backup_filename(dbname: str, now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"{dbname}-{stamp}.dump"


def prune(out_dir: Path, keep: int) -> list[Path]:
    """只保留最近 keep 份；返回被删除的文件。"""
    files = sorted(out_dir.glob("*.dump"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed: list[Path] = []
    for path in files[max(0, keep) :]:
        path.unlink(missing_ok=True)
        removed.append(path)
    return removed


def run_backup(database_url: str, out_dir: Path, keep: int = 7) -> Path:
    params = connection_params(database_url)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / backup_filename(params["dbname"])

    env = {**os.environ, "PGPASSWORD": params["password"]}
    command = [
        "pg_dump",
        "-h",
        params["host"],
        "-p",
        params["port"],
        "-U",
        params["user"],
        "-d",
        params["dbname"],
        "-Fc",
        "-f",
        str(target),
    ]
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"pg_dump 失败：{result.stderr.strip()[:300]}")

    if not target.exists() or target.stat().st_size == 0:
        raise RuntimeError("备份文件为空，可能磁盘已满")

    prune(out_dir, keep)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="PostgreSQL 备份")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="备份目录")
    parser.add_argument("--keep", type=int, default=7, help="保留份数")
    args = parser.parse_args()

    from app.core.config import settings

    url = settings.migration_database_url or settings.database_url
    try:
        path = run_backup(url, Path(args.out), args.keep)
    except Exception as exc:
        sys.exit(f"备份失败：{exc}")
    print(f"备份完成：{path} ({path.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
