import hashlib
import ipaddress
import math
import re
import unicodedata
from datetime import UTC, datetime
from urllib.parse import urlparse

from bson import ObjectId

_WHITESPACE_RE = re.compile(r"\s+")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_object_id(value: str | ObjectId | None) -> ObjectId | None:
    if value is None:
        return None
    if isinstance(value, ObjectId):
        return value
    if not ObjectId.is_valid(value):
        return None
    return ObjectId(value)


def require_object_id(value: str | ObjectId | None, *, field: str = "id") -> ObjectId:
    object_id = to_object_id(value)
    if object_id is None:
        from app.core.exceptions import NotFoundError

        raise NotFoundError(f"Resource with {field} '{value}' was not found.")
    return object_id


def slugify(value: str, *, fallback_prefix: str = "item") -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_RE.sub("-", ascii_value).strip("-")
    if not slug:
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]
        slug = f"{fallback_prefix}-{digest}"
    return slug[:80]


def escape_regex(value: str) -> str:
    return re.escape(value)


def validate_http_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must be an absolute http(s) URL.")
    return value


def validate_public_url(value: str) -> str:
    """Validate a fetch target and reject obvious SSRF targets."""

    validate_http_url(value)
    host = (urlparse(value).hostname or "").strip().lower()
    if not host:
        raise ValueError("URL must include a hostname.")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("Refusing to fetch a local network URL.")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return value

    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise ValueError("Refusing to fetch a private or reserved network address.")
    return value


def normalize_whitespace(value: str | None) -> str | None:
    if value is None:
        return None
    collapsed = _WHITESPACE_RE.sub(" ", value).strip()
    return collapsed or None


def truncate(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    if len(value) <= limit:
        return value
    return value[:limit].rstrip()


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
