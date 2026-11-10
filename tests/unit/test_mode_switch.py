"""运行时模式切换（离线/在线）的行为契约。

这一层的存在意义：settings 是进程启动时的配置快照。
- 只改内存：重启即丢，用户会困惑「明明点了在线，重启又变回去」
- 只改 .env：必须重启才生效，点了按钮没反应
所以必须两者都做，且切换时清掉查询向量缓存（否则旧 provider 算的向量会被复用）。
"""

from __future__ import annotations

import pytest

from app.core import mode


@pytest.fixture(autouse=True)
def _restore_settings():
    """每个用例结束后还原真实配置，避免污染同进程内的其它用例。"""
    saved = {
        "embedding_provider": mode.settings.embedding_provider,
        "llm_provider": mode.settings.llm_provider,
        "reranker_provider": mode.settings.reranker_provider,
        "retrieval_rerank_enabled": mode.settings.retrieval_rerank_enabled,
        "env": mode.settings.env,
    }
    yield
    for key, value in saved.items():
        setattr(mode.settings, key, value)


@pytest.fixture
def env_file(tmp_path):
    """一份带密钥的最小 .env，用于验证「只改 provider 行」。"""
    path = tmp_path / ".env"
    path.write_text(
        "LLM_PROVIDER=api\n"
        "LLM_BASE_URL=https://maas-api.cn-huabei-1.xf-yun.com/v1\n"
        "LLM_API_KEY=ak-secret-should-survive\n"
        "LLM_MODEL=spark-x2.5-1.7b\n"
        "EMBEDDING_PROVIDER=local\n"
        "RERANKER_PROVIDER=fake\n",
        encoding="utf-8",
    )
    return path


async def test_apply_offline_switches_all_providers_to_fake(env_file, monkeypatch):
    monkeypatch.setattr(mode, "_clear_query_cache", _noop_clear)
    await mode.apply_mode("offline", env_path=env_file)

    assert mode.settings.embedding_provider == "fake"
    assert mode.settings.llm_provider == "fake"
    assert mode.settings.reranker_provider == "fake"
    assert mode.settings.retrieval_rerank_enabled is False


async def test_apply_online_restores_real_providers(env_file, monkeypatch):
    monkeypatch.setattr(mode, "_clear_query_cache", _noop_clear)
    await mode.apply_mode("offline", env_path=env_file)
    await mode.apply_mode("online", env_path=env_file)

    assert mode.settings.embedding_provider == "local"
    assert mode.settings.llm_provider == "api"
    assert mode.settings.reranker_provider == "fake"


async def test_apply_unknown_mode_is_rejected(env_file):
    with pytest.raises(mode.UnknownModeError):
        await mode.apply_mode("turbo", env_path=env_file)


async def test_offline_is_refused_outside_local_env(env_file, monkeypatch):
    monkeypatch.setattr(mode, "_clear_query_cache", _noop_clear)
    monkeypatch.setattr(mode.settings, "env", "production")
    # 显式给定基线：settings 是进程级单例，前面的用例可能已把 provider 改成 fake
    monkeypatch.setattr(mode.settings, "llm_provider", "api")

    with pytest.raises(mode.ModeNotAllowedError):
        await mode.apply_mode("offline", env_path=env_file)

    # 拒绝必须发生在任何副作用之前：provider 不能被改掉
    assert mode.settings.llm_provider == "api"


async def test_switch_preserves_api_key_on_disk(env_file, monkeypatch):
    monkeypatch.setattr(mode, "_clear_query_cache", _noop_clear)
    await mode.apply_mode("offline", env_path=env_file)

    text = env_file.read_text(encoding="utf-8")
    assert "LLM_API_KEY=ak-secret-should-survive" in text
    assert "LLM_PROVIDER=fake" in text
    assert "EMBEDDING_PROVIDER=fake" in text


async def test_switch_clears_query_cache(env_file, monkeypatch):
    calls: list[int] = []

    async def fake_clear() -> int:
        calls.append(1)
        return 7

    monkeypatch.setattr(mode, "_clear_query_cache", fake_clear)
    status = await mode.apply_mode("offline", env_path=env_file)

    assert calls == [1]
    assert status.cache_cleared == 7


def test_describe_detects_offline(monkeypatch):
    monkeypatch.setattr(mode.settings, "embedding_provider", "fake")
    monkeypatch.setattr(mode.settings, "llm_provider", "fake")
    status = mode.describe_mode()

    assert status.mode == "offline"
    assert status.label == "离线"


def test_describe_detects_online(monkeypatch):
    monkeypatch.setattr(mode.settings, "embedding_provider", "local")
    monkeypatch.setattr(mode.settings, "llm_provider", "api")
    status = mode.describe_mode()

    assert status.mode == "online"
    assert status.label == "在线"


def test_describe_falls_back_to_custom_for_unusual_mix(monkeypatch):
    monkeypatch.setattr(mode.settings, "embedding_provider", "api")
    monkeypatch.setattr(mode.settings, "llm_provider", "fake")
    status = mode.describe_mode()

    assert status.mode == "custom"


def test_offline_status_carries_degradation_warning(monkeypatch):
    monkeypatch.setattr(mode.settings, "embedding_provider", "fake")
    monkeypatch.setattr(mode.settings, "llm_provider", "fake")
    status = mode.describe_mode()

    assert status.warning is not None
    assert "字面" in status.warning


def test_display_embedding_model_extracts_name_from_snapshot_path(monkeypatch):
    monkeypatch.setattr(mode.settings, "embedding_provider", "local")
    monkeypatch.setattr(
        mode.settings,
        "embedding_model",
        "E:/RAG_ai_pro/data/models/models/BAAI--bge-m3/snapshots/master",
    )

    assert mode._display_embedding_model() == "BAAI/bge-m3"


def test_display_embedding_model_keeps_remote_name_untouched(monkeypatch):
    monkeypatch.setattr(mode.settings, "embedding_provider", "api")
    monkeypatch.setattr(mode.settings, "embedding_model", "BAAI/bge-m3")

    assert mode._display_embedding_model() == "BAAI/bge-m3"


def test_fake_mode_hides_model_names(monkeypatch):
    """假实现不调用任何模型，把配置里的路径/模型名带出来只会误导。"""
    monkeypatch.setattr(mode.settings, "embedding_provider", "fake")
    monkeypatch.setattr(mode.settings, "llm_provider", "fake")
    status = mode.describe_mode()

    assert status.embedding_model == "—"
    assert status.llm_model == "—"


async def _noop_clear() -> int:
    return 0


def test_sync_from_env_picks_up_rewritten_file(env_file, monkeypatch):
    """API 改写 .env 后，worker 进程靠 mtime 变化感知并跟随。"""
    for key in (
        "LLM_PROVIDER",
        "EMBEDDING_PROVIDER",
        "RERANKER_PROVIDER",
        "RETRIEVAL_RERANK_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(mode, "_last_env_mtime", None)

    # 第一次只记录基线，不做重载
    assert mode.sync_from_env_if_changed(env_path=env_file) is False

    env_file.write_text(
        env_file.read_text(encoding="utf-8")
        .replace("LLM_PROVIDER=api", "LLM_PROVIDER=fake")
        .replace("EMBEDDING_PROVIDER=local", "EMBEDDING_PROVIDER=fake"),
        encoding="utf-8",
    )
    base = env_file.stat().st_mtime
    import os

    os.utime(env_file, (base + 100, base + 100))

    monkeypatch.setattr(mode.settings, "llm_provider", "api")
    monkeypatch.setattr(mode.settings, "embedding_provider", "local")

    assert mode.sync_from_env_if_changed(env_path=env_file) is True
    assert mode.settings.llm_provider == "fake"
    assert mode.settings.embedding_provider == "fake"


def test_sync_is_noop_when_file_unchanged(env_file, monkeypatch):
    monkeypatch.setattr(mode, "_last_env_mtime", env_file.stat().st_mtime_ns)
    assert mode.sync_from_env_if_changed(env_path=env_file) is False
