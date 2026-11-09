"""离线 / 在线模式切换。

为什么需要单独一层：`settings` 是进程启动时的配置快照。

- 只改内存：重启即回到文件配置，用户会困惑「明明点了在线，重启又变回去」
- 只改 .env：必须重启才生效，点了按钮没反应

所以切换要同时做三件事：改内存（立刻生效）、写 .env（重启后保持）、
清查询向量缓存（否则旧 provider 算出的查询向量会被复用，导致跨空间比较）。

落盘时**只重写三个 PROVIDER 行**，不碰 BASE_URL / API_KEY / MODEL：
切换模式不该让用户重新填一次密钥。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.core.errors import BadRequestError, PermissionDeniedError
from app.core.logging import get_logger
from app.core.redis_client import get_redis

log = get_logger("mode")

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"

QUERY_CACHE_PATTERN = "emb:*"


class UnknownModeError(BadRequestError):
    code = "UNKNOWN_MODE"
    message = "未知的模式"


class ModeNotAllowedError(PermissionDeniedError):
    code = "MODE_NOT_ALLOWED"
    message = "当前环境不允许该模式"


@dataclass(frozen=True)
class ModeSpec:
    key: str
    label: str
    embedding: str
    llm: str
    reranker: str
    rerank_enabled: bool


MODES: dict[str, ModeSpec] = {
    # 离线：三层全用确定性替身，零外呼。检索退化为字符重合，仅适合联调与演示。
    "offline": ModeSpec("offline", "离线", "fake", "fake", "fake", False),
    # 在线：本机 bge-m3 做真实语义向量，远程大模型生成。
    # 重排保持关闭——假重排按字面覆盖率打分，会在真实语义排序上做反优化。
    "online": ModeSpec("online", "在线", "local", "api", "fake", False),
}

OFFLINE_WARNING = (
    "离线模式检索退化为字面匹配（按字符重合度），换个说法就可能召回不到；切回在线无需重建索引。"
)


@dataclass
class ModeStatus:
    mode: str
    label: str
    embedding: str
    llm: str
    reranker: str
    llm_model: str
    embedding_model: str
    threshold: float
    warning: str | None = None
    persisted: bool = False
    cache_cleared: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "label": self.label,
            "providers": {
                "embedding": self.embedding,
                "llm": self.llm,
                "reranker": self.reranker,
            },
            "llm_model": self.llm_model,
            "embedding_model": self.embedding_model,
            "threshold": self.threshold,
            "warning": self.warning,
            "persisted": self.persisted,
            "cache_cleared": self.cache_cleared,
        }


def _display_embedding_model() -> str:
    """把配置值还原成人能读的模型名。

    local 模式下配的是一条缓存路径，最后一段往往是 snapshots/master 这类目录名，
    直接取 basename 会显示成 "master"。真正的名字在 BAAI--bge-m3 这样的段里。
    fake 模式不调用模型，配置里残留的路径只会造成误导，显示为「—」。
    """
    if settings.embedding_provider == "fake":
        return "—"

    name = settings.embedding_model.strip()
    if settings.embedding_provider != "local" or not name:
        return name

    for part in reversed(Path(name).parts):
        if "--" in part:
            cleaned = part.removeprefix("models--")
            return cleaned.replace("--", "/")
    return Path(name).name or name


def describe_mode() -> ModeStatus:
    """从当前内存配置推断模式。不读库、不发网络请求，可高频调用。"""
    embedding = settings.embedding_provider
    llm = settings.llm_provider
    reranker = settings.reranker_provider

    if embedding == "fake" and llm == "fake":
        mode_key, label, warning = "offline", "离线", OFFLINE_WARNING
    elif embedding in {"local", "api"} and llm == "api":
        mode_key, label, warning = "online", "在线", None
    else:
        # 用户手改 .env 配出的非常规组合，如实呈现，不硬套成两种预设
        mode_key, label, warning = "custom", "自定义", None

    return ModeStatus(
        mode=mode_key,
        label=label,
        embedding=embedding,
        llm=llm,
        reranker=reranker,
        llm_model="—" if llm == "fake" else settings.llm_model,
        embedding_model=_display_embedding_model(),
        threshold=settings.retrieval_no_answer_threshold,
        warning=warning,
    )


def _rewrite_env(path: Path, values: dict[str, str]) -> bool:
    """只替换已存在的键，不新增行、不动其它内容。返回是否成功写入。"""
    try:
        original = path.read_text(encoding="utf-8")
    except OSError:
        log.warning("mode_env_read_failed", path=str(path))
        return False

    seen: set[str] = set()
    lines: list[str] = []
    for raw in original.splitlines():
        match = re.match(r"^([A-Z_]+)=(.*)$", raw)
        if match and match.group(1) in values:
            key = match.group(1)
            lines.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            lines.append(raw)

    for key, value in values.items():
        if key not in seen:
            lines.append(f"{key}={value}")

    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        log.warning("mode_env_write_failed", path=str(path))
        return False
    return True


async def _clear_query_cache() -> int:
    """清掉查询向量缓存。

    缓存键里虽然带了 provider 类名，但同一类在不同模型下向量空间也不同
    （例如 bge-m3 换了权重）。切换时无条件清空是最省心的正确做法。
    """
    try:
        redis = get_redis()
        keys = [key async for key in redis.scan_iter(QUERY_CACHE_PATTERN)]
        if keys:
            await redis.delete(*keys)
        return len(keys)
    except Exception as exc:  # 缓存清不掉不该阻断切换，只是可能短暂读到旧值
        log.warning("mode_cache_clear_failed", error=type(exc).__name__)
        return 0


_last_env_mtime: int | None = None


def sync_from_env_if_changed(env_path: Path | None = None) -> bool:
    """文件被改写后把配置同步进当前进程。

    API 切换模式时改的是**它自己**的内存配置；worker 是另一个进程，
    不共享内存。少了这一步，API 已切离线而 worker 仍用真实模型向量化，
    新入库的分块与查询向量会落在不同空间。

    由 worker 在轮询间隙调用，代价是一次 stat()。
    """
    global _last_env_mtime
    path = env_path or ENV_PATH
    try:
        # 用纳秒精度：秒级精度下「同一秒内改两次」会被判定为未变化
        mtime = path.stat().st_mtime_ns
    except OSError:
        return False

    if _last_env_mtime is None:
        _last_env_mtime = mtime
        return False
    if mtime <= _last_env_mtime:
        return False
    _last_env_mtime = mtime

    try:
        # 显式指定文件，确保读到的是刚被改写的那一份，而不是构造时缓存的路径
        fresh = type(settings)(_env_file=str(path))
    except Exception as exc:  # 文件写坏了不能让进程起不来
        log.warning("mode_sync_invalid_env", error=type(exc).__name__)
        return False

    settings.embedding_provider = fresh.embedding_provider
    settings.llm_provider = fresh.llm_provider
    settings.reranker_provider = fresh.reranker_provider
    settings.retrieval_rerank_enabled = fresh.retrieval_rerank_enabled
    log.info(
        "mode_synced_from_env",
        embedding=settings.embedding_provider,
        llm=settings.llm_provider,
    )
    return True


async def apply_mode(
    key: str,
    *,
    env_path: Path | None = None,
    persist: bool = True,
) -> ModeStatus:
    spec = MODES.get(key)
    if spec is None:
        raise UnknownModeError(f"不支持的模式：{key}，可选 offline / online")

    # 与 config._guard_production 保持同一立场：非本地环境禁止假实现，
    # 否则生产流量会在无人察觉的情况下退化成摘录拼接。
    if spec.key == "offline" and settings.env != "local":
        raise ModeNotAllowedError("非本地环境不允许切换到离线模式")

    settings.embedding_provider = spec.embedding
    settings.llm_provider = spec.llm
    settings.reranker_provider = spec.reranker
    settings.retrieval_rerank_enabled = spec.rerank_enabled

    persisted = False
    if persist:
        persisted = _rewrite_env(
            env_path or ENV_PATH,
            {
                "EMBEDDING_PROVIDER": spec.embedding,
                "LLM_PROVIDER": spec.llm,
                "RERANKER_PROVIDER": spec.reranker,
            },
        )

    cleared = await _clear_query_cache()

    log.info("mode_switched", mode=spec.key, persisted=persisted, cache_cleared=cleared)

    status = describe_mode()
    status.persisted = persisted
    status.cache_cleared = cleared
    return status
