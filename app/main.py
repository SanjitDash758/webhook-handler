from app.core.metrics_bootstrap import setup_multiprocess_dir
setup_multiprocess_dir()
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator
from fastapi import FastAPI, Request
from sqlalchemy import text
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from app.core.exceptions import AuthenticationError
from app.api.middleware.rate_limit import RateLimitMiddleware
from app.api.routers.admin import router as admin_router
from app.api.routers.generic_webhooks import router as generic_router
from app.api.routers.stripe_webhooks import router as stripe_router
from app.api.routers.metrics import router as metrics_router
from app.api.routers.pipeline_ws import router as pipeline_ws_router
from app.core.config import settings
from app.core.database import engine, Base
from app.core.logging import get_logger, setup_logging
from app.utils.redis_client import close_redis, init_redis
import app.models.db.webhook_receipt

logger = get_logger(__name__)


# ============================================
# LIFESPAN
# ============================================
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging("DEBUG" if settings.DEBUG else "INFO")
    logger.info(
        f"Starting {settings.APP_NAME} v{settings.APP_VERSION} "
        f"(env={settings.ENVIRONMENT})"
    )

    await init_redis()
    logger.info("Redis ready")

    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Postgres ready (tables verified/created)")
    except Exception as exc:
        logger.critical(f"Postgres unreachable at startup: {exc}")
        raise

    logger.info("Startup complete")
    yield

    logger.info("Shutting down...")
    await close_redis()
    await engine.dispose()
    logger.info("Shutdown complete")


# ============================================
# APP FACTORY
# ============================================
def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="Idempotent webhook processor with retry, DLQ, and Redis fast-path.",
        lifespan=lifespan,
    )

    @app.exception_handler(AuthenticationError)
    async def _handle_authentication_error(request: Request, exc: AuthenticationError):
        return JSONResponse(
            status_code=401,
            content={"detail": str(exc)},
            headers={"WWW-Authenticate": "ApiKey"},
        )

    # ---- Middleware ----
    # Applied in reverse order of execution (bottom runs first).
    # 1. Rate limiting runs first on incoming requests
    app.add_middleware(RateLimitMiddleware)
    
    # 2. CORS middleware wraps around everything so browser preflight checks pass
    CORS_ORIGINS = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://localhost:3000"
    ).split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- Routers ----
    app.include_router(stripe_router)
    app.include_router(generic_router)
    app.include_router(admin_router)
    app.include_router(metrics_router)
    app.include_router(pipeline_ws_router)

    # ---- Health ----
    @app.get("/health", tags=["meta"], summary="Liveness + readiness probe")
    async def health() -> JSONResponse:
        checks = {"redis": False, "postgres": False}
        try:
            from app.utils.redis_client import get_redis
            client = await get_redis()
            await client.ping()
            checks["redis"] = True
        except Exception as exc:
            logger.warning(f"Health: redis check failed: {exc}")

        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["postgres"] = True
        except Exception as exc:
            logger.warning(f"Health: postgres check failed: {exc}")

        healthy = all(checks.values())
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={
                "status": "ok" if healthy else "degraded",
                "app": settings.APP_NAME,
                "version": settings.APP_VERSION,
                "checks": checks,
            },
        )

    # ---- Root ----
    @app.get("/", tags=["meta"], summary="Service index")
    async def root() -> dict:
        return {
            "service": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "endpoints": {
                "stripe_webhook": "POST /webhooks/stripe",
                "generic_webhook": "POST /webhooks/generic",
                "admin_dlq_list": "GET /admin/dlq",
                "admin_dlq_replay": "POST /admin/dlq/{id}/replay",
                "admin_dlq_resolve": "POST /admin/dlq/{id}/resolve",
                "health": "GET /health",
                "docs": "GET /docs",
            },
        }

    return app


app = create_app()