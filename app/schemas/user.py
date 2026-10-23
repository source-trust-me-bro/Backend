from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.base import LanguageCode, MongoBaseModel, ObjectIdStr, OptionalObjectIdStr

RoleName = Literal["reader", "editor", "admin"]


class ClerkPrincipal(BaseModel):
    sub: str
    email: str | None = None
    display_name: str | None = None
    username: str | None = None
    image_url: str | None = None


class UserProfile(MongoBaseModel):
    clerk_user_id: str
    email: str | None = None
    display_name: str | None = None
    roles: list[RoleName] = Field(default_factory=lambda: ["reader"])
    preferred_language: str = "en"
    community_id: OptionalObjectIdStr = None
    topic_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime | None = None


class UserUpdate(BaseModel):
    """PATCH payload. Roles may only be changed by an admin."""

    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    preferred_language: LanguageCode | None = None
    community_id: ObjectIdStr | None = None
    topic_ids: list[ObjectIdStr] | None = None
    roles: list[RoleName] | None = None


class UserRolesUpdate(BaseModel):
    roles: list[RoleName] = Field(min_length=1)
