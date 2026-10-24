"""Copies what the newsroom publishes to the dashboard into the reader app's own collections.

The reader features (feeds, trending, categories, following, saved, history, downloads) all
read the app's ``articles``, ``topics`` and ``sources``. The newsroom keeps its own database,
so after an editor publishes, this service copies:

* the topic tree, with each topic's ``branches`` (related topics) mapped to app ids;
* each outlet a story cites, as a source readers can follow;
* each published story once per language, so readers see it in the language they chose.

Copies are matched by newsroom ids, so running the sync again updates instead of duplicating.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any
from urllib.parse import urlparse

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.core.utils import slugify, utcnow
from app.repositories.articles import ArticleRepository, collection_name_for
from app.repositories.sources import SourceRepository
from app.repositories.topics import TopicRepository
from app.services.embedding_service import EmbeddingService
from app.services.newsroom_client import NewsroomClient

logger = logging.getLogger(__name__)

AUTHOR = "KalevaShorts newsroom"

TOPIC_REFRESH_SECONDS = 60
_last_topic_attempt: dict[str, float] = {}
_background: set[asyncio.Task[None]] = set()
_SECTION = re.compile(r"^##\s+.*$", re.MULTILINE)


def split_story(body: str | None) -> tuple[str | None, str | None]:
    """A newsroom story is '## What happened' then '## What it means for you', in any language.

    Returns (basic facts, explanation): the first section, and everything after it. A body
    without at least two sections is all explanation."""

    parts = [part.strip() for part in _SECTION.split(body or "") if part.strip()]
    if len(parts) >= 2:
        return parts[0], "\n\n".join(parts[1:])
    return None, (body or None)


async def sync_topic_docs(collection: Any, topics: list[dict[str, Any]]) -> dict[str, ObjectId]:
    """Copy the newsroom's topic tree into the reader app's ``topics`` collection.

    One read finds what is already there; only topics that are new or changed are written, so a
    repeat run with nothing new does no writes at all."""

    ids: dict[str, ObjectId] = {}
    if not topics:
        return ids
    now = utcnow()
    existing = {
        doc["newsroom_id"]: doc
        async for doc in collection.find({"newsroom_id": {"$in": [t["id"] for t in topics]}})
    }

    for topic in topics:
        fields = {
            "name": topic["name"][:120],
            "language": "en",
            "description": topic.get("description") or None,
            "keywords": sorted({k.strip().lower() for k in topic.get("keywords") or [] if k}),
        }
        found = existing.get(topic["id"])
        if found is not None:
            ids[topic["id"]] = found["_id"]
            if any(found.get(key) != value for key, value in fields.items()):
                await collection.update_one(
                    {"_id": found["_id"]}, {"$set": {**fields, "updated_at": now}}
                )
            continue
        slug = slugify(topic["name"], fallback_prefix="topic")
        for candidate in (slug, f"{slug[:70]}-{topic['id'][-6:]}"):
            try:
                result = await collection.insert_one(
                    {
                        **fields,
                        "updated_at": now,
                        "slug": candidate,
                        "newsroom_id": topic["id"],
                        "parent_id": None,
                        "branch_ids": [],
                        "created_at": now,
                    }
                )
            except DuplicateKeyError:
                # Another worker may have just added this very topic: use that one.
                twin = await collection.find_one({"newsroom_id": topic["id"]})
                if twin is not None:
                    ids[topic["id"]] = twin["_id"]
                    break
                continue
            ids[topic["id"]] = result.inserted_id
            existing[topic["id"]] = {"_id": result.inserted_id, "parent_id": None, "branch_ids": []}
            break

    # Second pass: parents and branches can only be mapped once every topic has an id.
    for topic in topics:
        if topic["id"] not in ids:
            continue
        parent = ids.get(topic.get("parent_id") or "")
        branches = [ids[b] for b in topic.get("branches") or [] if b in ids]
        found = existing.get(topic["id"]) or {}
        if found.get("parent_id") != parent or found.get("branch_ids") != branches:
            await collection.update_one(
                {"_id": ids[topic["id"]]},
                {"$set": {"parent_id": parent, "branch_ids": branches}},
            )
    return ids


async def auto_sync_topics(settings: Any, topic_repo: Any) -> None:
    """Keep the reader's topics in step with the newsroom without anyone pressing a button.

    Called when the topic tree is read. At most once a minute per process it asks the newsroom
    for its topics (a cheap call). With an empty tree the reader waits for it once; otherwise it
    runs in the background so the page is never slowed. Failures are logged and never break the
    page; the next read tries again soon."""

    base = (settings.newsroom_url or "").rstrip("/")
    if not base:
        return
    now = time.monotonic()
    last = _last_topic_attempt.get(base)
    if last is not None and now - last < TOPIC_REFRESH_SECONDS:
        return
    _last_topic_attempt[base] = now

    async def run() -> None:
        try:
            data = await asyncio.wait_for(
                NewsroomClient(settings).request("GET", "/export/topics"), timeout=8
            )
            await sync_topic_docs(topic_repo.collection, data.get("topics") or [])
        except Exception:
            logger.warning("Automatic topic sync failed; will retry", exc_info=True)
            _last_topic_attempt[base] = time.monotonic() - TOPIC_REFRESH_SECONDS + 10

    if await topic_repo.collection.count_documents({}, limit=1) == 0:
        await run()
        return
    task = asyncio.create_task(run())
    _background.add(task)
    task.add_done_callback(_background.discard)


class NewsroomSync:
    def __init__(
        self,
        *,
        client: NewsroomClient,
        article_repo: ArticleRepository,
        topic_repo: TopicRepository,
        source_repo: SourceRepository,
        embedding_service: EmbeddingService,
    ) -> None:
        self.client = client
        self.articles = article_repo
        self.topics = topic_repo
        self.sources = source_repo
        self.embedding = embedding_service

    async def run(self) -> dict[str, int]:
        data = await self.client.request("GET", "/export/dashboard")
        topic_ids = await self._sync_topics(data.get("topics") or [])
        created = updated = 0
        for story in data.get("stories") or []:
            source = await self._source_for(story.get("sources") or [])
            for version in story.get("versions") or []:
                if await self._sync_article(story, version, source, topic_ids):
                    created += 1
                else:
                    updated += 1
        return {
            "topics": len(topic_ids),
            "stories": len(data.get("stories") or []),
            "articles_created": created,
            "articles_updated": updated,
        }

    # ------------------------------------------------------------------ topics

    async def _sync_topics(self, topics: list[dict[str, Any]]) -> dict[str, ObjectId]:
        return await sync_topic_docs(self.topics.collection, topics)

    # ----------------------------------------------------------------- sources

    async def _source_for(self, refs: list[dict[str, Any]]) -> dict[str, Any] | None:
        """The outlet a story is filed under: its official source if it has one."""

        primary = next((r for r in refs if r.get("official")), None) or (refs[0] if refs else None)
        if not primary or not primary.get("name"):
            return None
        name = str(primary["name"])[:200]
        collection = self.sources.collection
        existing = await collection.find_one({"newsroom_outlet": name})
        if existing:
            return existing
        url = str(primary.get("url") or "")
        host = urlparse(url).netloc
        doc = {
            "name": name,
            "feed_url": f"newsroom://outlet/{slugify(name, fallback_prefix='outlet')}",
            "homepage_url": f"https://{host}" if host else None,
            "language": "en",
            "description": "Cited by the KalevaShorts newsroom.",
            "active": True,
            "newsroom_outlet": name,
            "created_by": None,
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        try:
            result = await collection.insert_one(doc)
        except DuplicateKeyError:
            return await collection.find_one({"feed_url": doc["feed_url"]})
        doc["_id"] = result.inserted_id
        return doc

    # ---------------------------------------------------------------- articles

    async def _sync_article(
        self,
        story: dict[str, Any],
        version: dict[str, Any],
        source: dict[str, Any] | None,
        topic_ids: dict[str, ObjectId],
    ) -> bool:
        language = version["language"]
        refs = story.get("sources") or []
        original = next((r.get("url") for r in refs if r.get("official")), None) or (
            refs[0].get("url") if refs else None
        )
        # One article per story and language. The link leads to the original reporting.
        anchor = f"ks-{story['story_id']}-{language}"
        url = (
            f"{original}#{anchor}"
            if original
            else f"newsroom://story/{story['story_id']}/{language}"
        )
        title = version["headline"]
        summary = version.get("standfirst") or None
        # Embedding every story again on every sync is the slow part: reuse the stored vector
        # while the text it was computed from is unchanged.
        existing = await self.articles.database[collection_name_for(language)].find_one(
            {"url": url}, {"title": 1, "summary": 1, "embedding": 1}
        )
        if (
            existing
            and existing.get("embedding")
            and existing.get("title") == title
            and existing.get("summary") == summary
        ):
            embedding = existing["embedding"]
        else:
            embedding = await asyncio.to_thread(self.embedding.embed, f"{title}\n\n{summary or ''}")
        basic_info, explanation = split_story(version.get("body_md"))
        _, created = await self.articles.upsert(
            {
                "source_id": source["_id"] if source else None,
                "source_name": source["name"] if source else AUTHOR,
                "url": url,
                "external_id": anchor,
                "title": title,
                "summary": summary,
                "content": version.get("body_md") or None,
                "language": language,
                "author": AUTHOR,
                "published_at": story.get("published_at"),
                "fetched_at": utcnow(),
                "topic_ids": [topic_ids[t] for t in story.get("topic_ids") or [] if t in topic_ids],
                "embedding": embedding,
                "newsroom_story_id": story["story_id"],
                "community_id": story.get("community_id"),
                "image_url": story.get("image_url"),
                "event_id": (story.get("event_ids") or [None])[0],
                "basic_info": basic_info,
                "explanation": explanation,
            }
        )
        return created
