from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.base import MongoBaseModel


class FeedbackCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=4000)
    subject: str | None = Field(default=None, max_length=200)


class FeedbackCreated(MongoBaseModel):
    created_at: datetime


class FeedbackOut(MongoBaseModel):
    email: str | None = None
    display_name: str | None = None
    subject: str | None = None
    message: str
    created_at: datetime
