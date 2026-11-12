from fastapi import APIRouter

from zhiheng.api.resources.files import router as files_router
from zhiheng.api.resources.knowledge import router as knowledge_router
from zhiheng.api.resources.operations import router as operations_router
from zhiheng.api.resources.skills import router as skills_router

router = APIRouter()
router.include_router(files_router)
router.include_router(knowledge_router)
router.include_router(operations_router)
router.include_router(skills_router)
