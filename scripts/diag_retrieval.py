"""诊断脚本：对指定 KB 实测检索，输出各路得分与召回块，验证检索链路是否有错。"""

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.core.db import session_scope  # noqa: E402
from app.core.security import Principal  # noqa: E402
from app.modules.retrieval import service as retrieval  # noqa: E402

TENANT = uuid.UUID("de69a78b-a69c-4725-afa7-dea0646a7418")
KB = uuid.UUID("253d7579-3e15-46e8-8479-f89df959c60c")


async def main() -> None:
    principal = Principal(user_id=uuid.uuid4(), tenant_id=TENANT, role="owner")
    async with session_scope(tenant_id=TENANT) as session:
        for query in ("printf用法是什么", "年假有多少天", "TCP 三次握手"):
            result = await retrieval.retrieve(session, principal, [KB], query)
            blocks = result.context.blocks
            print(f"\n=== 查询: {query} ===")
            print(
                f"top_dense_score={result.top_dense_score:.4f}  "
                f"no_answer_threshold={settings.retrieval_no_answer_threshold}  "
                f"blocks={len(blocks)}"
            )
            for b in blocks[:4]:
                preview = (b.content or "").replace("\n", " ")[:60]
                print(f"  [{b.index}] {preview}")


if __name__ == "__main__":
    asyncio.run(main())
