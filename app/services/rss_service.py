import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import feedparser
import httpx
from bs4 import BeautifulSoup

from app.config import Settings, get_settings
from app.core.exceptions import BadRequestError, UpstreamError
from app.core.utils import validate_public_url
from app.services.images import image_from_entry

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class FeedEntry:
    title: str | None
    url: str
    summary: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    external_id: str | None = None
    language: str | None = None
    image_url: str | None = None  # a link to the publisher's photo, never a copy


class RSSService:
    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._client = client

    async def fetch_entries(
        self, feed_url: str, *, language: str | None = None, limit: int = 20
    ) -> list[FeedEntry]:
        try:
            validate_public_url(feed_url)
        except ValueError as exc:
            raise BadRequestError(str(exc)) from exc
        payload = await self._fetch(feed_url)
        parsed = feedparser.parse(payload)

        if parsed.bozo and not parsed.entries:
            reason = getattr(parsed, "bozo_exception", "unknown parsing error")
            raise UpstreamError(f"Could not parse RSS feed: {reason}")
        if not parsed.entries:
            logger.info("Feed %s contained no entries", feed_url)

        entries: list[FeedEntry] = []
        for raw in parsed.entries:
            entry = self._to_entry(raw, default_language=language)
            if entry is not None:
                entries.append(entry)
            if len(entries) >= limit:
                break
        return entries

    async def _fetch(self, feed_url: str) -> bytes:
        headers = {
            "User-Agent": self.settings.feed_user_agent,
            "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
        }
        try:
            if self._client is not None:
                response = await self._client.get(feed_url, headers=headers, follow_redirects=True)
            else:
                async with httpx.AsyncClient(
                    timeout=self.settings.http_timeout_seconds, follow_redirects=True
                ) as client:
                    response = await client.get(feed_url, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise UpstreamError(f"Failed to fetch RSS feed: {exc}") from exc
        return response.content

    def _to_entry(self, raw, *, default_language: str | None) -> FeedEntry | None:
        url = (raw.get("link") or "").strip()
        if not url.startswith(("http://", "https://")):
            return None
        title = (raw.get("title") or "").strip() or None
        summary = _clean_html(raw.get("summary") or raw.get("description"))
        author = raw.get("author") or None

        published_at = None
        for key in ("published_parsed", "updated_parsed"):
            parsed_time = raw.get(key)
            if parsed_time:
                published_at = datetime(*parsed_time[:6], tzinfo=UTC)
                break

        return FeedEntry(
            title=title,
            url=url,
            summary=summary,
            author=author,
            published_at=published_at,
            external_id=(raw.get("id") or url)[:200],
            language=_normalize_feed_language(raw.get("language") or default_language),
            image_url=image_from_entry(raw, url),
        )


def _clean_html(value: str | None) -> str | None:
    if not value:
        return None
    text = BeautifulSoup(value, "lxml").get_text(" ", strip=True)
    collapsed = " ".join(text.split())
    return collapsed or None


def _normalize_feed_language(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().lower().replace("_", "-").split("-")[0]
