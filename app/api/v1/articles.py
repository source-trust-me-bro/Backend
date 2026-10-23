from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import (
    LanguageFilter,
    Profile,
    get_article_repo,
    get_current_user,
    require_editor,
)
from app.config import Settings, get_settings
from app.core.exceptions import NotFoundError
from app.repositories.articles import ArticleRepository, tokenize_query
from app.schemas.article import (
    ArticleDetail,
    ArticleSummary,
    ArticleTranslationCreate,
    ArticleTranslationOut,
)
from app.schemas.base import Page
from app.schemas.user import ClerkPrincipal

router = APIRouter(prefix="/articles", tags=["articles"])


@router.get("", response_model=Page[ArticleSummary], summary="Browse and search articles")
async def list_articles(
    language: LanguageFilter,
    source_id: str | None = Query(default=None),
    topic_id: str | None = Query(default=None),
    q: str | None = Query(default=None, min_length=1, max_length=500),
    published_after: datetime | None = Query(default=None),
    published_before: datetime | None = Query(default=None),
    sort: Literal["newest", "oldest"] = Query(default="newest"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _principal: ClerkPrincipal = Depends(get_current_user),
    repo: ArticleRepository = Depends(get_article_repo),
    settings: Settings = Depends(get_settings),
) -> Page[ArticleSummary]:
    tokens = tokenize_query(q, max_tokens=settings.search_max_tokens) if q else None
    documents, total = await repo.list_articles(
        language=language,
        source_id=source_id,
        topic_ids=[topic_id] if topic_id else None,
        tokens=tokens,
        published_after=published_after,
        published_before=published_before,
        page=page,
        page_size=page_size,
        sort=sort,
    )
    return Page.build(
        [ArticleSummary.model_validate(doc) for doc in documents], total, page, page_size
    )


@router.get("/{article_id}", response_model=ArticleDetail, summary="Fetch an article")
async def get_article(
    article_id: str,
    _principal: ClerkPrincipal = Depends(get_current_user),
    repo: ArticleRepository = Depends(get_article_repo),
) -> ArticleDetail:
    article = await repo.get_by_id(article_id)
    if article is None:
        raise NotFoundError(f"Article '{article_id}' was not found.")
    return ArticleDetail.model_validate(article)


@router.get(
    "/{article_id}/related",
    response_model=list[ArticleSummary],
    summary="Articles sharing topics with this article",
)
async def get_related_articles(
    article_id: str,
    limit: int = Query(default=5, ge=1, le=20),
    _principal: ClerkPrincipal = Depends(get_current_user),
    repo: ArticleRepository = Depends(get_article_repo),
) -> list[ArticleSummary]:
    article = await repo.get_by_id(article_id)
    if article is None:
        raise NotFoundError(f"Article '{article_id}' was not found.")
    related = await repo.related(article, limit=limit)
    return [ArticleSummary.model_validate(doc) for doc in related]


@router.get(
    "/{article_id}/translations",
    response_model=list[ArticleTranslationOut],
    summary="Stored translations for an article",
)
async def list_translations(
    article_id: str,
    language: LanguageFilter,
    _principal: ClerkPrincipal = Depends(get_current_user),
    repo: ArticleRepository = Depends(get_article_repo),
) -> list[ArticleTranslationOut]:
    if not await repo.exists(article_id):
        raise NotFoundError(f"Article '{article_id}' was not found.")
    translations = await repo.get_translations(article_id, language=language)
    return [ArticleTranslationOut.model_validate(doc) for doc in translations]


@router.post(
    "/{article_id}/translations",
    response_model=ArticleTranslationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Store a translation for an article (editor only)",
)
async def create_translation(
    article_id: str,
    payload: ArticleTranslationCreate,
    _editor: Profile = Depends(require_editor),
    repo: ArticleRepository = Depends(get_article_repo),
) -> ArticleTranslationOut:
    if not await repo.exists(article_id):
        raise NotFoundError(f"Article '{article_id}' was not found.")
    translation, _created = await repo.add_translation(article_id, payload.model_dump())
    return ArticleTranslationOut.model_validate(translation)
