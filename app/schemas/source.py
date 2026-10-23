from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.utils import validate_public_url
from app.schemas.base import LanguageCode, MongoBaseModel


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    feed_url: str = Field(min_length=1, max_length=2048)
    homepage_url: str | None = Field(default=None, max_length=2048)
    language: LanguageCode
    country: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=2000)
    active: bool = True

    @field_validator("feed_url")
    @classmethod
    def _validate_feed_url(cls, value: str) -> str:
        return validate_public_url(value)

    @field_validator("homepage_url")
    @classmethod
    def _validate_homepage_url(cls, value: str | None) -> str | None:
        return validate_public_url(value) if value else None


class SourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    feed_url: str | None = Field(default=None, min_length=1, max_length=2048)
    homepage_url: str | None = Field(default=None, max_length=2048)
    language: LanguageCode | None = None
    country: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=2000)
    active: bool | None = None

    @field_validator("feed_url", "homepage_url")
    @classmethod
    def _validate_urls(cls, value: str | None) -> str | None:
        return validate_public_url(value) if value else None


class SourceOut(MongoBaseModel):
    name: str
    feed_url: str
    homepage_url: str | None = None
    language: str
    country: str | None = None
    description: str | None = None
    active: bool = True
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
