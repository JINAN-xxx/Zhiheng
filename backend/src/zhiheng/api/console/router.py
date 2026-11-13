from fastapi import APIRouter

from zhiheng.api.console.admin_applications import router as admin_applications_router
from zhiheng.api.console.admin_identity import router as admin_identity_router
from zhiheng.api.console.admin_monitor import router as admin_monitor_router
from zhiheng.api.console.admin_operations import router as admin_operations_router
from zhiheng.api.console.agents import router as agents_router
from zhiheng.api.console.assistant_conversations import router as assistant_conversations_router
from zhiheng.api.console.auth import router as auth_router
from zhiheng.api.console.billing import router as billing_router
from zhiheng.api.console.chat import router as chat_router
from zhiheng.api.console.chat_files import router as chat_files_router
from zhiheng.api.console.developer import router as developer_router
from zhiheng.api.console.files import router as files_router
from zhiheng.api.console.knowledge import router as knowledge_router
from zhiheng.api.console.knowledge_graph import router as knowledge_graph_router
from zhiheng.api.console.knowledge_settings import router as knowledge_settings_router
from zhiheng.api.console.knowledge_sharing import router as knowledge_sharing_router
from zhiheng.api.console.message_actions import public_router as shared_messages_router
from zhiheng.api.console.message_actions import router as message_actions_router
from zhiheng.api.console.models import router as models_router
from zhiheng.api.console.runs import router as runs_router
from zhiheng.api.console.skills import router as skills_router
from zhiheng.api.console.usage import router as usage_router
from zhiheng.api.console.work import router as work_router

router = APIRouter()
router.include_router(assistant_conversations_router)
router.include_router(admin_applications_router)
router.include_router(admin_identity_router)
router.include_router(admin_monitor_router)
router.include_router(admin_operations_router)
router.include_router(agents_router)
router.include_router(auth_router)
router.include_router(billing_router)
router.include_router(chat_router)
router.include_router(chat_files_router)
router.include_router(developer_router)
router.include_router(files_router)
router.include_router(knowledge_router)
router.include_router(knowledge_settings_router)
router.include_router(knowledge_sharing_router)
router.include_router(knowledge_graph_router)
router.include_router(message_actions_router)
router.include_router(models_router)
router.include_router(runs_router)
router.include_router(skills_router)
router.include_router(usage_router)
router.include_router(work_router)
router.include_router(shared_messages_router)
