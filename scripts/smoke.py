"""全链路冒烟：注册 → 建库 → 上传 → 等就绪 → 提问 → 断言引用。

用法：
    uv run python -m scripts.smoke                 # 默认 http://localhost:8080
    uv run python -m scripts.smoke --base http://localhost:8000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path

import httpx

FIXTURE = Path("tests/fixtures/sample.md")


async def _wait_ready(client: httpx.AsyncClient, doc_id: str, timeout: float = 90) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        response = await client.get(f"/api/v1/documents/{doc_id}")
        response.raise_for_status()
        detail = response.json()
        if detail["status"] == "ready":
            return detail
        if detail["status"] == "failed":
            raise SystemExit(f"文档处理失败: {detail.get('error')}")
        await asyncio.sleep(3)
    raise SystemExit("等待文档就绪超时")


async def _ask(client: httpx.AsyncClient, kb_id: str, query: str) -> dict:
    events: dict[str, list[dict]] = {}
    async with client.stream(
        "POST", "/api/v1/chat/stream", json={"kb_ids": [kb_id], "query": query}
    ) as response:
        if response.status_code != 200:
            body = await response.aread()
            raise SystemExit(f"提问失败 {response.status_code}: {body[:200]!r}")
        event_name = ""
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:") and event_name:
                events.setdefault(event_name, []).append(json.loads(line[5:].strip()))
                event_name = ""
    return events


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8080")
    args = parser.parse_args()

    email = f"smoke-{uuid.uuid4().hex[:8]}@example.com"
    async with httpx.AsyncClient(base_url=args.base, timeout=120) as client:
        register = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "Passw0rd!", "tenant_name": "冒烟测试"},
        )
        register.raise_for_status()
        token = register.json()["token"]
        client.headers["Authorization"] = f"Bearer {token}"
        print("[1/5] 注册成功")

        kb = await client.post("/api/v1/kbs", json={"name": "冒烟知识库"})
        kb.raise_for_status()
        kb_id = kb.json()["id"]
        print(f"[2/5] 建库成功 {kb_id}")

        upload = await client.post(
            f"/api/v1/kbs/{kb_id}/documents",
            files={"file": (FIXTURE.name, FIXTURE.read_bytes(), "text/markdown")},
        )
        upload.raise_for_status()
        doc_id = upload.json()["document_id"]
        print(f"[3/5] 上传成功 {doc_id}")

        detail = await _wait_ready(client, doc_id)
        print(f"[4/5] 文档就绪，片段数 {detail['chunk_count']}")

        events = await _ask(client, kb_id, "年假有多少天")
        if "no_answer" in events:
            raise SystemExit("意外触发无答案兜底")
        done = events.get("done", [{}])[-1]
        if not done.get("used_citations"):
            raise SystemExit("回答未携带引用")
        print(f"[5/5] 问答成功，引用 {done['used_citations']}")

    print("SMOKE OK")


if __name__ == "__main__":
    asyncio.run(main())
