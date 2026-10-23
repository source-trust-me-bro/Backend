from datetime import datetime
from typing import Annotated, Any, Generic, TypeVar

from bson import ObjectId
from pydantic import BaseModel, BeforeValidator, ConfigDict, model_validator

from app.config import get_settings

T = TypeVar("T")


def _validate_language(value: Any) -> Any:
    if value is None:
        return None
    code = str(value).strip().lower()
    supported = get_settings().supported_languages
    if code not in supported:
        raise ValueError(
            f"Unsupported language '{code}'. Supported languages: {', '.join(supported)}."
        )
    return code


def _to_object_id_string(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    return value


def _to_object_id_string_list(value: Any) -> Any:
    if value is None:
        return []
    return [str(item) if isinstance(item, ObjectId) else item for item in value]


LanguageCode = Annotated[str, BeforeValidator(_validate_language)]
ObjectIdStr = Annotated[str, BeforeValidator(_to_object_id_string)]
OptionalObjectIdStr = Annotated[str | None, BeforeValidator(_to_object_id_string)]
ObjectIdList = Annotated[list[str], BeforeValidator(_to_object_id_string_list)]


class MongoBaseModel(BaseModel):
    """Base response model mapping Mongo's `_id` to `id`."""

    model_config = ConfigDict(populate_by_name=True)

    id: str

    @model_validator(mode="before")
    @classmethod
    def _map_object_id(cls, value: Any) -> Any:
        if isinstance(value, dict):
            data = dict(value)
            if "_id" in data:
                data["id"] = str(data["_id"])
            data.pop("_id", None)
            return data
        return value


class TimestampedModel(MongoBaseModel):
    created_at: datetime
    updated_at: datetime


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int

    @classmethod
    def build(cls, items: list[T], total: int, page: int, page_size: int) -> "Page[T]":
        pages = (total + page_size - 1) // page_size if total else 0
        return cls(items=items, total=total, page=page, page_size=page_size, pages=pages)
