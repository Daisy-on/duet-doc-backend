from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import router as api_v1_router
from app.core.config import Settings, get_settings
from app.core.exceptions import AIServiceError
from app.core.logging import configure_logging
from app.database import create_database
from app.providers.deepseek import DeepSeekProvider
from app.schemas.ai import APIError, ErrorResponse
from app.services.ai_dispatcher import AIDispatcher


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.app_env)
    if resolved_settings.dev_auth_enabled and resolved_settings.app_env != "development":
        raise ValueError("DEV_AUTH_ENABLED is restricted to development")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        timeout = httpx.Timeout(
            resolved_settings.ai_read_timeout_seconds,
            connect=resolved_settings.ai_connect_timeout_seconds,
        )
        client = httpx.AsyncClient(timeout=timeout)
        provider = DeepSeekProvider(resolved_settings, client)
        app.state.ai_dispatcher = AIDispatcher(resolved_settings, provider)
        engine = None
        if resolved_settings.database_url:
            engine, sessions = create_database(resolved_settings)
            app.state.database_sessions = sessions
        try:
            yield
        finally:
            await client.aclose()
            if engine is not None:
                await engine.dispose()

    app = FastAPI(
        title="Duet Doc Backend",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID"],
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(AIServiceError)
    async def handle_ai_error(request: Request, exc: AIServiceError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", str(uuid4()))
        body = ErrorResponse(
            error=APIError(
                code=exc.code,
                message=exc.message,
                request_id=request_id,
                retryable=exc.retryable,
            )
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=body.model_dump(by_alias=True),
        )

    app.include_router(api_v1_router)
    return app


app = create_app()
