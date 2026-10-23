import asyncio
import logging
import uuid
from typing import Any

from app.config import Settings, get_settings
from app.core.exceptions import AppError, BadRequestError, NotFoundError, UpstreamError
from app.core.utils import normalize_whitespace, truncate, utcnow
from app.repositories.articles import ArticleRepository
from app.repositories.ingestion_jobs import IngestionJobRepository
from app.repositories.sources import SourceRepository
from app.repositories.topics import TopicRepository
from app.schemas.article import ArticleIngest
from app.services.embedding_service import EmbeddingService
from app.services.extraction_service import ExtractionService, detect_language
from app.services.rss_service import FeedEntry, RSSService

logger = logging.getLogger(__name__)

EMPTY_STATS = {"fetched": 0, "created": 0, "updated": 0, "skipped": 0, "failed": 0}


class IngestionService:
    def __init__(
        self,
        *,
        source_repo: SourceRepository,
        article_repo: ArticleRepository,
        job_repo: IngestionJobRepository,
        topic_repo: TopicRepository,
        rss_service: RSSService,
        extraction_service: ExtractionService,
        embedding_service: EmbeddingService,
        settings: Settings | None = None,
    ) -> None:
        self.source_repo = source_repo
        self.article_repo = article_repo
        self.job_repo = job_repo
        self.topic_repo = topic_repo
        self.rss_service = rss_service
        self.extraction_service = extraction_service
        self.embedding_service = embedding_service
        self.settings = settings or get_settings()

    async def run_source(
        self,
        source_id: str,
        *,
        trigger: str = "manual",
        max_items: int | None = None,
    ) -> dict[str, Any]:
        source = await self.source_repo.get_by_id(source_id)
        if source is None:
            raise NotFoundError(f"Source '{source_id}' was not found.")

        limit = max_items or self.settings.ingestion_max_items
        job = await self.job_repo.create(
            source_id=str(source["_id"]), trigger=trigger, max_items=limit
        )
        job = await self.job_repo.mark_running(job["_id"]) or job
        stats = dict(EMPTY_STATS)

        try:
            entries = await self.rss_service.fetch_entries(
                source["feed_url"], language=source.get("language"), limit=limit
            )
        except AppError as exc:
            await self.job_repo.mark_finished(
                job["_id"], status="failed", stats=stats, error=exc.detail
            )
            raise
        except Exception as exc:
            logger.exception("Feed fetch failed for source %s", source_id)
            await self.job_repo.mark_finished(
                job["_id"], status="failed", stats=stats, error=str(exc)[:500]
            )
            raise UpstreamError(f"Feed ingestion failed: {exc}") from exc

        stats["fetched"] = len(entries)
        for entry in entries:
            try:
                outcome = await self._ingest_feed_entry(source, entry)
            except Exception:
                logger.warning("Failed to ingest entry %s", entry.url, exc_info=True)
                stats["failed"] += 1
            else:
                stats[outcome] += 1

        finished = await self.job_repo.mark_finished(job["_id"], status="completed", stats=stats)
        return finished or job

    async def _ingest_feed_entry(self, source: dict[str, Any], entry: FeedEntry) -> str:
        title = entry.title
        summary = entry.summary
        content: str | None = None
        author = entry.author
        published_at = entry.published_at
        language = entry.language or source.get("language") or self.settings.default_language

        if self.settings.fetch_article_content:
            try:
                extracted = await self.extraction_service.extract(
                    entry.url, fallback_language=language
                )
            except AppError as exc:
                logger.info("Skipping article extraction for %s: %s", entry.url, exc.detail)
            except Exception:
                logger.warning("Article extraction crashed for %s", entry.url, exc_info=True)
            else:
                content = extracted.content
                title = title or extracted.title
                summary = summary or truncate(extracted.content, 400)
                author = author or extracted.author
                published_at = published_at or extracted.published_at
                language = extracted.language or language

        if not title:
            logger.info("Skipping entry without a title: %s", entry.url)
            return "skipped"

        language = (
            detect_language(
                f"{title}. {summary or ''}",
                supported=self.settings.supported_languages,
                fallback=language,
            )
            or self.settings.default_language
        )
        topic_ids = await self.topic_repo.match_keywords(
            f"{title} {summary or ''}", language=language
        )
        embedding = await asyncio.to_thread(
            self.embedding_service.embed, f"{title}\n\n{summary or ''}"
        )

        document = {
            "source_id": source["_id"],
            "source_name": source["name"],
            "url": entry.url,
            "external_id": entry.external_id,
            "title": title,
            "summary": summary,
            "content": content,
            "language": language,
            "author": author,
            "published_at": published_at,
            "fetched_at": utcnow(),
            "topic_ids": topic_ids,
            "embedding": embedding,
        }
        _, created = await self.article_repo.upsert(document)
        return "created" if created else "updated"

    async def ingest_article(self, payload: ArticleIngest) -> dict[str, Any]:
        source = None
        if payload.source_id:
            source = await self.source_repo.get_by_id(payload.source_id)
            if source is None:
                raise NotFoundError(f"Source '{payload.source_id}' was not found.")

        url = payload.url
        title = payload.title
        summary = payload.summary
        content = payload.content
        author = payload.author
        published_at = payload.published_at
        language = payload.language or (source or {}).get("language")

        if url and content is None and self.settings.fetch_article_content:
            extracted = await self.extraction_service.extract(url, fallback_language=language)
            content = extracted.content
            title = title or extracted.title
            summary = summary or truncate(extracted.content, 400)
            author = author or extracted.author
            published_at = published_at or extracted.published_at
            language = language or extracted.language

        if not title:
            raise BadRequestError(
                "Unable to determine an article title. Provide a title or a URL with "
                "extractable metadata."
            )
        if content is None and summary is None:
            raise BadRequestError("Article content could not be extracted.")

        summary = summary or truncate(content, 400)
        language = (
            detect_language(
                f"{title}. {summary or ''}",
                supported=self.settings.supported_languages,
                fallback=language or self.settings.default_language,
            )
            or self.settings.default_language
        )

        if payload.topic_ids:
            matched = await self.topic_repo.get_many(payload.topic_ids)
            if len(matched) != len(set(payload.topic_ids)):
                raise BadRequestError("One or more topic_ids do not exist.")
            topic_ids = [doc["_id"] for doc in matched]
        else:
            topic_ids = await self.topic_repo.match_keywords(
                f"{title} {summary or ''}", language=language
            )

        embedding = await asyncio.to_thread(
            self.embedding_service.embed, f"{title}\n\n{summary or ''}"
        )
        document = {
            "source_id": source["_id"] if source else None,
            "source_name": source["name"] if source else "manual",
            "url": url or f"manual://article/{uuid.uuid4()}",
            "external_id": None,
            "title": normalize_whitespace(title),
            "summary": summary,
            "content": content,
            "language": language,
            "author": author,
            "published_at": published_at,
            "fetched_at": utcnow(),
            "topic_ids": topic_ids,
            "embedding": embedding,
        }
        article, _ = await self.article_repo.upsert(document)
        return article
