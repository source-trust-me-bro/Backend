from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.utils import require_object_id, slugify, to_object_id, utcnow
from app.repositories.base import BaseRepository


class TopicRepository(BaseRepository):
    collection_name = "topics"

    async def create(self, data: dict[str, Any]) -> dict[str, Any]:
        parent_id = await self._resolve_parent(data.get("parent_id"))
        slug = slugify(data.get("slug") or data["name"], fallback_prefix="topic")
        now = utcnow()
        doc = {
            **data,
            "slug": slug,
            "parent_id": parent_id,
            "description": data.get("description"),
            "keywords": data.get("keywords", []),
            "created_at": now,
            "updated_at": now,
        }
        try:
            result = await self.collection.insert_one(doc)
        except DuplicateKeyError as exc:
            raise ConflictError(f"A topic with slug '{slug}' already exists.") from exc
        doc["_id"] = result.inserted_id
        return doc

    async def update(self, topic_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        object_id = require_object_id(topic_id)
        if "parent_id" in fields:
            parent_id = fields["parent_id"]
            if parent_id is not None:
                resolved = require_object_id(parent_id, field="parent_id")
                if resolved == object_id:
                    raise BadRequestError("A topic cannot be its own parent.")
                if not await self.exists(resolved):
                    raise NotFoundError(f"Parent topic '{parent_id}' was not found.")
                fields["parent_id"] = resolved
        if fields.get("slug"):
            fields["slug"] = slugify(fields["slug"], fallback_prefix="topic")
        fields = {**fields, "updated_at": utcnow()}
        try:
            return await self.collection.find_one_and_update(
                {"_id": object_id},
                {"$set": fields},
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError as exc:
            raise ConflictError("A topic with this slug already exists.") from exc

    async def list_all(self, *, language: str | None = None) -> list[dict[str, Any]]:
        filters: dict[str, Any] = {}
        if language:
            filters["language"] = language
        cursor = self.collection.find(filters).sort([("name", 1)])
        return [doc async for doc in cursor]

    async def get_many(self, topic_ids: list[str]) -> list[dict[str, Any]]:
        object_ids = [
            oid for oid in (to_object_id(value) for value in topic_ids) if oid is not None
        ]
        if not object_ids:
            return []
        cursor = self.collection.find({"_id": {"$in": object_ids}})
        return [doc async for doc in cursor]

    async def match_keywords(self, text: str, *, language: str) -> list[Any]:
        """Return ids of topics whose keywords appear in the supplied text."""

        haystack = text.casefold()
        cursor = self.collection.find({"language": language, "keywords": {"$ne": []}})
        matched: list[Any] = []
        async for topic in cursor:
            if any(keyword.casefold() in haystack for keyword in topic.get("keywords", [])):
                matched.append(topic["_id"])
        return matched

    @staticmethod
    def build_tree(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        nodes: dict[str, dict[str, Any]] = {}
        for doc in documents:
            node = {**doc, "children": []}
            nodes[str(doc["_id"])] = node

        roots: list[dict[str, Any]] = []
        for node in nodes.values():
            parent_id = node.get("parent_id")
            parent = nodes.get(str(parent_id)) if parent_id is not None else None
            if parent is None:
                roots.append(node)
            else:
                parent["children"].append(node)
        return roots

    async def _resolve_parent(self, parent_id: Any) -> Any:
        if parent_id is None:
            return None
        resolved = require_object_id(parent_id, field="parent_id")
        if not await self.exists(resolved):
            raise NotFoundError(f"Parent topic '{parent_id}' was not found.")
        return resolved
