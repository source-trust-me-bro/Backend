import contextlib
import re
import weakref
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.core.exceptions import ConflictError
from app.core.utils import escape_regex, require_object_id, to_object_id, utcnow
from app.database import ARTICLE_COLLECTION_PREFIX, ensure_article_indexes
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


_LANGUAGE_CODE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})?$")
LEGACY_COLLECTION = "articles"  # before the split into one collection per language

# Collections whose indexes this process has already ensured, per database object.
_indexed: "weakref.WeakKeyDictionary[Any, set[str]]" = weakref.WeakKeyDictionary()


def language_code(value: str | None) -> str:
    """The language part of a collection name. Anything that is not a language code is 'und'."""

    code = (value or "").strip().lower()
    return code if _LANGUAGE_CODE.fullmatch(code) else "und"


def collection_name_for(language: str | None) -> str:
    return f"{ARTICLE_COLLECTION_PREFIX}{language_code(language)}"


def _when(value: Any) -> datetime:
    """A sortable timestamp for documents with or without a time zone, or with none."""

    if not isinstance(value, datetime):
        return datetime.min
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def _merge_sorted(
    documents: list[dict[str, Any]], sort: str, *, skip: int, limit: int
) -> list[dict[str, Any]]:
    newest = sort != "oldest"
    ordered = sorted(
        documents,
        key=lambda d: (_when(d.get("published_at")), _when(d.get("created_at"))),
        reverse=newest,
    )
    return ordered[skip : skip + limit]


class ArticleRepository(BaseRepository):
    """Articles, one MongoDB collection per language: ``articles_en``, ``articles_fi``,
    ``articles_se``... Asking for one language reads exactly one collection. Without a language
    the repository reads them all and merges the result."""

    collection_name = LEGACY_COLLECTION  # only the migration reads it

    @property
    def translations(self):
        return self.database["article_translations"]

    # ------------------------------------------------------------ collections

    async def language_collection_names(self) -> list[str]:
        names = await self.database.list_collection_names()
        return sorted(n for n in names if n.startswith(ARTICLE_COLLECTION_PREFIX))

    async def _writable(self, language: str | None):
        """The collection for a language, with its indexes ensured once per process."""

        name = collection_name_for(language)
        done = _indexed.setdefault(self.database, set())
        collection = self.database[name]
        if name not in done:
            await ensure_article_indexes(collection)
            done.add(name)
        return collection

    async def _targets(self, language: str | None) -> list[str]:
        """The collections to read: the one language asked for, or every language."""

        if language:
            return [collection_name_for(language)]
        return await self.language_collection_names()

    async def counts_by_language(self) -> dict[str, int]:
        """How many articles each language collection holds, keyed by language code."""

        counts: dict[str, int] = {}
        for name in await self.language_collection_names():
            code = name.removeprefix(ARTICLE_COLLECTION_PREFIX)
            counts[code] = await self.database[name].count_documents({})
        return counts

    # ----------------------------------------------------------------- writes

    async def upsert(self, data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        doc = _normalize_article_document(data)
        if not doc.get("url"):
            raise ValueError("Article documents require a url.")
        collection = await self._writable(doc.get("language"))
        now = utcnow()
        update = {
            "$set": {**doc, "updated_at": now},
            "$setOnInsert": {"created_at": now},
        }
        result = await collection.update_one({"url": doc["url"]}, update, upsert=True)
        article = await collection.find_one({"url": doc["url"]})
        if article is None:
            raise RuntimeError("Article upsert did not persist a document.")
        return article, result.upserted_id is not None

    async def delete(self, article_id: str) -> bool:
        object_id = to_object_id(article_id)
        if object_id is None:
            return False
        await self.translations.delete_many({"article_id": object_id})
        return await self._delete_one(object_id)

    async def delete_by_id(self, entity_id: str | ObjectId) -> bool:
        object_id = to_object_id(entity_id)
        return False if object_id is None else await self._delete_one(object_id)

    async def _delete_one(self, object_id: ObjectId) -> bool:
        for name in await self.language_collection_names():
            result = await self.database[name].delete_one({"_id": object_id})
            if result.deleted_count:
                return True
        return False

    # ------------------------------------------------------------------ reads

    async def get_by_id(self, entity_id: str | ObjectId) -> dict[str, Any] | None:
        object_id = to_object_id(entity_id)
        if object_id is None:
            return None
        for name in await self.language_collection_names():
            found = await self.database[name].find_one({"_id": object_id})
            if found is not None:
                return found
        return None

    async def exists(self, entity_id: str | ObjectId) -> bool:
        return await self.get_by_id(entity_id) is not None

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
        newsroom_only: bool = False,
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
        if newsroom_only:
            # Stories an editor published from the newsroom (copied by NewsroomSync).
            filters["newsroom_story_id"] = {"$exists": True}
        spec = SORT_SPECS.get(sort, SORT_SPECS["newest"])
        skip = (page - 1) * page_size
        names = await self._targets(language)

        if len(names) == 1:  # one language: the database does the sorting and paging
            collection = self.database[names[0]]
            total = await collection.count_documents(filters)
            cursor = (
                collection.find(filters, SUMMARY_PROJECTION).sort(spec).skip(skip).limit(page_size)
            )
            return [doc async for doc in cursor], total

        total = 0
        pool: list[dict[str, Any]] = []
        for name in names:  # several languages: each gives its best skip+limit, then merge
            collection = self.database[name]
            total += await collection.count_documents(filters)
            cursor = collection.find(filters, SUMMARY_PROJECTION).sort(spec).limit(skip + page_size)
            pool.extend([doc async for doc in cursor])
        return _merge_sorted(pool, sort, skip=skip, limit=page_size), total

    async def existing_ids(self, ids: list[ObjectId]) -> set[ObjectId]:
        if not ids:
            return set()
        found: set[ObjectId] = set()
        for name in await self.language_collection_names():
            cursor = self.database[name].find({"_id": {"$in": ids}}, {"_id": 1})
            found |= {doc["_id"] async for doc in cursor}
        return found

    async def summaries_by_ids(self, ids: list[ObjectId]) -> dict[ObjectId, dict[str, Any]]:
        if not ids:
            return {}
        found: dict[ObjectId, dict[str, Any]] = {}
        for name in await self.language_collection_names():
            cursor = self.database[name].find({"_id": {"$in": ids}}, SUMMARY_PROJECTION)
            found.update({doc["_id"]: doc async for doc in cursor})
        return found

    async def related(self, article: dict[str, Any], *, limit: int = 5) -> list[dict[str, Any]]:
        filters: dict[str, Any] = {"_id": {"$ne": article["_id"]}}
        topic_ids = article.get("topic_ids") or []
        if topic_ids:
            filters["topic_ids"] = {"$in": topic_ids}
        # Related stories are in the same language, so this reads that one collection.
        collection = self.database[collection_name_for(article.get("language"))]
        cursor = (
            collection.find(filters, SUMMARY_PROJECTION).sort(SORT_SPECS["newest"]).limit(limit)
        )
        return [doc async for doc in cursor]

    async def semantic_candidates(
        self, filters: dict[str, Any], *, limit: int
    ) -> list[dict[str, Any]]:
        query = {**filters, "embedding": {"$exists": True, "$ne": []}}
        names = await self._targets(filters.get("language"))
        pool: list[dict[str, Any]] = []
        for name in names:
            cursor = self.database[name].find(query).sort(SORT_SPECS["newest"]).limit(limit)
            pool.extend([doc async for doc in cursor])
        return pool if len(names) == 1 else _merge_sorted(pool, "newest", skip=0, limit=limit)

    # ----------------------------------------------------------- translations

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


async def migrate_legacy_articles(database: Any) -> dict[str, int]:
    """Move articles from the old single ``articles`` collection into per-language collections.

    Ids are kept, so saved items and history keep pointing at the same articles. Safe to run
    again: a document is removed from the old collection only once it exists in its new one,
    and the old collection is dropped when it is empty."""

    if LEGACY_COLLECTION not in await database.list_collection_names():
        return {}
    repo = ArticleRepository(database)
    legacy = database[LEGACY_COLLECTION]
    moved: dict[str, int] = {}
    by_language: dict[str, list[dict[str, Any]]] = {}
    async for doc in legacy.find({}):
        by_language.setdefault(language_code(doc.get("language")), []).append(doc)
    for code, docs in by_language.items():
        target = await repo._writable(code)
        for doc in docs:
            # The same _id or link may already be there from an earlier run.
            twin = await target.find_one({"$or": [{"_id": doc["_id"]}, {"url": doc.get("url")}]})
            if twin is None:
                await target.insert_one(doc)
            elif twin["_id"] != doc["_id"]:
                # Same link, different id: keep the article that is already there and point
                # anything saved on the old id at it (skipped where the reader has both).
                with contextlib.suppress(DuplicateKeyError):
                    await database["user_articles"].update_many(
                        {"article_id": doc["_id"]}, {"$set": {"article_id": twin["_id"]}}
                    )
            await legacy.delete_one({"_id": doc["_id"]})
            moved[code] = moved.get(code, 0) + 1
    if await legacy.count_documents({}) == 0:
        await database.drop_collection(LEGACY_COLLECTION)
    return moved


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
