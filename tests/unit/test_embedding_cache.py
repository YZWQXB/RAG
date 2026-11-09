from __future__ import annotations

from app.modules.retrieval.embedding_cache import embed_cached
from app.providers.embedding.fake import FakeEmbedding


class StubRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def setex(self, key: str, ttl: int, value: str):
        self.data[key] = value


class BrokenRedis:
    async def get(self, key: str):
        raise ConnectionError("redis down")

    async def setex(self, key: str, ttl: int, value: str):
        raise ConnectionError("redis down")


async def test_second_call_hits_cache(monkeypatch):
    provider = FakeEmbedding()
    from app.providers import set_embedding_provider

    set_embedding_provider(provider)
    stub = StubRedis()
    monkeypatch.setattr("app.modules.retrieval.embedding_cache.get_redis", lambda: stub)

    first = await embed_cached("年假有几天")
    second = await embed_cached("年假有几天")

    assert first == second
    assert provider.calls == 1


async def test_degrades_when_redis_down(monkeypatch):
    provider = FakeEmbedding()
    from app.providers import set_embedding_provider

    set_embedding_provider(provider)
    monkeypatch.setattr("app.modules.retrieval.embedding_cache.get_redis", lambda: BrokenRedis())

    vector = await embed_cached("无关问题")
    assert len(vector) == 1024
    assert provider.calls == 1
