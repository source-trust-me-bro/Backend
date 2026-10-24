import re
from datetime import datetime
from typing import Any

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.core.exceptions import ConflictError
from app.core.utils import escape_regex, require_object_id, to_object_id, utcnow
from app.repositories.base import BaseRepository

SUMMARY_PROJECTION: dict[str, Any] = {"content": 0, "embedding": 0}
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

SORT_SPECS: dict[str, list[tuple[str, int]]] = {
    "newest": [("published_at", -1), ("created_at", -1)],
    "oldest": [("published_at", 1), ("created_at", 1)],
}


def tokenize_query(query: str, *, max_tokens: int = 8) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(query.casefold()):
        token = match.group(0)
        if token not in tokens:
            tokens.append(token)
        if len(tokens) >= max_tokens:
            break
    return tokens


class ArticleRepository(BaseRepository):
    collection_name = "articles"

    @property
    def translations(self):
        return self.database["article_translations"]

    async def upsert(self, data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        doc = _normalize_article_document(data)
        if not doc.get("url"):
            raise ValueError("Article documents require a url.")
        now = utcnow()
        update = {
            "$set": {**doc, "updated_at": now},
            "$setOnInsert": {"created_at": now},
        }
        result = await self.collection.update_one({"url": doc["url"]}, update, upsert=True)
        article = await self.collection.find_one({"url": doc["url"]})
        if article is None:
            raise RuntimeError("Article upsert did not persist a document.")
        return article, result.upserted_id is not None

    async def list_articles(
        self,
        *,
        language: str | None = None,
        source_id: str | None = None,
        source_ids: list[str] | None = None,
        topic_ids: list[str] | None = None,
        tokens: list[str] | None = None,
        published_after: datetime | None = None,
        published_before: datetime | None = None,
        exclude_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
        sort: str = "newest",
    ) -> tuple[list[dict[str, Any]], int]:
        filters = build_article_filters(
            language=language,
            source_id=source_id,
            source_ids=source_ids,
            topic_ids=topic_ids,
            tokens=tokens,
            published_after=published_after,
            published_before=published_before,
            exclude_id=exclude_id,
        )
        total = await self.collection.count_documents(filters)
        cursor = (
            self.collection.find(filters, SUMMARY_PROJECTION)
            .sort(SORT_SPECS.get(sort, SORT_SPECS["newest"]))
            .skip((page - 1) * page_size)
            .limit(page_size)
        )
        return [doc async for doc in cursor], total

    async def existing_ids(self, ids: list[ObjectId]) -> set[ObjectId]:
        if not ids:
            return set()
        cursor = self.collection.find({"_id": {"$in": ids}}, {"_id": 1})
        return {doc["_id"] async for doc in cursor}

    async def summaries_by_ids(self, ids: list[ObjectId]) -> dict[ObjectId, dict[str, Any]]:
        if not ids:
            return {}
        cursor = self.collection.find({"_id": {"$in": ids}}, SUMMARY_PROJECTION)
        return {doc["_id"]: doc async for doc in cursor}

    async def related(self, article: dict[str, Any], *, limit: int = 5) -> list[dict[str, Any]]:
        filters: dict[str, Any] = {
            "_id": {"$ne": article["_id"]},
            "language": article.get("language"),
        }
        topic_ids = article.get("topic_ids") or []
        if topic_ids:
            filters["topic_ids"] = {"$in": topic_ids}
        cursor = (
            self.collection.find(filters, SUMMARY_PROJECTION)
            .sort(SORT_SPECS["newest"])
            .limit(limit)
        )
        return [doc async for doc in cursor]

    async def semantic_candidates(
        self, filters: dict[str, Any], *, limit: int
    ) -> list[dict[str, Any]]:
        query = {**filters, "embedding": {"$exists": True, "$ne": []}}
        cursor = self.collection.find(query).sort(SORT_SPECS["newest"]).limit(limit)
        return [doc async for doc in cursor]

    async def add_translation(
        self, article_id: str, data: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        object_id = require_object_id(article_id)
        now = utcnow()
        text_fields = {key: data.get(key) for key in ("title", "summary", "content")}
        update = {
            "$set": {**text_fields, "updated_at": now},
            "$setOnInsert": {
                "article_id": object_id,
                "language": data["language"],
                "created_at": now,
            },
        }
        try:
            result = await self.translations.update_one(
                {"article_id": object_id, "language": data["language"]},
                update,
                upsert=True,
            )
        except DuplicateKeyError as exc:
            raise ConflictError("A translation for this language already exists.") from exc
        doc = await self.translations.find_one(
            {"article_id": object_id, "language": data["language"]}
        )
        if doc is None:
            raise RuntimeError("Translation upsert did not persist a document.")
        return doc, result.upserted_id is not None

    async def get_translations(
        self, article_id: str, *, language: str | None = None
    ) -> list[dict[str, Any]]:
        object_id = require_object_id(article_id)
        filters: dict[str, Any] = {"article_id": object_id}
        if language:
            filters["language"] = language
        cursor = self.translations.find(filters).sort([("language", 1)])
        return [doc async for doc in cursor]

    async def get_translation(self, article_id: str, language: str) -> dict[str, Any] | None:
        object_id = require_object_id(article_id)
        return await self.translations.find_one({"article_id": object_id, "language": language})

    async def delete(self, article_id: str) -> bool:
        object_id = to_object_id(article_id)
        if object_id is None:
            return False
        await self.translations.delete_many({"article_id": object_id})
        result = await self.collection.delete_one({"_id": object_id})
        return result.deleted_count > 0


def build_article_filters(
    *,
    language: str | None = None,
    source_id: str | None = None,
    source_ids: list[str] | None = None,
    topic_ids: list[str] | None = None,
    tokens: list[str] | None = None,
    published_after: datetime | None = None,
    published_before: datetime | None = None,
    exclude_id: str | None = None,
) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if language:
        filters["language"] = language
    if source_ids is not None:
        allowed = [oid for oid in (to_object_id(value) for value in source_ids) if oid is not None]
        if source_id:
            single = to_object_id(source_id)
            allowed = [oid for oid in allowed if oid == single]
        filters["source_id"] = {"$in": allowed}
    elif source_id:
        object_id = to_object_id(source_id)
        filters["source_id"] = object_id if object_id is not None else {"$in": []}
    if topic_ids is not None:
        object_ids = [
            oid for oid in (to_object_id(value) for value in topic_ids) if oid is not None
        ]
        filters["topic_ids"] = {"$in": object_ids}
    if published_after or published_before:
        window: dict[str, Any] = {}
        if published_after:
            window["$gte"] = published_after
        if published_before:
            window["$lte"] = published_before
        filters["published_at"] = window
    if exclude_id:
        object_id = to_object_id(exclude_id)
        if object_id is not None:
            filters["_id"] = {"$ne": object_id}
    if tokens:
        filters["$and"] = [
            {
                "$or": [
                    {"title": {"$regex": escape_regex(token), "$options": "i"}},
                    {"summary": {"$regex": escape_regex(token), "$options": "i"}},
                    {"content": {"$regex": escape_regex(token), "$options": "i"}},
                ]
            }
            for token in tokens
        ]
    return filters


def _normalize_article_document(data: dict[str, Any]) -> dict[str, Any]:
    doc = {
        key: value for key, value in data.items() if key not in {"_id", "created_at", "updated_at"}
    }
    source_id = doc.get("source_id")
    if source_id is not None:
        doc["source_id"] = require_object_id(source_id, field="source_id")
    if "topic_ids" in doc:
        doc["topic_ids"] = [
            oid
            for oid in (to_object_id(value) for value in doc["topic_ids"] or [])
            if oid is not None
        ]
    return doc
