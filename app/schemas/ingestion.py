from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.base import MongoBaseModel, ObjectIdStr

JobStatus = Literal["queued", "running", "completed", "failed"]
JobTrigger = Literal["manual", "scheduled"]


class IngestionStats(BaseModel):
    fetched: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0


class IngestionJobCreate(BaseModel):
    source_id: ObjectIdStr
    max_items: int | None = Field(default=None, ge=1, le=100)
    trigger: JobTrigger = "manual"


class IngestionJobOut(MongoBaseModel):
    source_id: ObjectIdStr
    status: JobStatus
    trigger: str
    max_items: int
    stats: IngestionStats = Field(default_factory=IngestionStats)
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
