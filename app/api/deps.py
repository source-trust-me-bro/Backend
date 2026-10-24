"""Shared FastAPI dependencies: repositories, services, profiles, and RBAC."""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import Settings, get_settings
from app.core.exceptions import BadRequestError, ForbiddenError
from app.core.security import get_current_user
from app.database import get_database
from app.repositories.articles import ArticleRepository
from app.repositories.communities import CommunityRepository
from app.repositories.feedback import FeedbackRepository
from app.repositories.ingestion_jobs import IngestionJobRepository
from app.repositories.library import LibraryRepository
from app.repositories.sources import SourceRepository
from app.repositories.topics import TopicRepository
from app.repositories.users import UserRepository
from app.schemas.user import ClerkPrincipal
from app.services.clerk_service import fetch_clerk_user
from app.services.embedding_service import EmbeddingService
from app.services.extraction_service import ExtractionService
from app.services.ingestion_service import IngestionService
from app.services.related import RelatedService
from app.services.rss_service import RSSService
from app.services.search_service import SearchService

Profile = dict[str, Any]

Database = Annotated[AsyncIOMotorDatabase, Depends(get_database)]


def get_user_repo(database: Database) -> UserRepository:
    return UserRepository(database)


def get_source_repo(database: Database) -> SourceRepository:
    return SourceRepository(database)


def get_topic_repo(database: Database) -> TopicRepository:
    return TopicRepository(database)


def get_community_repo(database: Database) -> CommunityRepository:
    return CommunityRepository(database)


def get_article_repo(database: Database) -> ArticleRepository:
    return ArticleRepository(database)


def get_library_repo(database: Database) -> LibraryRepository:
    return LibraryRepository(database)


def get_feedback_repo(database: Database) -> FeedbackRepository:
    return FeedbackRepository(database)


def get_job_repo(database: Database) -> IngestionJobRepository:
    return IngestionJobRepository(database)


def get_rss_service(settings: Settings = Depends(get_settings)) -> RSSService:
    return RSSService(settings=settings)


def get_extraction_service(settings: Settings = Depends(get_settings)) -> ExtractionService:
    return ExtractionService(settings=settings)


_embedding_service: EmbeddingService | None = None


def get_embedding_service(
    settings: Settings = Depends(get_settings),
) -> EmbeddingService:
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService(settings=settings)
    return _embedding_service


def get_search_service(
    article_repo: ArticleRepository = Depends(get_article_repo),
    embedding_service: EmbeddingService = Depends(get_embedding_service),
    settings: Settings = Depends(get_settings),
) -> SearchService:
    return SearchService(article_repo, embedding_service, settings)


def get_related_service(
    article_repo: ArticleRepository = Depends(get_article_repo),
) -> RelatedService:
    return RelatedService(article_repo)


def get_ingestion_service(
    source_repo: SourceRepository = Depends(get_source_repo),
    article_repo: ArticleRepository = Depends(get_article_repo),
    job_repo: IngestionJobRepository = Depends(get_job_repo),
    topic_repo: TopicRepository = Depends(get_topic_repo),
    rss_service: RSSService = Depends(get_rss_service),
    extraction_service: ExtractionService = Depends(get_extraction_service),
    embedding_service: EmbeddingService = Depends(get_embedding_service),
    settings: Settings = Depends(get_settings),
) -> IngestionService:
    return IngestionService(
        source_repo=source_repo,
        article_repo=article_repo,
        job_repo=job_repo,
        topic_repo=topic_repo,
        rss_service=rss_service,
        extraction_service=extraction_service,
        embedding_service=embedding_service,
        settings=settings,
    )


async def get_current_profile(
    principal: ClerkPrincipal = Depends(get_current_user),
    user_repo: UserRepository = Depends(get_user_repo),
    settings: Settings = Depends(get_settings),
) -> Profile:
    """Return the local profile, provisioning it on first authentication."""

    # Clerk's default session token only carries `sub`, so look up the email and name.
    if principal.email is None:
        directory_entry = await fetch_clerk_user(principal.sub, settings)
        if directory_entry is not None:
            principal = principal.model_copy(
                update={
                    "email": directory_entry.email,
                    "display_name": principal.display_name or directory_entry.display_name,
                    "username": principal.username or directory_entry.username,
                    "image_url": principal.image_url or directory_entry.image_url,
                }
            )

    return await user_repo.sync_from_principal(
        principal, default_language=settings.default_language
    )


def require_roles(*roles: str) -> Callable[..., Awaitable[Profile]]:
    allowed = set(roles)

    async def dependency(profile: Profile = Depends(get_current_profile)) -> Profile:
        granted = set(profile.get("roles") or [])
        if not granted.intersection(allowed):
            raise ForbiddenError(
                "This action requires one of the following roles: "
                + ", ".join(sorted(allowed))
                + "."
            )
        return profile

    return dependency


require_editor = require_roles("editor", "admin")
require_admin = require_roles("admin")


def validated_language(
    language: str | None = Query(
        default=None, description="ISO 639-1 language code (e.g. en, fi, sv)."
    ),
    settings: Settings = Depends(get_settings),
) -> str | None:
    if language is None:
        return None
    code = language.strip().lower()
    if code not in settings.supported_languages:
        raise BadRequestError(
            f"Unsupported language '{language}'. Supported languages: "
            + ", ".join(settings.supported_languages)
            + "."
        )
    return code


LanguageFilter = Annotated[str | None, Depends(validated_language)]
