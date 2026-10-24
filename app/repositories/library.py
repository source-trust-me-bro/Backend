from datetime import datetime
from typing import Any, Literal

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.core.utils import utcnow
from app.repositories.base import BaseRepository

Flag = Literal["saved", "downloaded"]
Kind = Literal["saved", "downloaded", "history"]

# kind -> (filter on the user's rows, field to order by, newest first)
_KINDS: dict[str, tuple[dict[str, Any], str]] = {
    "saved": ({"saved": True}, "saved_at"),
    "downloaded": ({"downloaded": True}, "downloaded_at"),
    "history": ({"last_read_at": {"$exists": True}}, "last_read_at"),
}


class LibraryRepository(BaseRepository):
    """Per-user article state: saved, downloaded, and reading history."""

    collection_name = "user_articles"

    async def _upsert(self, user_id: str, article_id: ObjectId, update: dict[str, Any]) -> None:
        key = {"clerk_user_id": user_id, "article_id": article_id}
        for attempt in range(2):
            try:
                await self.collection.update_one(key, update, upsert=True)
                return
            except DuplicateKeyError:
                # Two concurrent first writes for the same pair; the retry updates instead.
                if attempt == 1:
                    raise

    async def article_ids(self, user_id: str, kind: Kind) -> list[ObjectId]:
        """Article ids for a list, ordered newest first."""

        base_filter, order_field = _KINDS[kind]
        cursor = self.collection.find(
            {"clerk_user_id": user_id, **base_filter}, {"article_id": 1}
        ).sort([(order_field, -1), ("_id", -1)])
        return [doc["article_id"] async for doc in cursor]

    async def set_flag(self, user_id: str, article_id: ObjectId, flag: Flag, value: bool) -> None:
        now = utcnow()
        other = "downloaded" if flag == "saved" else "saved"
        await self._upsert(
            user_id,
            article_id,
            {
                "$set": {flag: value, f"{flag}_at": now if value else None, "updated_at": now},
                "$setOnInsert": {other: False, f"{other}_at": None, "created_at": now},
            },
        )

    async def mark_read(self, user_id: str, article_id: ObjectId) -> None:
        now = utcnow()
        await self._upsert(
            user_id,
            article_id,
            {
                "$set": {"last_read_at": now, "updated_at": now},
                "$inc": {"read_count": 1},
                "$setOnInsert": {
                    "saved": False,
                    "saved_at": None,
                    "downloaded": False,
                    "downloaded_at": None,
                    "created_at": now,
                },
            },
        )

    async def clear_history(self, user_id: str) -> None:
        await self.collection.update_many(
            {"clerk_user_id": user_id, "last_read_at": {"$exists": True}},
            {
                "$unset": {"last_read_at": "", "read_count": ""},
                "$set": {"updated_at": utcnow()},
            },
        )

    async def clear_downloaded(self, user_id: str) -> None:
        await self.collection.update_many(
            {"clerk_user_id": user_id, "downloaded": True},
            {"$set": {"downloaded": False, "downloaded_at": None, "updated_at": utcnow()}},
        )

    async def trending_article_ids(self, since: datetime, limit: int) -> list[ObjectId]:
        """Most-read articles since `since`: total reads first, then distinct readers."""

        pipeline: list[dict[str, Any]] = [
            {"$match": {"last_read_at": {"$gte": since}}},
            {
                "$group": {
                    "_id": "$article_id",
                    "reads": {"$sum": "$read_count"},
                    "readers": {"$sum": 1},
                }
            },
            {"$sort": {"reads": -1, "readers": -1, "_id": 1}},
            {"$limit": limit},
        ]
        return [doc["_id"] async for doc in self.collection.aggregate(pipeline)]
