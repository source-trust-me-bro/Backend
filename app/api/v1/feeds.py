from fastapi import APIRouter, Depends, Query

from app.api.deps import (
    LanguageFilter,
    Profile,
    get_article_repo,
    get_community_repo,
    get_current_profile,
    get_related_service,
)
from app.repositories.articles import ArticleRepository
from app.repositories.clicks import with_clicks
from app.repositories.communities import CommunityRepository
from app.schemas.article import ArticleSummary
from app.schemas.base import Page
from app.services.related import RelatedService

router = APIRouter(prefix="/feeds", tags=["feeds"])


@router.get(
    "/personalized",
    response_model=Page[ArticleSummary],
    summary="Feed tailored to the caller's language, community, and topics",
)
async def personalized_feed(
    language: LanguageFilter,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    profile: Profile = Depends(get_current_profile),
    community_repo: CommunityRepository = Depends(get_community_repo),
    article_repo: ArticleRepository = Depends(get_article_repo),
) -> Page[ArticleSummary]:
    target_language = language or profile.get("preferred_language")

    topic_ids = [str(value) for value in profile.get("topic_ids") or []]
    if profile.get("community_id"):
        community = await community_repo.get_by_id(profile["community_id"])
        if community is not None:
            topic_ids.extend(str(value) for value in community.get("topic_ids") or [])
    topic_ids = list(dict.fromkeys(topic_ids))

    documents, total = await article_repo.list_articles(
        language=target_language,
        topic_ids=topic_ids or None,
        page=page,
        page_size=page_size,
    )
    return Page.build(
        [ArticleSummary.model_validate(doc) for doc in documents], total, page, page_size
    )


@router.get(
    "/following",
    response_model=Page[ArticleSummary],
    summary="Newest articles from the news sources the caller follows",
)
async def following_feed(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    profile: Profile = Depends(get_current_profile),
    article_repo: ArticleRepository = Depends(get_article_repo),
) -> Page[ArticleSummary]:
    source_ids = [str(value) for value in profile.get("followed_source_ids") or []]
    documents, total = await article_repo.list_articles(
        source_ids=source_ids, page=page, page_size=page_size
    )
    return Page.build(
        [ArticleSummary.model_validate(doc) for doc in documents], total, page, page_size
    )


@router.get(
    "/community/{community_id}",
    response_model=list[ArticleSummary],
    summary="Stories for one newsroom community, ranked from the news tree",
)
async def community_feed(
    community_id: str,
    language: LanguageFilter,
    limit: int = Query(default=20, ge=1, le=50),
    _profile: Profile = Depends(get_current_profile),
    related: RelatedService = Depends(get_related_service),
) -> list[ArticleSummary]:
    """``community_id`` is the newsroom's id (``lapland-seasonal``, ``sapmi-north``...), which
    the newsroom sync stores on every story it copies."""

    found = await related.for_community(community_id, language=language, limit=limit)
    await with_clicks(related.db, found)
    return [ArticleSummary.model_validate(doc) for doc in found]
