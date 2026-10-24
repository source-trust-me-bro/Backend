from pydantic import BaseModel, Field


class FlagUpdate(BaseModel):
    value: bool


class LibraryState(BaseModel):
    saved: list[str] = Field(default_factory=list)
    downloaded: list[str] = Field(default_factory=list)


class SavedResult(BaseModel):
    saved: bool


class DownloadedResult(BaseModel):
    downloaded: bool


class FollowResult(BaseModel):
    following: bool
