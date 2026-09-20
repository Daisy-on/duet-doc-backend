from fastapi import APIRouter

from app.api.v1 import ai, auth, cloud_rag, health, media, models, rag, sync

router = APIRouter(prefix="/api/v1")
router.include_router(health.router)
router.include_router(auth.router)
router.include_router(ai.router)
router.include_router(models.router)
router.include_router(media.router)
router.include_router(rag.router)
router.include_router(cloud_rag.router)
router.include_router(sync.router)
