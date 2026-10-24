import logging
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class DatabaseState:
    """Holds the process-wide Motor client and database handle."""

    client: AsyncIOMotorClient | None = None
    database: AsyncIOMotorDatabase | None = None


_state = DatabaseState()


def build_client(settings: Settings) -> AsyncIOMotorClient:
    return AsyncIOMotorClient(
        settings.mongodb_uri,
        tz_aware=True,
        serverSelectionTimeoutMS=settings.mongodb_timeout_ms,
        connectTimeoutMS=settings.mongodb_timeout_ms,
        uuidRepresentation="standard",
    )


async def connect_to_mongo(settings: Settings | None = None) -> AsyncIOMotorDatabase:
    """Create the Motor client. Connections are opened lazily by the driver."""

    settings = settings or get_settings()
    if _state.client is None:
        _state.client = build_client(settings)
        _state.database = _state.client[settings.mongodb_database]
    assert _state.database is not None
    return _state.database


async def close_mongo_connection() -> None:
    if _state.client is not None:
        _state.client.close()
    _state.client = None
    _state.database = None


def get_database() -> AsyncIOMotorDatabase:
    """FastAPI dependency returning the configured database instance."""

    if _state.database is None:
        raise RuntimeError("MongoDB is not initialized. Did the application lifespan run?")
    return _state.database


async def ping_database(database: AsyncIOMotorDatabase) -> None:
    await database.command("ping")


def _unique(*fields: str, **kwargs: Any) -> dict[str, Any]:
    return {"keys": list(fields), "unique": True, **kwargs}


INDEX_PLAN: dict[str, list[dict[str, Any]]] = {
    "users": [
        _unique("clerk_user_id"),
        {"keys": ["email"]},
        {"keys": ["community_id"]},
    ],
    "sources": [
        _unique("feed_url"),
        {"keys": ["language", "active"]},
    ],
    "articles": [
        _unique("url"),
        {"keys": ["source_id", "published_at"]},
        {"keys": ["language", "published_at"]},
        {"keys": ["topic_ids", "published_at"]},
        {"keys": ["published_at"]},
    ],
    "article_translations": [
        _unique("article_id", "language"),
    ],
    "topics": [
        _unique("slug"),
        {"keys": ["parent_id"]},
        {"keys": ["language"]},
        {"keys": ["branches"]},
    ],
    "communities": [
        _unique("slug"),
        {"keys": ["language"]},
        {"keys": ["editor_user_ids"]},
        {"keys": ["topic_ids"]},
    ],
    "ingestion_jobs": [
        {"keys": ["source_id", "created_at"]},
        {"keys": ["status"]},
        {"keys": ["created_at"]},
    ],
}


async def ensure_indexes(database: AsyncIOMotorDatabase) -> None:
    for collection_name, indexes in INDEX_PLAN.items():
        collection = database[collection_name]
        for index in indexes:
            keys = index["keys"]
            options = {k: v for k, v in index.items() if k != "keys"}
            await collection.create_index([(field, 1) for field in keys], **options)


async def backfill_topic_branches(database: AsyncIOMotorDatabase) -> None:
    """Migration 001: give every legacy topic an empty `branches` list.

    Topic-to-topic relationships are stored symmetrically on both documents.
    New topics always get the field up front; this backfill keeps older records
    consistent and is idempotent.
    """

    await database["topics"].update_many(
        {"branches": {"$exists": False}},
        {"$set": {"branches": []}},
    )


async def run_migrations(database: AsyncIOMotorDatabase) -> None:
    """Apply idempotent, startup-safe data migrations in order."""

    await backfill_topic_branches(database)
