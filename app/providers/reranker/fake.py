"""确定性假重排：按查询词在文档中的覆盖率给分。

没有语义能力，但能保证"包含更多查询词元的文档排前面"这一基本可验证行为，
足以测试重排流程与截断逻辑。
"""

from __future__ import annotations

from app.providers.reranker.base import RerankerError


class FakeReranker:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.calls += 1
        if self.fail:
            raise RerankerError("模拟重排服务不可用")
        terms = {c for c in query if not c.isspace()}
        if not terms:
            return [0.0] * len(documents)
        return [len(terms & set(doc)) / len(terms) for doc in documents]
