import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import (
    articles,
    clicks,
    communities,
    feedback,
    feeds,
    follows,
    health,
    ingestion,
    languages,
    library,
    newsroom,
    sources,
    topics,
    users,
)
from app.config import get_settings
from app.core.exceptions import AppError
from app.database import close_mongo_connection, connect_to_mongo, ensure_indexes
from app.repositories.articles import migrate_legacy_articles
from app.services.newsroom_client import close_newsroom_clients

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    if settings.startup_db_check:
        try:
            database = await connect_to_mongo(settings)
            await ensure_indexes(database)
            moved = await migrate_legacy_articles(database)
            if moved:
                logger.info("Moved articles into per-language collections: %s", moved)
            logger.info("Connected to MongoDB database '%s'", database.name)
        except Exception:
            logger.exception(
                "MongoDB startup check failed; the API is up but /health/ready will fail."
            )

    yield

    await close_newsroom_clients()
    await close_mongo_connection()


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "code": exc.code},
            headers=exc.headers,
        )


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "Multilingual news platform API. Clerk session tokens are validated as RS256 "
            "JWTs against the configured JWKS endpoint."
        ),
        lifespan=lifespan,
    )

    origins = settings.cors_origin_list
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials="*" not in origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _register_exception_handlers(application)

    api_router = APIRouter(prefix="/api/v1")
    api_router.include_router(health.router)
    api_router.include_router(users.router)
    api_router.include_router(sources.router)
    api_router.include_router(ingestion.router)
    api_router.include_router(articles.router)
    api_router.include_router(languages.router)
    api_router.include_router(topics.router)
    api_router.include_router(topics.search_router)
    api_router.include_router(communities.router)
    api_router.include_router(feeds.router)
    api_router.include_router(library.router)
    api_router.include_router(follows.router)
    api_router.include_router(feedback.router)
    api_router.include_router(newsroom.router)
    api_router.include_router(clicks.router)
    application.include_router(api_router)

    @application.get("/", include_in_schema=False)
    async def root() -> dict[str, Any]:
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "environment": settings.app_env,
            "docs": "/docs",
            "health": "/api/v1/health",
        }

    return application


app = create_app()
