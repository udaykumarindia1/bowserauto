"""FastAPI application factory.

The API process never drives browsers itself; it persists state, publishes control signals and
relays real-time data. In development (no Redis) an embedded worker runs inside this process so a
single command gives a fully working system.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api import api_router
from app.core.config import get_settings
from app.core.database import dispose_engine, init_db, session_scope, wait_for_database
from app.core.errors import AppError
from app.core.logging import configure_logging, get_logger
from app.core.ratelimit import get_rate_limiter
from app.realtime.bus import start_bus, stop_bus
from app.storage.paths import ensure_data_dirs

logger = get_logger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # noqa: ANN001, ANN201
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.url.path.startswith("/mock-platform"):
            # The mock site is loaded inside the automated browser; it may embed its own assets.
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
        elif request.url.path.startswith("/api"):
            response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            response.headers.setdefault("Cache-Control", "no-store")
        if get_settings().cookies_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Request id + structured access log + global rate limit."""

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001, ANN201
        settings = get_settings()
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        started = time.perf_counter()
        if request.url.path.startswith("/api") and not request.url.path.startswith(("/api/ws", "/api/events", "/api/health")):
            from app.core.deps import client_ip

            allowed, retry_after = await get_rate_limiter().hit(f"general:{client_ip(request)}", settings.rate_limit_per_minute)
            if not allowed:
                return JSONResponse({"error": {"code": "rate_limited", "message": "Too many requests", "details": {"retry_after": retry_after}}}, status_code=429, headers={"Retry-After": str(retry_after)})
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled error", extra={"action": f"{request.method} {request.url.path}", "request_id": request_id})
            return JSONResponse({"error": {"code": "internal_error", "message": "Internal server error", "details": {"request_id": request_id}}}, status_code=500)
        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers["X-Request-ID"] = request_id
        if not request.url.path.startswith(("/api/health", "/mock-platform")):
            logger.info(
                "%s %s -> %s (%dms)",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
                extra={"action": f"{request.method} {request.url.path}", "result": response.status_code, "duration_ms": duration_ms, "request_id": request_id},
            )
        return response


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    ensure_data_dirs()
    await wait_for_database(retries=60 if settings.is_production else 5, delay=1.0)
    await init_db()
    async with session_scope() as db:
        from app.services.templates import ensure_builtin_templates

        await ensure_builtin_templates(db)
    await start_bus()
    worker = None
    if settings.worker_is_embedded and not settings.is_test:
        from app.workers.main import start_embedded_worker

        worker = await start_embedded_worker()
        logger.info("Embedded worker started (set REDIS_URL + run `python -m app.workers.main` for separate workers)")
    logger.info("%s API ready (env=%s, db=%s, bus=%s)", settings.app_name, settings.app_env, settings.sqlalchemy_database_url.split("://")[0], "redis" if settings.redis_url else "memory")
    try:
        yield
    finally:
        if worker is not None:
            from app.workers.main import stop_embedded_worker

            await stop_embedded_worker()
        await stop_bus()
        await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=f"{settings.app_name} API",
        version="1.0.0",
        description="AI Browser Automation Workspace – tasks, browser sessions, scenes, generated files and real-time events.",
        docs_url="/api/docs" if not settings.is_production else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if not settings.is_production else "/api/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "Authorization", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Content-Disposition"],
    )
    if settings.trusted_hosts and settings.trusted_hosts != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        headers = {}
        if exc.status_code == 429 and exc.details.get("retry_after"):
            headers["Retry-After"] = str(exc.details["retry_after"])
        return JSONResponse(exc.to_dict(), status_code=exc.status_code, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": [str(p) for p in e.get("loc", [])], "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        message = errors[0]["msg"] if errors else "Invalid request"
        if errors and errors[0]["loc"]:
            field = ".".join(p for p in errors[0]["loc"] if p not in ("body", "query", "path"))
            if field:
                message = f"{field}: {message}"
        return JSONResponse({"error": {"code": "validation_error", "message": message, "details": {"errors": errors}}}, status_code=422)

    app.include_router(api_router)
    if not settings.is_production or settings.mock_platform_url:
        from app.mock_platform.router import router as mock_router

        app.include_router(mock_router)

    @app.get("/", include_in_schema=False)
    async def root() -> dict:
        return {"name": settings.app_name, "status": "ok", "api": "/api", "docs": "/api/docs" if not settings.is_production else None}

    return app


app = create_app()
