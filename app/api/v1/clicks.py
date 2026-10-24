"""How many readers opened a story, clicked through to the original, or shared it."""

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import (
    Database,
    LanguageFilter,
    Profile,
    get_article_repo,
    get_current_profile,
)
from app.core.exceptions import NotFoundError
from app.core.utils import require_object_id, utcnow
from app.repositories.articles import ArticleRepository
from app.repositories.clicks import ClickRepository, with_clicks
from app.schemas.article import ArticleSummary

router = APIRouter(prefix="/clicks", tags=["clicks"])


def get_click_repo(database: Database) -> ClickRepository:
    return ClickRepository(database)


class ClickIn(BaseModel):
    kind: Literal["open", "outbound", "share"] = "outbound"
    community_id: str | None = Field(default=None, max_length=64)


class ClickStats(BaseModel):
    open_users: int = 0
    open_total: int = 0
    outbound_users: int = 0
    outbound_total: int = 0
    share_users: int = 0
    share_total: int = 0


class TopClicked(BaseModel):
    article: ArticleSummary
    users: int
    clicks: int


@router.get("/top", response_model=list[TopClicked], summary="Stories most readers clicked")
async def top_clicked(
    language: LanguageFilter,
    kind: Literal["open", "outbound", "share"] = Query(default="outbound"),
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=10, ge=1, le=50),
    _profile: Profile = Depends(get_current_profile),
    clicks: ClickRepository = Depends(get_click_repo),
    articles: ArticleRepository = Depends(get_article_repo),
) -> list[TopClicked]:
    rows = await clicks.most_clicked(utcnow() - timedelta(days=days), kind, limit * 3)
    found = await articles.summaries_by_ids([row["_id"] for row in rows])
    picked = [
        (row, found[row["_id"]])
        for row in rows
        if row["_id"] in found and (not language or found[row["_id"]].get("language") == language)
    ][:limit]
    await with_clicks(clicks.database, [doc for _, doc in picked])
    return [
        TopClicked(
            article=ArticleSummary.model_validate(doc), users=row["users"], clicks=row["clicks"]
        )
        for row, doc in picked
    ]


@router.post("/{article_id}", response_model=ClickStats, summary="Count a click on a story")
async def record_click(
    article_id: str,
    payload: ClickIn,
    profile: Profile = Depends(get_current_profile),
    clicks: ClickRepository = Depends(get_click_repo),
    articles: ArticleRepository = Depends(get_article_repo),
) -> ClickStats:
    if not await articles.exists(article_id):
        raise NotFoundError(f"Article '{article_id}' was not found.")
    stats = await clicks.record(
        profile["clerk_user_id"],
        require_object_id(article_id),
        payload.kind,
        community_id=payload.community_id,
    )
    return ClickStats(**stats)


@router.get("/{article_id}", response_model=ClickStats, summary="Click counts for a story")
async def click_stats(
    article_id: str,
    _profile: Profile = Depends(get_current_profile),
    clicks: ClickRepository = Depends(get_click_repo),
) -> ClickStats:
    return ClickStats(**await clicks.stats(require_object_id(article_id)))
