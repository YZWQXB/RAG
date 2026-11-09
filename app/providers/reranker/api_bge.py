from __future__ import annotations

import httpx

from app.core.config import settings
from app.providers.reranker.base import RerankerError


class ApiBgeReranker:
    def __init__(self) -> None:
        self.base_url = settings.reranker_base_url.rstrip("/")
        self.api_key = settings.reranker_api_key
        self.model = settings.reranker_model

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        if not self.api_key:
            raise RerankerError("未配置 RERANKER_API_KEY")

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{self.base_url}/rerank",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "query": query,
                    "documents": documents,
                    "top_n": len(documents),
                },
            )
            response.raise_for_status()
            payload = response.json()

        scores = [0.0] * len(documents)
        for item in payload.get("results", []):
            scores[item["index"]] = float(item["relevance_score"])
        return scores
