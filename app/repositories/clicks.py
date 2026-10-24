"""How many readers opened a story, and how many clicked through to the original report.

Two collections:

* ``article_clicks`` one row per (reader, article, kind). Its count says how often that reader
  clicked; the row itself means "one more unique reader".
* ``article_stats``  one row per article with running totals, so a feed can show counts for
  twenty stories with a single query.
"""

from datetime import datetime
from typing import Any, Literal

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.core.utils import utcnow
from app.repositories.base import BaseRepository

ClickKind = Literal["open", "outbound", "share"]
KINDS: tuple[str, ...] = ("open", "outbound", "share")


def empty_stats() -> dict[str, int]:
    return {f"{kind}_{part}": 0 for kind in KINDS for part in ("users", "total")}


class ClickRepository(BaseRepository):
    collection_name = "article_clicks"

    @property
    def stats_collection(self):
        return self.database["article_stats"]

    async def record(
        self,
        user_id: str,
        article_id: ObjectId,
        kind: ClickKind,
        *,
        community_id: str | None = None,
    ) -> dict[str, int]:
        now = utcnow()
        key = {"clerk_user_id": user_id, "article_id": article_id, "kind": kind}
        update = {
            "$inc": {"count": 1},
            "$set": {"last_at": now, "community_id": community_id},
            "$setOnInsert": {"first_at": now},
        }
        new_reader = False
        for attempt in range(2):
            try:
                result = await self.collection.update_one(key, update, upsert=True)
                new_reader = result.upserted_id is not None
                break
            except DuplicateKeyError:
                if attempt == 1:  # two first clicks at the same moment; the retry updates
                    raise
        inc = {f"{kind}_total": 1}
        if new_reader:
            inc[f"{kind}_users"] = 1
        await self.stats_collection.update_one(
            {"_id": article_id}, {"$inc": inc, "$set": {"updated_at": now}}, upsert=True
        )
        return await self.stats(article_id)

    async def stats(self, article_id: ObjectId) -> dict[str, int]:
        row = await self.stats_collection.find_one({"_id": article_id}) or {}
        return {**empty_stats(), **{k: int(row.get(k, 0)) for k in empty_stats()}}

    async def stats_many(self, ids: list[ObjectId]) -> dict[ObjectId, dict[str, int]]:
        if not ids:
            return {}
        found = {
            row["_id"]: {**empty_stats(), **{k: int(row.get(k, 0)) for k in empty_stats()}}
            async for row in self.stats_collection.find({"_id": {"$in": ids}})
        }
        return {article_id: found.get(article_id, empty_stats()) for article_id in ids}

    async def most_clicked(
        self, since: datetime, kind: ClickKind, limit: int
    ) -> list[dict[str, Any]]:
        """Articles with the most unique readers since ``since``, then the most clicks."""

        pipeline: list[dict[str, Any]] = [
            {"$match": {"kind": kind, "last_at": {"$gte": since}}},
            {"$group": {"_id": "$article_id", "users": {"$sum": 1}, "clicks": {"$sum": "$count"}}},
            {"$sort": {"users": -1, "clicks": -1, "_id": 1}},
            {"$limit": limit},
        ]
        return [row async for row in self.collection.aggregate(pipeline)]


async def with_clicks(database: Any, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach each article's click counts as ``clicks``, with one query for the whole list."""

    stats = await ClickRepository(database).stats_many([doc["_id"] for doc in docs])
    for doc in docs:
        doc["clicks"] = stats.get(doc["_id"])
    return docs
