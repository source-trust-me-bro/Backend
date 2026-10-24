from collections.abc import Sequence
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.exceptions import ConflictError
from app.core.utils import to_object_id, utcnow
from app.repositories.base import BaseRepository


class SourceRepository(BaseRepository):
    collection_name = "sources"

    async def create(
        self, data: dict[str, Any], *, created_by: str | None = None
    ) -> dict[str, Any]:
        now = utcnow()
        doc = {**data, "created_by": created_by, "created_at": now, "updated_at": now}
        try:
            result = await self.collection.insert_one(doc)
        except DuplicateKeyError as exc:
            raise ConflictError("A source with this feed URL already exists.") from exc
        doc["_id"] = result.inserted_id
        return doc

    async def list(
        self,
        *,
        language: str | None = None,
        active: bool | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        filters: dict[str, Any] = {}
        if language:
            filters["language"] = language
        if active is not None:
            filters["active"] = active

        total = await self.collection.count_documents(filters)
        cursor = (
            self.collection.find(filters)
            .sort([("name", 1)])
            .skip((page - 1) * page_size)
            .limit(page_size)
        )
        return [doc async for doc in cursor], total

    async def get_many(self, source_ids: Sequence[str]) -> Sequence[dict[str, Any]]:
        object_ids = [oid for oid in (to_object_id(value) for value in source_ids) if oid]
        if not object_ids:
            return []
        cursor = self.collection.find({"_id": {"$in": object_ids}}).sort([("name", 1)])
        return [doc async for doc in cursor]

    async def update(self, source_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        object_id = to_object_id(source_id)
        if object_id is None:
            return None
        fields = {**fields, "updated_at": utcnow()}
        try:
            return await self.collection.find_one_and_update(
                {"_id": object_id},
                {"$set": fields},
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError as exc:
            raise ConflictError("A source with this feed URL already exists.") from exc
