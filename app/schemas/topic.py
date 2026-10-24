from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.base import LanguageCode, MongoBaseModel, ObjectIdList, OptionalObjectIdStr


class TopicCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=80)
    parent_id: OptionalObjectIdStr = None
    language: LanguageCode
    description: str | None = Field(default=None, max_length=2000)
    keywords: list[str] = Field(default_factory=list)

    @field_validator("keywords")
    @classmethod
    def _clean_keywords(cls, value: list[str]) -> list[str]:
        cleaned = {keyword.strip().lower() for keyword in value if keyword.strip()}
        return sorted(cleaned)


class TopicUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=80)
    parent_id: OptionalObjectIdStr = None
    language: LanguageCode | None = None
    description: str | None = Field(default=None, max_length=2000)
    keywords: list[str] | None = None

    @field_validator("keywords")
    @classmethod
    def _clean_keywords(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = {keyword.strip().lower() for keyword in value if keyword.strip()}
        return sorted(cleaned)


class TopicBranchCreate(BaseModel):
    branch_id: str


class TopicOut(MongoBaseModel):
    name: str
    slug: str
    parent_id: OptionalObjectIdStr = None
    language: str
    description: str | None = None
    keywords: list[str] = Field(default_factory=list)
    branches: ObjectIdList = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class TopicNode(TopicOut):
    children: list["TopicNode"] = Field(default_factory=list)


TopicNode.model_rebuild()
