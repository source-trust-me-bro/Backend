from typing import Any

from bson import ObjectId
from pymongo import ReturnDocument

from app.core.utils import require_object_id, utcnow
from app.repositories.base import BaseRepository

EMPTY_STATS = {"fetched": 0, "created": 0, "updated": 0, "skipped": 0, "failed": 0}


class IngestionJobRepository(BaseRepository):
    collection_name = "ingestion_jobs"

    async def create(self, *, source_id: str, trigger: str, max_items: int) -> dict[str, Any]:
        object_id = require_object_id(source_id, field="source_id")
        doc = {
            "source_id": object_id,
            "status": "queued",
            "trigger": trigger,
            "max_items": max_items,
            "stats": dict(EMPTY_STATS),
            "error": None,
            "started_at": None,
            "finished_at": None,
            "created_at": utcnow(),
        }
        result = await self.collection.insert_one(doc)
        doc["_id"] = result.inserted_id
        return doc

    async def mark_running(self, job_id: ObjectId) -> dict[str, Any] | None:
        return await self.collection.find_one_and_update(
            {"_id": job_id},
            {"$set": {"status": "running", "started_at": utcnow()}},
            return_document=ReturnDocument.AFTER,
        )

    async def mark_finished(
        self,
        job_id: ObjectId,
        *,
        status: str,
        stats: dict[str, int],
        error: str | None = None,
    ) -> dict[str, Any] | None:
        return await self.collection.find_one_and_update(
            {"_id": job_id},
            {
                "$set": {
                    "status": status,
                    "stats": stats,
                    "error": error,
                    "finished_at": utcnow(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )

    async def list(
        self,
        *,
        source_id: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        filters: dict[str, Any] = {}
        if source_id:
            filters["source_id"] = require_object_id(source_id, field="source_id")
        if status:
            filters["status"] = status
        total = await self.collection.count_documents(filters)
        cursor = (
            self.collection.find(filters)
            .sort([("created_at", -1)])
            .skip((page - 1) * page_size)
            .limit(page_size)
        )
        return [doc async for doc in cursor], total
