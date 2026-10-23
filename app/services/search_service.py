from typing import Any

from app.config import Settings, get_settings
from app.core.utils import cosine_similarity
from app.repositories.articles import ArticleRepository, build_article_filters, tokenize_query
from app.services.embedding_service import EmbeddingService


class SearchService:
    def __init__(
        self,
        article_repo: ArticleRepository,
        embedding_service: EmbeddingService,
        settings: Settings | None = None,
    ) -> None:
        self.article_repo = article_repo
        self.embedding_service = embedding_service
        self.settings = settings or get_settings()

    async def text_search(
        self,
        query: str,
        *,
        language: str | None = None,
        topic_ids: list[str] | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        tokens = tokenize_query(query, max_tokens=self.settings.search_max_tokens)
        return await self.article_repo.list_articles(
            language=language,
            topic_ids=topic_ids,
            tokens=tokens,
            page=page,
            page_size=page_size,
        )

    async def semantic_search(
        self,
        query: str,
        *,
        language: str | None = None,
        topic_ids: list[str] | None = None,
        limit: int = 10,
    ) -> list[tuple[dict[str, Any], float]]:
        vector = self.embedding_service.embed(query)
        if not vector:
            return []

        filters = build_article_filters(language=language, topic_ids=topic_ids)
        candidates = await self.article_repo.semantic_candidates(
            filters, limit=self.settings.semantic_candidate_limit
        )
        scored = [
            (article, cosine_similarity(vector, article.get("embedding") or []))
            for article in candidates
        ]
        scored = [item for item in scored if item[1] > 0.0]
        scored.sort(key=lambda item: (-item[1], str(item[0]["_id"])))
        return scored[:limit]
