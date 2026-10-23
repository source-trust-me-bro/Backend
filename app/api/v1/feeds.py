from fastapi import APIRouter, Depends, Query

from app.api.deps import (
    LanguageFilter,
    Profile,
    get_article_repo,
    get_community_repo,
    get_current_profile,
)
from app.repositories.articles import ArticleRepository
from app.repositories.communities import CommunityRepository
from app.schemas.article import ArticleSummary
from app.schemas.base import Page

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
