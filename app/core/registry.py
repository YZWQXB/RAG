"""集中导入所有 ORM 模型，供 Alembic 构建 metadata。

新增模型必须在此登记，否则 autogenerate 与 Base.metadata.create_all 会漏表。
"""

from app.core.db import Base
from app.modules.audit.models import AuditLog  # noqa: F401
from app.modules.conversation.models import Conversation, Message, MessageCitation
from app.modules.iam.models import Membership, User
from app.modules.ingestion.models import (
    Chunk,
    Document,
    DocumentVersion,
    IngestionJob,
    OutboxEvent,
)
from app.modules.knowledge.models import KbAcl, KnowledgeBase
from app.modules.quota.models import UsageRecord
from app.modules.tenancy.models import Tenant

__all__ = [
    "Base",
    "Tenant",
    "User",
    "Membership",
    "KnowledgeBase",
    "KbAcl",
    "Document",
    "DocumentVersion",
    "Chunk",
    "IngestionJob",
    "OutboxEvent",
    "Conversation",
    "Message",
    "MessageCitation",
    "UsageRecord",
    "AuditLog",
]
