from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.utils import validate_public_url
from app.schemas.base import (
    LanguageCode,
    MongoBaseModel,
    ObjectIdList,
    ObjectIdStr,
    OptionalObjectIdStr,
)


class ArticleSummary(MongoBaseModel):
    source_id: OptionalObjectIdStr = None
    source_name: str
    title: str
    summary: str | None = None
    url: str
    language: str
    author: str | None = None
    published_at: datetime | None = None
    topic_ids: ObjectIdList = Field(default_factory=list)
    created_at: datetime
    # Hotlinked from the publisher, shown as "Photo: <source_name>"; never re-hosted.
    image_url: str | None = None
    # Newsroom stories split in two: "What happened" and "What it means for you".
    basic_info: str | None = None
    explanation: str | None = None
    event_id: str | None = None
    community_id: OptionalObjectIdStr = None  # the newsroom community the story was written for
    reasons: list[str] = Field(default_factory=list)  # why a related or feed story is shown
    clicks: dict[str, int] | None = None  # see ClickRepository.stats


class ArticleDetail(ArticleSummary):
    content: str | None = None
    updated_at: datetime


class ArticleIngest(BaseModel):
    """Manually ingest a single article, either by URL or with inline content."""

    source_id: ObjectIdStr | None = None
    url: str | None = Field(default=None, max_length=2048)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    summary: str | None = Field(default=None, max_length=5000)
    content: str | None = None
    language: LanguageCode | None = None
    author: str | None = Field(default=None, max_length=200)
    published_at: datetime | None = None
    topic_ids: ObjectIdList = Field(default_factory=list)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str | None) -> str | None:
        return validate_public_url(value) if value else None

    @model_validator(mode="after")
    def _require_identifiable_article(self) -> "ArticleIngest":
        if not self.url and not (self.title and self.content):
            raise ValueError("Provide either a url or both title and content.")
        return self


class ArticleTranslationCreate(BaseModel):
    language: LanguageCode
    title: str | None = Field(default=None, max_length=500)
    summary: str | None = Field(default=None, max_length=5000)
    content: str | None = None

    @model_validator(mode="after")
    def _require_translated_text(self) -> "ArticleTranslationCreate":
        if not (self.title or self.summary or self.content):
            raise ValueError("At least one translated field must be provided.")
        return self


class ArticleTranslationOut(MongoBaseModel):
    article_id: ObjectIdStr
    language: str
    title: str | None = None
    summary: str | None = None
    content: str | None = None
    created_at: datetime
    updated_at: datetime


class SemanticSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    language: LanguageCode | None = None
    limit: int = Field(default=10, ge=1, le=50)
    community_id: ObjectIdStr | None = None


class SemanticHit(BaseModel):
    article: ArticleSummary
    score: float


class SemanticSearchResponse(BaseModel):
    query: str
    language: str | None = None
    results: list[SemanticHit]
    total: int
