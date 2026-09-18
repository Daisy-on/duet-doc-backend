import logging
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.dependencies import AuthenticatedUser
from app.schemas.models import ModelManifest
from app.services.model_delivery import (
    ModelDeliveryService,
    ModelNotFoundError,
    ModelSigningError,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/models", tags=["models"])


def get_model_delivery_service(request: Request) -> ModelDeliveryService:
    service = getattr(request.app.state, "model_delivery_service", None)
    if service is None:
        raise HTTPException(503, "Model delivery is not configured")
    return cast(ModelDeliveryService, service)


ModelDelivery = Annotated[ModelDeliveryService, Depends(get_model_delivery_service)]


@router.get("/{model_id}/manifest", response_model=ModelManifest)
def get_model_manifest(
    model_id: str,
    current_user: AuthenticatedUser,
    service: ModelDelivery,
) -> ModelManifest:
    try:
        return service.get_manifest(model_id)
    except ModelNotFoundError as exc:
        raise HTTPException(404, "Model not found") from exc
    except ModelSigningError as exc:
        logger.exception(
            "model_manifest_signing_failed model_id=%s user_id=%s",
            model_id,
            current_user.user_id,
        )
        raise HTTPException(503, "Model download is temporarily unavailable") from exc
