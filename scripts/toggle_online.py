"""在线/离线/本地三种模式一键切换（.env Provider 开关）。

用法：
    python scripts/toggle_online.py --on      # 离线 -> 全部指向本地模拟服务（接线验证）
    python scripts/toggle_online.py --local   # 本地向量 + 真实 DeepSeek 生成（推荐的真实在线模式）
    python scripts/toggle_online.py --off     # 恢复离线模式（fake）
    python scripts/toggle_online.py --status  # 查看当前配置

--on 会把三个 BASE_URL 指向 http://127.0.0.1:8001/v1，只能配合
scripts/mock_online_provider.py 使用；检测到真实 Key 时默认拒绝覆盖，需要 --force。

--local 保留 LLM 段的真实 Key，只把向量化换成本机 bge-m3，并关闭重排
（bge-reranker 同样是另一个 2GB 级模型，需要时再单独开启）。

切换后必须重启 API 与 worker 才生效（配置在进程启动时读取）。
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
BACKUP_PATH = ENV_PATH.parent / ".env.offline-backup"

MOCK_BASE = "http://127.0.0.1:8001/v1"
MOCK_KEY = "test-key-online"

LINES = (
    "EMBEDDING_PROVIDER",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_API_KEY",
    "EMBEDDING_MODEL",
    "LLM_PROVIDER",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "RERANKER_PROVIDER",
    "RERANKER_BASE_URL",
    "RERANKER_API_KEY",
    "RETRIEVAL_RERANK_ENABLED",
)

# 形如 sk-xxxx 且不是占位值的，视为真实 Key
_REAL_KEY = re.compile(r"^sk-[A-Za-z0-9]{16,}$")


def read_env() -> dict[str, str]:
    result: dict[str, str] = {}
    if not ENV_PATH.exists():
        sys.exit(f"未找到 {ENV_PATH}")
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z_]+)=(.*)$", raw)
        if m:
            result[m.group(1)] = m.group(2)
    return result


def write_env(values: dict[str, str]) -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    out: list[str] = []
    for raw in lines:
        m = re.match(r"^([A-Z_]+)=(.*)$", raw)
        if m and m.group(1) in values:
            out.append(f"{m.group(1)}={values[m.group(1)]}")
            seen.add(m.group(1))
        else:
            out.append(raw)
    for key, value in values.items():
        if key not in seen:
            out.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


def _backup_once() -> None:
    if BACKUP_PATH.exists():
        print(f"发现已有备份 {BACKUP_PATH}，跳过备份（上次切换未 --off？）")
    else:
        shutil.copyfile(ENV_PATH, BACKUP_PATH)
        print(f"已备份当前配置 -> {BACKUP_PATH}")


def _has_real_key(env: dict[str, str]) -> bool:
    return any(_REAL_KEY.match(env.get(k, "")) for k in ("LLM_API_KEY", "EMBEDDING_API_KEY"))


def turn_on(*, force: bool = False) -> None:
    env = read_env()
    if _has_real_key(env) and not force:
        sys.exit(
            "检测到 .env 里已有真实 API Key，--on 会用模拟 Key 覆盖它。\n"
            "确实要覆盖请加 --force；只想验证本地向量请改用 --local。"
        )
    _backup_once()
    write_env(
        {
            "EMBEDDING_PROVIDER": "api",
            "EMBEDDING_BASE_URL": MOCK_BASE,
            "EMBEDDING_API_KEY": MOCK_KEY,
            "LLM_PROVIDER": "api",
            "LLM_BASE_URL": MOCK_BASE,
            "LLM_API_KEY": MOCK_KEY,
            "RERANKER_PROVIDER": "api",
            "RERANKER_BASE_URL": MOCK_BASE,
            "RERANKER_API_KEY": MOCK_KEY,
            "RETRIEVAL_RERANK_ENABLED": "true",
        }
    )
    print("已切换到【模拟在线模式】（三个 Provider 全部指向 127.0.0.1:8001）")
    print("下一步：1) 启动模拟服务  2) 重启 API 与 worker（配置启动时读取）")


def turn_local() -> None:
    """本地向量 + 真实 LLM。向量走本机 bge-m3，生成走已配置的在线 LLM。"""
    env = read_env()
    if env.get("LLM_PROVIDER") != "api" or not env.get("LLM_API_KEY"):
        print("警告：LLM 未配置为 api 或 Key 为空，本次只切向量，生成仍为离线。")
    _backup_once()
    write_env(
        {
            "EMBEDDING_PROVIDER": "local",
            "EMBEDDING_MODEL": env.get("EMBEDDING_MODEL") or "BAAI/bge-m3",
            "RERANKER_PROVIDER": "fake",
            "RETRIEVAL_RERANK_ENABLED": "false",
        }
    )
    print("已切换到【本地在线模式】")
    print("  向量：本机 CPU 跑 bge-m3（进程内推理，不调外部服务）")
    print("  生成：沿用 .env 里的 LLM 配置")
    print("  重排：已关闭（假重排会按字面覆盖打乱真实语义排序）")
    print("下一步：重启 API 与 worker 后生效；首次问答会触发模型加载，耗时较长。")


def turn_off() -> None:
    if BACKUP_PATH.exists():
        shutil.copyfile(BACKUP_PATH, ENV_PATH)
        BACKUP_PATH.unlink()
        print("已从备份恢复离线配置")
    else:
        write_env(
            {
                "EMBEDDING_PROVIDER": "fake",
                "LLM_PROVIDER": "fake",
                "RERANKER_PROVIDER": "fake",
            }
        )
        print("无备份，仅将三个 PROVIDER 置回 fake")
    print("已切换到【离线模式】。重启 API 与 worker 后生效。")


def status() -> None:
    env = read_env()
    emb = env.get("EMBEDDING_PROVIDER", "(缺失)")
    llm = env.get("LLM_PROVIDER", "(缺失)")
    rrk = env.get("RERANKER_PROVIDER", "(缺失)")
    label = {
        "local": "本地 bge-m3",
        "api": "远程 API",
        "fake": "离线替身",
    }
    print(f"向量 EMBEDDING  = {emb:6} -> {label.get(emb, '?')}")
    print(f"生成 LLM        = {llm:6} -> {label.get(llm, '?')}")
    print(f"重排 RERANKER   = {rrk:6} -> {label.get(rrk, '?')}")
    print(f"重排开关        = {env.get('RETRIEVAL_RERANK_ENABLED', '(缺失)')}")
    print("-" * 56)
    for key in LINES:
        value = env.get(key) or "(缺失)"
        if "API_KEY" in key and value:
            value = value[:6] + "****" if len(value) > 6 else "(空)"
        print(f"  {key}={value}")


def main() -> None:
    parser = argparse.ArgumentParser(description="在线/离线/本地模式切换")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--on", action="store_true", help="切换到模拟在线模式")
    group.add_argument("--local", action="store_true", help="本地向量 + 真实 LLM")
    group.add_argument("--off", action="store_true", help="恢复离线模式")
    group.add_argument("--status", action="store_true", help="查看当前配置")
    parser.add_argument("--force", action="store_true", help="允许 --on 覆盖真实 Key")
    args = parser.parse_args()
    if args.on:
        turn_on(force=args.force)
    elif args.local:
        turn_local()
    elif args.off:
        turn_off()
    else:
        status()


if __name__ == "__main__":
    main()
