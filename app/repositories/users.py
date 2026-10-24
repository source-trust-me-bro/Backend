from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.utils import utcnow
from app.repositories.base import BaseRepository
from app.schemas.user import ClerkPrincipal


class UserRepository(BaseRepository):
    collection_name = "users"

    async def get_by_clerk_id(self, clerk_user_id: str) -> dict[str, Any] | None:
        return await self.collection.find_one({"clerk_user_id": clerk_user_id})

    async def search_by_name(self, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        """Accounts whose display name contains ``query`` (case-insensitive).

        Used server-side so editors and community admins can find the person they want to
        appoint as a community moderator or admin.
        """

        import re

        pattern = re.escape(query.strip())
        cursor = (
            self.collection.find({"display_name": {"$regex": pattern, "$options": "i"}})
            .sort("display_name", 1)
            .limit(limit)
        )
        return [doc async for doc in cursor]

    async def get_by_ids(self, user_ids: list[str]) -> list[dict[str, Any]]:
        object_ids = [ObjectId(value) for value in user_ids if ObjectId.is_valid(value)]
        if not object_ids:
            return []
        cursor = self.collection.find({"_id": {"$in": object_ids}})
        return [doc async for doc in cursor]

    async def sync_from_principal(
        self,
        principal: ClerkPrincipal,
        *,
        default_language: str,
    ) -> dict[str, Any]:
        """Provision the local profile on first sight and refresh claim-derived fields."""

        now = utcnow()
        # Never overwrite stored values with None when the token lacks a claim.
        claim_fields = {
            key: value
            for key, value in (
                ("email", principal.email),
                ("display_name", principal.display_name),
                ("image_url", principal.image_url),
            )
            if value is not None
        }
        update = {
            "$set": {
                **claim_fields,
                "last_seen_at": now,
                "updated_at": now,
            },
            "$setOnInsert": {
                "clerk_user_id": principal.sub,
                "roles": ["reader"],
                "preferred_language": default_language,
                "community_id": None,
                "topic_ids": [],
                "followed_source_ids": [],
                "created_at": now,
            },
        }
        for attempt in range(2):
            try:
                doc = await self.collection.find_one_and_update(
                    {"clerk_user_id": principal.sub},
                    update,
                    upsert=True,
                    return_document=ReturnDocument.AFTER,
                )
                if doc is not None:
                    return doc
            except DuplicateKeyError:
                if attempt == 1:
                    raise
        existing = await self.get_by_clerk_id(principal.sub)
        if existing is None:
            raise RuntimeError("Failed to provision user profile.")
        return existing

    async def update_fields(
        self, clerk_user_id: str, fields: dict[str, Any]
    ) -> dict[str, Any] | None:
        if not fields:
            return await self.get_by_clerk_id(clerk_user_id)
        fields = {**fields, "updated_at": utcnow()}
        return await self.collection.find_one_and_update(
            {"clerk_user_id": clerk_user_id},
            {"$set": fields},
            return_document=ReturnDocument.AFTER,
        )

    async def set_followed_source(
        self, clerk_user_id: str, source_id: str, value: bool
    ) -> dict[str, Any] | None:
        operator = "$addToSet" if value else "$pull"
        return await self.collection.find_one_and_update(
            {"clerk_user_id": clerk_user_id},
            {operator: {"followed_source_ids": source_id}, "$set": {"updated_at": utcnow()}},
            return_document=ReturnDocument.AFTER,
        )

    async def set_roles(self, clerk_user_id: str, roles: list[str]) -> dict[str, Any] | None:
        return await self.update_fields(clerk_user_id, {"roles": roles})

    async def touch_last_seen(self, clerk_user_id: str) -> None:
        await self.collection.update_one(
            {"clerk_user_id": clerk_user_id},
            {"$set": {"last_seen_at": datetime.now(UTC)}},
        )
