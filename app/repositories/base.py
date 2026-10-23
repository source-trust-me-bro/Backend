from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.utils import to_object_id


class BaseRepository:
    collection_name: str = ""

    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        self.database = database

    @property
    def collection(self):
        return self.database[self.collection_name]

    async def get_by_id(self, entity_id: str | ObjectId) -> dict[str, Any] | None:
        object_id = to_object_id(entity_id)
        if object_id is None:
            return None
        return await self.collection.find_one({"_id": object_id})

    async def exists(self, entity_id: str | ObjectId) -> bool:
        object_id = to_object_id(entity_id)
        if object_id is None:
            return False
        return await self.collection.count_documents({"_id": object_id}, limit=1) > 0

    async def delete_by_id(self, entity_id: str | ObjectId) -> bool:
        object_id = to_object_id(entity_id)
        if object_id is None:
            return False
        result = await self.collection.delete_one({"_id": object_id})
        return result.deleted_count > 0
