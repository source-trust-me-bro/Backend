from typing import Any

from app.core.utils import utcnow
from app.repositories.base import BaseRepository


class FeedbackRepository(BaseRepository):
    collection_name = "feedback"

    async def create(self, data: dict[str, Any]) -> dict[str, Any]:
        doc = {**data, "created_at": utcnow()}
        result = await self.collection.insert_one(doc)
        doc["_id"] = result.inserted_id
        return doc

    async def list(self, *, page: int = 1, page_size: int = 20) -> tuple[list[dict[str, Any]], int]:
        total = await self.collection.count_documents({})
        cursor = (
            self.collection.find({})
            .sort([("created_at", -1), ("_id", -1)])
            .skip((page - 1) * page_size)
            .limit(page_size)
        )
        return [doc async for doc in cursor], total
