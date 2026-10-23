from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.base import (
    LanguageCode,
    MongoBaseModel,
    ObjectIdList,
    OptionalObjectIdStr,
)


class CommunityCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    slug: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    language: LanguageCode
    topic_ids: ObjectIdList = Field(default_factory=list)
    editor_user_ids: ObjectIdList = Field(default_factory=list)

    @field_validator("topic_ids", "editor_user_ids")
    @classmethod
    def _dedupe_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class CommunityUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    slug: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    language: LanguageCode | None = None
    topic_ids: ObjectIdList | None = None
    editor_user_ids: ObjectIdList | None = None

    @field_validator("topic_ids", "editor_user_ids")
    @classmethod
    def _dedupe_ids(cls, value: list[str] | None) -> list[str] | None:
        return list(dict.fromkeys(value)) if value is not None else None


class CommunityOut(MongoBaseModel):
    name: str
    slug: str
    description: str | None = None
    language: str
    topic_ids: ObjectIdList = Field(default_factory=list)
    editor_user_ids: ObjectIdList = Field(default_factory=list)
    created_by: OptionalObjectIdStr = None
    created_at: datetime
    updated_at: datetime
