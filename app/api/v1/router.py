from fastapi import APIRouter

from app.api.v1 import ai, health, sync

router = APIRouter(prefix="/api/v1")
router.include_router(health.router)
router.include_router(ai.router)
router.include_router(sync.router)
