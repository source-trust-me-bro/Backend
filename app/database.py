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
    "article_translations": [
        _unique("article_id", "language"),
    ],
    "topics": [
        _unique("slug"),
        {"keys": ["parent_id"]},
        {"keys": ["language"]},
    ],
    "communities": [
        _unique("slug"),
        {"keys": ["language"]},
        {"keys": ["editor_user_ids"]},
        {"keys": ["topic_ids"]},
    ],
    "user_articles": [
        _unique("clerk_user_id", "article_id"),
        {"keys": ["clerk_user_id", "saved", "saved_at"]},
        {"keys": ["clerk_user_id", "downloaded", "downloaded_at"]},
        {"keys": ["clerk_user_id", "last_read_at"]},
        {"keys": ["last_read_at"]},
    ],
    "feedback": [
        {"keys": ["created_at"]},
    ],
    "ingestion_jobs": [
        {"keys": ["source_id", "created_at"]},
        {"keys": ["status"]},
        {"keys": ["created_at"]},
    ],
}


# Articles live in one collection per language (articles_en, articles_fi, ...), each with the
# same indexes. They are created on first use; see ArticleRepository.
ARTICLE_COLLECTION_PREFIX = "articles_"
ARTICLE_INDEXES: list[dict[str, Any]] = [
    _unique("url"),
    {"keys": ["source_id", "published_at"]},
    {"keys": ["topic_ids", "published_at"]},
    {"keys": ["published_at"]},
]


async def _create_indexes(collection: Any, indexes: list[dict[str, Any]]) -> None:
    for index in indexes:
        keys = index["keys"]
        options = {k: v for k, v in index.items() if k != "keys"}
        await collection.create_index([(field, 1) for field in keys], **options)


async def ensure_article_indexes(collection: Any) -> None:
    await _create_indexes(collection, ARTICLE_INDEXES)


async def ensure_indexes(database: AsyncIOMotorDatabase) -> None:
    for collection_name, indexes in INDEX_PLAN.items():
        await _create_indexes(database[collection_name], indexes)
    for name in await database.list_collection_names():
        if name.startswith(ARTICLE_COLLECTION_PREFIX):
            await ensure_article_indexes(database[name])
