import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from bs4 import BeautifulSoup
from langdetect import DetectorFactory, LangDetectException, detect

from app.config import Settings, get_settings
from app.core.exceptions import BadRequestError, UpstreamError
from app.core.utils import normalize_whitespace, truncate, validate_public_url

logger = logging.getLogger(__name__)

DetectorFactory.seed = 0

_ISO_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


@dataclass(slots=True)
class ExtractionResult:
    title: str | None = None
    content: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    language: str | None = None
    site_name: str | None = None


class ExtractionService:
    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._client = client

    async def extract(self, url: str, *, fallback_language: str | None = None) -> ExtractionResult:
        try:
            validate_public_url(url)
        except ValueError as exc:
            raise BadRequestError(str(exc)) from exc
        headers = {
            "User-Agent": self.settings.feed_user_agent,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        }
        try:
            if self._client is not None:
                response = await self._client.get(url, headers=headers, follow_redirects=True)
            else:
                async with httpx.AsyncClient(
                    timeout=self.settings.http_timeout_seconds, follow_redirects=True
                ) as client:
                    response = await client.get(url, headers=headers)
            try:
                validate_public_url(str(response.url))
            except ValueError as exc:
                raise BadRequestError(str(exc)) from exc
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise UpstreamError(f"Failed to fetch article: {exc}") from exc
        return self.extract_from_html(
            response.text, url=str(response.url), fallback_language=fallback_language
        )

    def extract_from_html(
        self,
        html: str,
        *,
        url: str | None = None,
        fallback_language: str | None = None,
    ) -> ExtractionResult:
        result = ExtractionResult()
        metadata = _read_trafilatura_metadata(html, url)
        if metadata is not None:
            result.title = normalize_whitespace(getattr(metadata, "title", None))
            result.author = normalize_whitespace(getattr(metadata, "author", None))
            result.site_name = normalize_whitespace(getattr(metadata, "sitename", None))
            result.published_at = _parse_metadata_date(getattr(metadata, "date", None))
            result.language = _normalize_language(getattr(metadata, "language", None))

        content = _trafilatura_text(html, url)
        if not content:
            content, fallback_title = _beautifulsoup_text(html)
            result.title = result.title or fallback_title

        content = truncate(normalize_whitespace(content), self.settings.max_content_chars)
        result.content = content

        detected = detect_language(
            " ".join(part for part in (result.title, content) if part),
            supported=self.settings.supported_languages,
            fallback=fallback_language or result.language or self.settings.default_language,
        )
        result.language = detected
        return result


def detect_language(text: str | None, *, supported: list[str], fallback: str | None) -> str | None:
    cleaned = (text or "").strip()
    if len(cleaned) < 20:
        return fallback
    try:
        detected = detect(cleaned[:2000])
    except LangDetectException:
        return fallback
    base = detected.replace("_", "-").split("-")[0].lower()
    return base if base in supported else fallback


def strip_html(value: str | None) -> str | None:
    if not value:
        return None
    return normalize_whitespace(BeautifulSoup(value, "lxml").get_text(" ", strip=True))


def _read_trafilatura_metadata(html: str, url: str | None):
    try:
        from trafilatura.metadata import extract_metadata
    except ImportError:
        return None
    try:
        return extract_metadata(html, default_url=url)
    except Exception:
        logger.debug("Metadata extraction failed", exc_info=True)
        return None


def _trafilatura_text(html: str, url: str | None) -> str | None:
    try:
        import trafilatura
    except ImportError:
        return None
    try:
        return trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
            no_fallback=False,
        )
    except Exception:
        logger.debug("trafilatura extraction failed", exc_info=True)
        return None


def _beautifulsoup_text(html: str) -> tuple[str | None, str | None]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()

    title = None
    if soup.title and soup.title.string:
        title = normalize_whitespace(soup.title.string)
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = normalize_whitespace(str(og_title["content"])) or title

    container = soup.find("article") or soup.find("main") or soup.body or soup
    paragraphs = [
        text
        for element in container.find_all(["p", "h2", "h3"])
        if (text := normalize_whitespace(element.get_text(" ", strip=True)))
    ]
    content = (
        "\n\n".join(paragraphs) if paragraphs else normalize_whitespace(container.get_text(" "))
    )
    return content, title


def _parse_metadata_date(value: str | None) -> datetime | None:
    if not value:
        return None
    match = _ISO_DATE_RE.search(value)
    if not match:
        return None
    try:
        return datetime.fromisoformat(match.group(1)).replace(tzinfo=UTC)
    except ValueError:
        return None


def _normalize_language(value: str | None) -> str | None:
    if not value:
        return None
    return value.replace("_", "-").split("-")[0].lower()
