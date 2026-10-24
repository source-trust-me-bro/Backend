"""How many readers opened a story, clicked through to the original, or shared it."""

from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import (
    Database,
    LanguageFilter,
    Profile,
    get_article_repo,
    get_current_profile,
    require_editor,
)
from app.config import Settings, get_settings
from app.core.exceptions import NotFoundError
from app.core.utils import require_object_id, to_object_id, utcnow
from app.repositories.articles import ArticleRepository
from app.repositories.clicks import KINDS, ClickRepository, with_clicks
from app.services.newsroom_client import NewsroomClient
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


class AudienceRow(BaseModel):
    community_id: str | None = None
    name: str
    readers: int
    clicks: int


class ReportArticle(BaseModel):
    id: str
    title: str
    source_name: str
    url: str
    language: str
    written_for: str | None = None
    topics: list[str] = Field(default_factory=list)
    outlets: int = 1
    open: dict[str, int]
    outbound: dict[str, int]
    share: dict[str, int]
    readers: int
    last_click_at: datetime | None = None
    audience: list[AudienceRow] = Field(default_factory=list)
    why: list[str] = Field(default_factory=list)


class ClicksReport(BaseModel):
    days: int
    readers: int
    open: dict[str, int]
    outbound: dict[str, int]
    share: dict[str, int]
    communities: list[AudienceRow]
    articles: list[ReportArticle]


NO_COMMUNITY = "Not from a community feed"


async def community_names(database: Any, settings: Settings, ids: set[str]) -> dict[str, str]:
    """Display names for community ids: local communities first, then the newsroom's."""

    names: dict[str, str] = {}
    object_ids = [oid for oid in (to_object_id(value) for value in ids) if oid is not None]
    if object_ids:
        async for doc in database["communities"].find({"_id": {"$in": object_ids}}, {"name": 1}):
            names[str(doc["_id"])] = doc.get("name") or str(doc["_id"])
    if ids - names.keys():
        try:
            listed = await NewsroomClient(settings).request("GET", "/communities")
            names |= {c["id"]: c["name"] for c in listed if c["id"] not in names}
        except Exception:  # noqa: BLE001 - ids are still shown when the newsroom is down
            pass
    return names


@router.get(
    "/report",
    response_model=ClicksReport,
    summary="Editors: which stories readers click and which communities they come from",
)
async def clicks_report(
    database: Database,
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=200),
    _editor: Profile = Depends(require_editor),
    settings: Settings = Depends(get_settings),
    clicks: ClickRepository = Depends(get_click_repo),
    articles: ArticleRepository = Depends(get_article_repo),
) -> ClicksReport:
    """Aggregates only. A reader is counted, never named."""

    since = utcnow() - timedelta(days=days)
    rows = await clicks.breakdown(since)

    per_article: dict[Any, dict[str, Any]] = {}
    per_community: dict[str | None, dict[str, int]] = {}
    totals = {kind: {"users": 0, "total": 0} for kind in KINDS}
    for row in rows:
        key = row["_id"]
        kind, community = key["kind"], key.get("community")
        entry = per_article.setdefault(
            key["article"], {"kinds": {k: {"users": 0, "total": 0} for k in KINDS}, "by": {}}
        )
        entry["kinds"][kind]["users"] += row["readers"]
        entry["kinds"][kind]["total"] += row["clicks"]
        entry["last"] = max(entry.get("last") or row["last_at"], row["last_at"])
        for bucket in (
            entry["by"].setdefault(community, {"readers": 0, "clicks": 0}),
            per_community.setdefault(community, {"readers": 0, "clicks": 0}),
        ):
            bucket["readers"] += row["readers"]
            bucket["clicks"] += row["clicks"]
        totals[kind]["users"] += row["readers"]
        totals[kind]["total"] += row["clicks"]

    ranked = sorted(
        per_article.items(),
        key=lambda item: (
            -sum(k["users"] for k in item[1]["kinds"].values()),
            -sum(k["total"] for k in item[1]["kinds"].values()),
        ),
    )[:limit]
    found = await articles.summaries_by_ids([article_id for article_id, _ in ranked])

    topic_ids = list({tid for doc in found.values() for tid in doc.get("topic_ids") or []})
    topic_names = {
        doc["_id"]: doc.get("name", "")
        async for doc in database["topics"].find({"_id": {"$in": topic_ids}}, {"name": 1})
    }
    event_ids = list({doc["event_id"] for doc in found.values() if doc.get("event_id")})
    outlets: dict[str, int] = {}
    if event_ids:
        for name in await articles.language_collection_names():
            async for row in database[name].aggregate(
                [
                    {"$match": {"event_id": {"$in": event_ids}}},
                    {"$group": {"_id": {"e": "$event_id", "s": "$source_name"}}},
                    {"$group": {"_id": "$_id.e", "n": {"$sum": 1}}},
                ]
            ):
                outlets[row["_id"]] = outlets.get(row["_id"], 0) + row["n"]

    wanted = {str(c) for c in per_community if c} | {
        str(doc["community_id"]) for doc in found.values() if doc.get("community_id")
    }
    names = await community_names(database, settings, wanted)

    def label(community: Any) -> str:
        return NO_COMMUNITY if not community else names.get(str(community), str(community))

    def audience_rows(by: dict[str | None, dict[str, int]]) -> list[AudienceRow]:
        return [
            AudienceRow(community_id=c, name=label(c), **sums)
            for c, sums in sorted(by.items(), key=lambda kv: -kv[1]["readers"])
        ]

    result: list[ReportArticle] = []
    for article_id, entry in ranked:
        doc = found.get(article_id)
        if doc is None:
            continue
        kinds = entry["kinds"]
        topics = [topic_names[t] for t in doc.get("topic_ids") or [] if topic_names.get(t)]
        written_for = label(doc["community_id"]) if doc.get("community_id") else None
        audience = audience_rows(entry["by"])
        n_outlets = outlets.get(doc.get("event_id") or "", 1)
        why: list[str] = []
        if written_for:
            why.append(f"Written for {written_for}")
        if topics:
            why.append("Filed under " + ", ".join(topics))
        if n_outlets > 1:
            why.append(f"Same news reported by {n_outlets} outlets")
        top = next((a for a in audience if a.community_id), None)
        if top:
            why.append(f"Most readers come from {top.name} ({top.readers})")
        result.append(
            ReportArticle(
                id=str(article_id),
                title=doc.get("title", ""),
                source_name=doc.get("source_name", ""),
                url=doc.get("url", ""),
                language=doc.get("language", ""),
                written_for=written_for,
                topics=topics,
                outlets=n_outlets,
                open=kinds["open"],
                outbound=kinds["outbound"],
                share=kinds["share"],
                readers=max(k["users"] for k in kinds.values()),
                last_click_at=entry.get("last"),
                audience=audience,
                why=why,
            )
        )

    return ClicksReport(
        days=days,
        readers=await clicks.unique_readers(since),
        open=totals["open"],
        outbound=totals["outbound"],
        share=totals["share"],
        communities=audience_rows(per_community),
        articles=result,
    )


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
