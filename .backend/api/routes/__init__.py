"""
Description: Route package initialization
Features:
    - Aggregate API routes
Dependencies: fastapi
"""

from fastapi import APIRouter

from api.routes.health import router as health_router
from api.routes.documents import router as documents_router
from api.routes.vector import router as vector_router
from api.routes.rag import router as rag_router
from api.routes.chat import router as chat_router
from api.routes.agents import router as agents_router


# ============================================
# region Route Aggregation
# ============================================

router = APIRouter()

# Core routes
router.include_router(health_router)
router.include_router(agents_router)  # Agent management (/agents)
router.include_router(documents_router)
router.include_router(vector_router)
router.include_router(rag_router)
router.include_router(chat_router)

# endregion
# ============================================
