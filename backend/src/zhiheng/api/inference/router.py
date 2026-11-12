from fastapi import APIRouter

from zhiheng.api.inference.agents import router as agents_router
from zhiheng.api.inference.chat import router as chat_router
from zhiheng.api.inference.embeddings import router as embeddings_router
from zhiheng.api.inference.models import router as models_router
from zhiheng.api.inference.rerank import router as rerank_router

router = APIRouter()
router.include_router(models_router)
router.include_router(chat_router)
router.include_router(embeddings_router)
router.include_router(rerank_router)
router.include_router(agents_router)
