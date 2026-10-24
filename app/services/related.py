"""Related news and per-community recommendations, read from the news tree.

The newsroom already builds the tree: every story belongs to an **event** (the same news from
several outlets), events are filed under **topics**, topics have a parent and children, and the
Connector agent links related topics as **branches**. This service walks that tree and scores
each candidate:

    same event (another outlet)          +6     strongest: it is the same news
    shared topic                          +3 each (max 2)
    branch topic (Connector link)        +1.5
    parent or child topic                +1
    meaning (embedding cosine)           +4 x cosine
    for the reader's community           +2
    popularity (unique readers, clicks)  +1 x log(1 + readers + 2 x clickers)
    freshness                            x (0.25 .. 1), half-life 5 days

then keeps at most two stories per outlet so one source cannot fill the list. Every result
carries the reasons, so the page can say *why* it is shown ("same news, another outlet").
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.utils import cosine_similarity, utcnow
from app.repositories.articles import ArticleRepository, collection_name_for

W = {"event": 6.0, "topic": 3.0, "branch": 1.5, "family": 1.0, "meaning": 4.0, "group": 2.0,
     "popular": 1.0, "affinity": 3.0}  # fmt: skip
HALF_LIFE_DAYS = 5.0
WINDOW_DAYS = 30
PER_SOURCE = 2
POOL = 300


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _freshness(doc: dict[str, Any]) -> float:
    when = _aware(doc.get("published_at")) or _aware(doc.get("created_at"))
    if when is None:
        return 0.5
    age = max(0.0, (utcnow() - when).total_seconds() / 86400)
    return 0.25 + 0.75 * 0.5 ** (age / HALF_LIFE_DAYS)


_OUTLETS = [  # same grouping as the newsroom's outlets.py: one publisher, however many feeds
    (
        "City of Oulu",
        ("city of oulu", "oulun kaupunki", "mun oulu", "munoulu", "businessoulu", "business oulu"),
    ),
    ("OSL", ("oulu region public transport", "(osl)")),
    ("Migri", ("migri", "immigration service")),
    ("Kaleva", ("kaleva",)),
    ("Yle", ("yle",)),
    ("BBC", ("bbc",)),
]


def outlet_of(source_name: str | None) -> str:
    name = (source_name or "").casefold()
    for outlet, needles in _OUTLETS:
        if any(needle in name for needle in needles):
            return outlet
    return source_name or ""


def _diverse(scored: list[tuple[float, dict[str, Any]]], limit: int) -> list[dict[str, Any]]:
    per_source: Counter[str] = Counter()
    out = []
    for _, doc in sorted(scored, key=lambda pair: -pair[0]):
        source = outlet_of(doc.get("source_name"))
        if per_source[source] >= PER_SOURCE:
            continue
        per_source[source] += 1
        doc.pop("embedding", None)
        out.append(doc)
        if len(out) >= limit:
            break
    return out


class RelatedService:
    def __init__(self, article_repo: ArticleRepository) -> None:
        self.articles = article_repo
        self.db = article_repo.database

    # ------------------------------------------------------------------ the tree

    async def _neighbours(self, topic_ids: list[Any]) -> tuple[set[Any], set[Any]]:
        """Topics one step away: branches (Connector links) and family (parent, children)."""

        if not topic_ids:
            return set(), set()
        topics = self.db["topics"]
        own = [
            d
            async for d in topics.find(
                {"_id": {"$in": topic_ids}}, {"parent_id": 1, "branch_ids": 1}
            )
        ]
        branches = {b for d in own for b in d.get("branch_ids") or []}
        family = {d["parent_id"] for d in own if d.get("parent_id")}
        family |= {
            d["_id"] async for d in topics.find({"parent_id": {"$in": topic_ids}}, {"_id": 1})
        }
        mine = set(topic_ids)
        return branches - mine, family - mine - branches

    async def _popularity(self, ids: list[Any]) -> dict[Any, float]:
        if not ids:
            return {}
        rows = self.db["article_stats"].find({"_id": {"$in": ids}})
        return {
            row["_id"]: row.get("open_users", 0) + 2 * row.get("outbound_users", 0)
            async for row in rows
        }

    # --------------------------------------------------------------- related news

    async def related(
        self,
        article: dict[str, Any],
        *,
        limit: int = 6,
        communities: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        topic_ids = list(article.get("topic_ids") or [])
        branches, family = await self._neighbours(topic_ids)
        near = list(set(topic_ids) | branches | family)

        clauses: list[dict[str, Any]] = []
        if article.get("event_id"):
            clauses.append({"event_id": article["event_id"]})
        if near:
            clauses.append({"topic_ids": {"$in": near}})
        if article.get("community_id"):
            clauses.append({"community_id": article["community_id"]})
        if not clauses:
            return []
        since = utcnow() - timedelta(days=WINDOW_DAYS)
        query: dict[str, Any] = {
            "_id": {"$ne": article["_id"]},
            "$and": [
                {"$or": clauses},
                {"$or": [{"published_at": {"$gte": since}}, {"published_at": None}]},
            ],
        }
        if article.get("newsroom_story_id"):  # the same story in another language is not "related"
            query["newsroom_story_id"] = {"$ne": article["newsroom_story_id"]}

        collection = self.db[collection_name_for(article.get("language"))]
        cursor = collection.find(query, {"content": 0}).sort("published_at", -1).limit(POOL)
        pool = [doc async for doc in cursor]
        popular = await self._popularity([d["_id"] for d in pool])

        mine = set(topic_ids)
        groups = set(communities or [])
        if article.get("community_id"):
            groups.add(article["community_id"])
        vector = article.get("embedding") or []

        scored = []
        for doc in pool:
            theirs = set(doc.get("topic_ids") or [])
            score, reasons = 0.0, []
            if article.get("event_id") and doc.get("event_id") == article["event_id"]:
                score += W["event"]
                reasons.append("Same news from another outlet")
            shared = mine & theirs
            if shared:
                score += W["topic"] * min(2, len(shared))
                reasons.append("Same topic")
            if theirs & branches:
                score += W["branch"]
                reasons.append("Related topic")
            if theirs & family:
                score += W["family"]
            if vector and doc.get("embedding"):
                score += W["meaning"] * max(0.0, cosine_similarity(vector, doc["embedding"]))
            if doc.get("community_id") and doc["community_id"] in groups:
                score += W["group"]
                reasons.append("For your community")
            score += W["popular"] * math.log1p(popular.get(doc["_id"], 0))
            score *= _freshness(doc)
            if score > 0.5:
                doc["reasons"] = reasons
                scored.append((score, doc))
        return _diverse(scored, limit)

    # ---------------------------------------------------- one community's feed

    async def for_community(
        self,
        community_id: str,
        *,
        language: str | None = None,
        limit: int = 20,
        days: int = WINDOW_DAYS,
    ) -> list[dict[str, Any]]:
        """Stories for one group: its own stories first, then stories from the topics (and the
        Connector's related topics) the group reads most."""

        names = (
            [collection_name_for(language)]
            if language
            else await self.articles.language_collection_names()
        )
        since = utcnow() - timedelta(days=days)
        window = {"$or": [{"published_at": {"$gte": since}}, {"published_at": None}]}

        # What this group reads: topic counts over its own stories.
        affinity: Counter[Any] = Counter()
        for name in names:
            async for doc in self.db[name].find(
                {"community_id": community_id, **window}, {"topic_ids": 1}
            ):
                affinity.update(doc.get("topic_ids") or [])
        top_topics = [t for t, _ in affinity.most_common(8)]
        branches, _ = await self._neighbours(top_topics)
        total = sum(affinity.values()) or 1

        clauses: list[dict[str, Any]] = [{"community_id": community_id}]
        if top_topics or branches:
            clauses.append({"topic_ids": {"$in": list(set(top_topics) | branches)}})
        pool: list[dict[str, Any]] = []
        for name in names:
            cursor = (
                self.db[name]
                .find({"$and": [{"$or": clauses}, window]}, {"content": 0})
                .sort("published_at", -1)
                .limit(POOL)
            )
            pool += [doc async for doc in cursor]
        popular = await self._popularity([d["_id"] for d in pool])

        seen_stories: set[Any] = set()
        scored = []
        for doc in pool:
            story = doc.get("newsroom_story_id")
            if story and story in seen_stories:
                continue  # one language edition per story
            if story:
                seen_stories.add(story)
            theirs = set(doc.get("topic_ids") or [])
            score, reasons = 0.0, []
            if doc.get("community_id") == community_id:
                score += W["group"] * 1.5
                reasons.append("Written for this community")
            fit = sum(affinity[t] for t in theirs) / total
            if fit:
                score += W["affinity"] * min(1.0, fit * 3)
                reasons.append("A topic this community follows")
            elif theirs & branches:
                score += W["branch"]
                reasons.append("Related to what this community follows")
            score += W["popular"] * math.log1p(popular.get(doc["_id"], 0))
            score *= _freshness(doc)
            doc["reasons"] = reasons
            scored.append((score, doc))
        return _diverse(scored, limit)
