"""大规模知识库构建与规模校验。

做三件事：
1. 生成 N 篇互不相同的 Markdown 文档并走**真实入库流水线**（上传→解析→分块→向量化→落库）
2. 统计入库吞吐与分块质量
3. 用带答案的查询集测召回率与延迟，并验证跨租户隔离在规模下仍然成立

用法：
    uv run python -m scripts.scale_check                # 默认 300 篇
    uv run python -m scripts.scale_check --docs 800 --queries 100
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select, text

from app.core.db import admin_engine, dispose_engines, session_scope
from app.core.errors import ConflictError
from app.core.security import Principal
from app.modules.ingestion.models import Chunk, Document
from app.modules.ingestion.service import upload_document
from app.modules.knowledge.schemas import KbCreate
from app.modules.knowledge.service import create_kb, list_kbs
from app.modules.quota.plans import PLANS
from app.modules.retrieval.service import retrieve
from app.modules.tenancy.service import register_tenant
from worker.tasks.outbox import poll_once

SCALE_SLUG = "scale-lab"
SCALE_EMAIL = "scale@example.com"
REPORT = Path("tests/eval/scale_report.json")

TOPICS = [
    ("售后政策", "退换货与保修"),
    ("产品规格", "型号与参数"),
    ("员工手册", "假期与考勤"),
    ("财务制度", "报销与付款"),
    ("信息安全", "账号与数据"),
    ("物流配送", "签收与异常"),
    ("采购流程", "供应商管理"),
    ("质量体系", "抽检与追溯"),
    ("客户服务", "响应时效"),
    ("合规审计", "留痕与复核"),
]

SECTIONS = [
    ("适用范围", "本规定适用于全部业务线与所有签署正式合同的供应商，含试用期员工与外包人员。"),
    ("责任分工", "由业务负责人初审，风控与法务复核，重大事项提交管理委员会决议并留档备查。"),
    ("时限要求", "自事项发生之日起计算，三个工作日内提交申请，逾期需附书面说明并经两级复核。"),
    ("费用标准", "按实际发生额据实列支，单笔超过五千元需附加比价记录与审批单，不允许拆分规避。"),
    ("异常处理", "发现异常应立即冻结流程并上报，二十四小时内完成初步核查，七十二小时内给出结论。"),
    ("记录保存", "全部原始凭证与审批轨迹保存不少于五年，电子件与纸质件同样具备审计效力。"),
]


def _doc_markdown(index: int, rng: random.Random) -> tuple[str, str, str]:
    """生成一篇唯一文档，返回 (文件名, 正文) 与唯一编号。"""
    code = f"PRD-{index:06d}"
    topic, subtitle = TOPICS[index % len(TOPICS)]
    body = [f"# {topic}（{code}）", "", "## 0 概要", "", f"本文档编号 {code}，主题为 {subtitle}。"]

    section_count = rng.randint(3, len(SECTIONS))
    for name, sentence in SECTIONS[:section_count]:
        body.append("")
        body.append(f"## {name}")
        body.append("")
        # 每个段落都带上唯一编号，便于按编号做可判定的召回评测
        body.append(
            f"{sentence}（依据 {code} 第 {name} 条，责任岗位为 {rng.choice(['A', 'B', 'C'])} 类）。"
        )
        body.append("")
        body.append(
            f"编号 {code} 在本条下的量化要求为 {rng.randint(1, 30)} 个自然日内完成，"
            f"特殊情况可延长至 {rng.randint(31, 90)} 个自然日。"
        )

    return f"{code}.md", "\n".join(body) + "\n", code


async def _ensure_tenant() -> tuple[uuid.UUID, uuid.UUID]:
    try:
        result = await register_tenant(SCALE_EMAIL, "Passw0rd!", "规模校验空间", SCALE_SLUG)
        return result.tenant_id, result.user_id
    except ConflictError:
        async with admin_engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        text(
                            "SELECT t.id, m.user_id FROM tenants t "
                            "JOIN memberships m ON m.tenant_id = t.id "
                            "JOIN users u ON u.id = m.user_id WHERE u.email = :e"
                        ),
                        {"e": SCALE_EMAIL},
                    )
                )
                .mappings()
                .one()
            )
        return row["id"], row["user_id"]


async def _ensure_kb(tenant_id: uuid.UUID, principal: Principal):
    async with session_scope(tenant_id=tenant_id) as session:
        existing = await list_kbs(session, principal)
        if existing:
            return existing[0].id
        kb = await create_kb(session, principal, KbCreate(name="规模校验知识库"))
        return kb.id


async def build(docs: int, tenant_id: uuid.UUID, user_id: uuid.UUID) -> dict:
    # 免费套餐只允许 100 篇，规模校验需要放开额度（仅运行时生效，不改代码）
    PLANS["scale"] = {
        "documents": 10**6,
        "storage_bytes": 10**12,
        "queries_per_month": 10**7,
        "llm_tokens_per_month": 10**9,
    }
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("UPDATE tenants SET plan = 'scale' WHERE id = :i"), {"i": tenant_id}
        )

    principal = Principal(user_id=user_id, tenant_id=tenant_id, role="owner")
    kb_id = await _ensure_kb(tenant_id, principal)

    rng = random.Random(20261109)
    codes: list[str] = []
    started = time.perf_counter()

    for index in range(docs):
        name, body, code = _doc_markdown(index, rng)
        raw = body.encode("utf-8")
        async with session_scope(tenant_id=tenant_id) as session:
            await upload_document(session, principal, kb_id, name, raw)
        codes.append(code)

    upload_seconds = time.perf_counter() - started

    # 本地存储要求全程只有一个入库进程：多个进程/宿主共享同一个库时，
    # 各自看到的是各自的磁盘，会出现"文件不存在"。检测到并发抢占就直接中止，
    # 避免把污染后的数字当成结论。
    async with admin_engine.connect() as conn:
        pending = (
            await conn.execute(
                text("SELECT count(*) FROM outbox_events WHERE status IN ('pending', 'processing')")
            )
        ).scalar_one()
    if pending == 0:
        raise SystemExit(
            "检测到已有其它入库进程（worker 容器？），本次结果会被污染。\n"
            "请先执行: docker compose stop worker"
        )

    processed = 0
    while True:
        handled = await poll_once(limit=50)
        if not handled:
            break
        processed += handled

    total_seconds = time.perf_counter() - started

    async with session_scope(tenant_id=tenant_id) as session:
        doc_count = (
            await session.execute(
                select(func.count(Document.id)).where(Document.deleted_at.is_(None))
            )
        ).scalar_one()
        ready = (
            await session.execute(select(func.count(Document.id)).where(Document.status == "ready"))
        ).scalar_one()
        failed = (
            await session.execute(
                select(func.count(Document.id)).where(Document.status == "failed")
            )
        ).scalar_one()
        chunk_count = (await session.execute(select(func.count(Chunk.id)))).scalar_one()
        chars = (await session.execute(select(func.sum(func.length(Chunk.content))))).scalar_one()

    return {
        "kb_id": str(kb_id),
        "documents": doc_count,
        "ready": ready,
        "failed": failed,
        "chunks": chunk_count,
        "chars": int(chars or 0),
        "upload_seconds": round(upload_seconds, 2),
        "total_seconds": round(total_seconds, 2),
        "docs_per_second": round(doc_count / total_seconds, 2) if total_seconds else 0,
        "codes": codes,
    }


async def measure_quality(
    tenant_id: uuid.UUID, user_id: uuid.UUID, kb_id: uuid.UUID, codes: list[str], queries: int
) -> dict:
    """分阶段度量召回。

    注意：本环境用的是字符袋假向量 + 假重排，它们**没有语义能力**，
    对 "PRD-000123 与 PRD-000230" 这类仅数字不同的样本几乎无法区分。
    因此这里度量的是链路的**候选召回能力**与延迟，不是语义质量：
    - dense_recall@20 / sparse_recall@20：单路召回的候选覆盖率
    - fused_recall@5：融合后前 5 条命中标注块的比例（关闭重排，避免假重排打乱顺序）
    """
    principal = Principal(user_id=user_id, tenant_id=tenant_id, role="owner")
    rng = random.Random(7)
    sample = rng.sample(codes, min(queries, len(codes)))

    dense_recalls: list[float] = []
    sparse_recalls: list[float] = []
    fused_recalls: list[float] = []
    reciprocal: list[float] = []
    latencies: list[int] = []

    async with session_scope(tenant_id=tenant_id) as session:
        for code in sample:
            relevant = set(
                (await session.execute(select(Chunk.id).where(Chunk.content.contains(code))))
                .scalars()
                .all()
            )
            if not relevant:
                continue
            query = f"{code} 的量化要求是多少个自然日"

            started = time.perf_counter()
            result = await retrieve(session, principal, [kb_id], query, use_rerank=False)
            latencies.append(int((time.perf_counter() - started) * 1000))

            dense_ids = {candidate.id for candidate in result.dense}
            sparse_ids = {candidate.id for candidate in result.sparse}
            fused_ids = [candidate.id for candidate in result.candidates]

            dense_recalls.append(len(relevant & dense_ids) / len(relevant))
            sparse_recalls.append(len(relevant & sparse_ids) / len(relevant))
            fused_recalls.append(len(relevant & set(fused_ids[:5])) / len(relevant))

            rank = next((i for i, cid in enumerate(fused_ids, 1) if cid in relevant), 0)
            reciprocal.append(1.0 / rank if rank else 0.0)

    latencies.sort()

    def pct(p: float) -> int:
        if not latencies:
            return 0
        return latencies[min(len(latencies) - 1, int(len(latencies) * p))]

    def avg(values: list[float]) -> float:
        return round(sum(values) / len(values), 4) if values else 0.0

    return {
        "queries": len(latencies),
        "dense_recall@20": avg(dense_recalls),
        "sparse_recall@20": avg(sparse_recalls),
        "fused_recall@5": avg(fused_recalls),
        "mrr": avg(reciprocal),
        "latency_ms_p50": pct(0.5),
        "latency_ms_p95": pct(0.95),
        "provider_note": "字符袋假向量 + 假重排，数值仅反映链路与候选召回，不代表语义质量",
    }


async def measure_isolation(kb_id: uuid.UUID) -> dict:
    """另一个租户拿着同样的 kb_id 查询，必须召回 0 条。"""
    other_tenant, other_user = await _ensure_other_tenant()
    principal = Principal(user_id=other_user, tenant_id=other_tenant, role="owner")
    async with session_scope(tenant_id=other_tenant) as session:
        result = await retrieve(session, principal, [kb_id], "PRD-000001 的量化要求")
        blocks = len(result.context.blocks)
    return {"foreign_kb_results": blocks, "leaked": blocks != 0}


async def _ensure_other_tenant() -> tuple[uuid.UUID, uuid.UUID]:
    try:
        result = await register_tenant(
            "scale-other@example.com", "Passw0rd!", "对照空间", "scale-other"
        )
        return result.tenant_id, result.user_id
    except ConflictError:
        async with admin_engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        text(
                            "SELECT t.id, m.user_id FROM tenants t "
                            "JOIN memberships m ON m.tenant_id = t.id "
                            "JOIN users u ON u.id = m.user_id "
                            "WHERE u.email = 'scale-other@example.com'"
                        )
                    )
                )
                .mappings()
                .one()
            )
        return row["id"], row["user_id"]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs", type=int, default=300)
    parser.add_argument("--queries", type=int, default=60)
    parser.add_argument("--reuse", action="store_true", help="复用已有语料，只做测量")
    args = parser.parse_args()

    tenant_id, user_id = await _ensure_tenant()

    if args.reuse and REPORT.exists():
        previous = json.loads(REPORT.read_text(encoding="utf-8"))
        build_stats = previous["build"]
        build_stats.pop("codes", None)
        kb_id = uuid.UUID(previous["build"]["kb_id"])
        codes = previous["codes"]
    else:
        build_stats = await build(args.docs, tenant_id, user_id)
        codes = build_stats.pop("codes")
        kb_id = uuid.UUID(build_stats["kb_id"])
        print(
            f"[build] docs={build_stats['documents']} ready={build_stats['ready']} "
            f"failed={build_stats['failed']} chunks={build_stats['chunks']} "
            f"in {build_stats['total_seconds']}s"
        )

    quality = await measure_quality(tenant_id, user_id, kb_id, codes, args.queries)
    isolation = await measure_isolation(kb_id)

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "build": build_stats,
        "quality": quality,
        "isolation": isolation,
        "codes": codes,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[quality] {quality}")
    print(f"[isolation] {isolation}")
    print(f"[report] {REPORT}")
    await dispose_engines()


if __name__ == "__main__":
    asyncio.run(main())
