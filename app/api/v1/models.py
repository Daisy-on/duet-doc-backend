import logging
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.dependencies import AuthenticatedUser
from app.schemas.models import ModelManifest
from app.services.model_delivery import (
    ModelNotFoundError,
    ModelSigningError,
)
from app.services.model_manifest_manager import (
    ModelManifestManager,
    ModelManifestRateLimitError,
)
from app.services.rate_limit import RateLimitExceeded, get_client_ip

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/models", tags=["models"])


def get_model_manifest_manager(request: Request) -> ModelManifestManager:
    manager = getattr(request.app.state, "model_manifest_manager", None)
    if manager is None:
        raise HTTPException(503, "Model delivery is not configured")
    return cast(ModelManifestManager, manager)


ManifestManager = Annotated[ModelManifestManager, Depends(get_model_manifest_manager)]


def _rate_limit_response(retry_after_seconds: int) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail={
            "code": "MODEL_DOWNLOAD_RATE_LIMITED",
            "message": "模型下载请求过于频繁，请稍后重试",
            "retry_after_seconds": retry_after_seconds,
        },
        headers={"Retry-After": str(retry_after_seconds)},
    )


@router.get("/{model_id}/manifest", response_model=ModelManifest)
async def get_model_manifest(
    model_id: str,
    request: Request,
    current_user: AuthenticatedUser,
    manager: ManifestManager,
) -> ModelManifest:
    settings = request.app.state.settings
    client_ip = get_client_ip(request, settings.trust_proxy_headers)
    try:
        await request.app.state.model_manifest_ip_limiter.check(client_ip)
        return await manager.get_manifest(current_user.user_id, model_id, client_ip)
    except (RateLimitExceeded, ModelManifestRateLimitError) as exc:
        raise _rate_limit_response(exc.retry_after_seconds) from exc
    except ModelNotFoundError as exc:
        raise HTTPException(404, "Model not found") from exc
    except ModelSigningError as exc:
        logger.exception(
            "model_manifest_signing_failed model_id=%s user_id=%s",
            model_id,
            current_user.user_id,
        )
        raise HTTPException(503, "Model download is temporarily unavailable") from exc
