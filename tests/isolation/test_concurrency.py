"""并发下的租户隔离。

单请求交替（test_pool_reuse）只覆盖了串行复用；这里用并发把连接池压满，
确保 SET LOCAL 的上下文在并发交错时不会串到别的请求上。
"""

from __future__ import annotations

import asyncio

import pytest

from tests.helpers import seed_document

pytestmark = pytest.mark.db


async def test_concurrent_requests_keep_own_tenant(client_a, client_b, tenant_a, tenant_b):
    async def probe(client, tenant_id):
        response = await client.get("/api/v1/auth/me")
        assert response.status_code == 200
        return response.json()["tenant_id"] == str(tenant_id)

    tasks = []
    for _ in range(30):
        tasks.append(probe(client_a, tenant_a[0]))
        tasks.append(probe(client_b, tenant_b[0]))

    results = await asyncio.gather(*tasks)
    assert all(results), "并发请求中出现了租户上下文串用"


async def test_concurrent_writes_stay_in_own_tenant(client_a, client_b):
    async def create(client, name):
        response = await client.post("/api/v1/kbs", json={"name": name})
        assert response.status_code == 201
        return response.json()["id"]

    a_ids, b_ids = await asyncio.gather(
        asyncio.gather(*[create(client_a, f"A-{i}") for i in range(10)]),
        asyncio.gather(*[create(client_b, f"B-{i}") for i in range(10)]),
    )

    a_list = {kb["id"] for kb in (await client_a.get("/api/v1/kbs")).json()}
    b_list = {kb["id"] for kb in (await client_b.get("/api/v1/kbs")).json()}

    assert set(a_ids) <= a_list
    assert set(b_ids) <= b_list
    assert not (a_list & b_list), "两个租户的知识库列表出现交集"


async def test_concurrent_retrieval_never_crosses_tenant(client_a, client_b, tenant_a, tenant_b):
    from tests.helpers import fake_vector

    kb_a, _, _ = await seed_document(
        tenant_a[0],
        tenant_a[1],
        [("A 租户机密：报销上限 800 元。", await fake_vector("A 租户机密：报销上限 800 元。"))],
    )
    kb_b, _, _ = await seed_document(
        tenant_b[0],
        tenant_b[1],
        [("B 租户机密：报销上限 900 元。", await fake_vector("B 租户机密：报销上限 900 元。"))],
    )

    async def query(client, kb_id):
        response = await client.post(
            "/api/v1/debug/retrieve", json={"kb_ids": [str(kb_id)], "query": "报销上限"}
        )
        assert response.status_code == 200
        return response.json()

    tasks = []
    for _ in range(10):
        tasks.append(query(client_a, kb_a))
        tasks.append(query(client_b, kb_b))

    results = await asyncio.gather(*tasks)
    for index, body in enumerate(results):
        previews = " ".join(item["preview"] for item in body["dense"])
        if index % 2 == 0:
            assert "A 租户" in previews or previews == ""
            assert "B 租户" not in previews
        else:
            assert "B 租户" in previews or previews == ""
            assert "A 租户" not in previews
