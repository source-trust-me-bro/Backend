from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.exceptions import ConflictError
from app.core.utils import require_object_id, slugify, utcnow
from app.repositories.base import BaseRepository


class CommunityRepository(BaseRepository):
    collection_name = "communities"

    async def create(
        self, data: dict[str, Any], *, created_by: str | None = None
    ) -> dict[str, Any]:
        slug = slugify(data.get("slug") or data["name"], fallback_prefix="community")
        now = utcnow()
        doc = {
            **data,
            "slug": slug,
            "description": data.get("description"),
            "topic_ids": data.get("topic_ids", []),
            "editor_user_ids": data.get("editor_user_ids", []),
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }
        try:
            result = await self.collection.insert_one(doc)
        except DuplicateKeyError as exc:
            raise ConflictError(f"A community with slug '{slug}' already exists.") from exc
        doc["_id"] = result.inserted_id
        return doc

    async def list(
        self,
        *,
        language: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        filters: dict[str, Any] = {}
        if language:
            filters["language"] = language
        total = await self.collection.count_documents(filters)
        cursor = (
            self.collection.find(filters)
            .sort([("name", 1)])
            .skip((page - 1) * page_size)
            .limit(page_size)
        )
        return [doc async for doc in cursor], total

    async def update(self, community_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        object_id = require_object_id(community_id)
        if fields.get("slug"):
            fields["slug"] = slugify(fields["slug"], fallback_prefix="community")
        fields = {**fields, "updated_at": utcnow()}
        try:
            return await self.collection.find_one_and_update(
                {"_id": object_id},
                {"$set": fields},
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError as exc:
            raise ConflictError("A community with this slug already exists.") from exc
