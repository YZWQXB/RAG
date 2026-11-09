from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.core.config import settings
from app.modules.audit.router import router as audit_router
from app.modules.conversation.router import router as conversation_router
from app.modules.generation.router import router as chat_router
from app.modules.iam.router import router as member_router
from app.modules.ingestion.router import router as document_router
from app.modules.knowledge.router import router as knowledge_router
from app.modules.quota.router import router as quota_router
from app.modules.retrieval.router import router as debug_router
from app.modules.system.router import router as system_router
from app.modules.tenancy.router import router as tenant_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(tenant_router)
api_router.include_router(member_router)
api_router.include_router(knowledge_router)
api_router.include_router(document_router)
api_router.include_router(chat_router)
api_router.include_router(conversation_router)
api_router.include_router(quota_router)
api_router.include_router(audit_router)
api_router.include_router(system_router)

# 检索调试台：调分块与阈值的唯一可视化依据。
# 它会返回 chunk 预览与各路召回分数，属于信息暴露面，生产环境一律不挂载——
# 即使有人误把一个 ENABLE_DEBUG_API=true 的配置带上了生产。
if settings.enable_debug_api and not settings.is_production:
    api_router.include_router(debug_router)
