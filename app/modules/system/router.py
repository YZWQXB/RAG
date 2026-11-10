"""运行模式（离线/在线）的查看与切换。

这是运维口子而非业务功能：切换会改变**整个进程**的检索与生成行为，
影响该实例上所有租户，因此只有空间所有者与管理员可见可切。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.v1.deps import CurrentPrincipal, DbSession
from app.core import mode
from app.core.errors import PermissionDeniedError
from app.modules.audit import service as audit

router = APIRouter(prefix="/system", tags=["system"])

SWITCH_ROLES = frozenset({"owner", "admin"})


class ModeSwitchIn(BaseModel):
    mode: Literal["offline", "online"]


class ModeOut(BaseModel):
    mode: str
    label: str
    providers: dict[str, str]
    llm_model: str
    embedding_model: str
    threshold: float
    warning: str | None = None
    persisted: bool = False
    cache_cleared: int = 0
    can_switch: bool = False


def _payload(status: mode.ModeStatus, *, can_switch: bool) -> ModeOut:
    return ModeOut(**status.as_dict(), can_switch=can_switch)


@router.get("/mode", response_model=ModeOut)
async def get_mode(principal: CurrentPrincipal) -> ModeOut:
    return _payload(mode.describe_mode(), can_switch=principal.role in SWITCH_ROLES)


@router.post("/mode", response_model=ModeOut)
async def switch_mode(
    payload: ModeSwitchIn, principal: CurrentPrincipal, session: DbSession
) -> ModeOut:
    if principal.role not in SWITCH_ROLES:
        raise PermissionDeniedError("只有空间所有者或管理员可以切换运行模式")

    before = mode.describe_mode().mode
    status = await mode.apply_mode(payload.mode)
    # 切到离线会让整个实例的检索退化为字面匹配，属于必须留痕的配置变更
    await audit.record(
        session,
        principal,
        "mode.switch",
        target_type="system",
        detail={"from": before, "to": status.mode},
    )
    return _payload(status, can_switch=True)
