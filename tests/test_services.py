import httpx
import pytest

from app.core.exceptions import BadRequestError
from app.core.utils import slugify, validate_http_url, validate_public_url
from app.repositories.articles import tokenize_query
from app.services.embedding_service import EmbeddingService
from app.services.extraction_service import ExtractionService
from app.services.rss_service import RSSService

FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example feed</title>
    <link>https://example.com</link>
    <description>Example news</description>
    <item>
      <title>First post</title>
      <link>https://example.com/first</link>
      <guid>post-1</guid>
      <description>&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt;&lt;/p&gt;</description>
      <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Second post</title>
      <link>https://example.com/second</link>
      <pubDate>Tue, 02 Jan 2024 12:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""

ARTICLE_HTML = """
<html>
  <head>
    <title>City council approves new budget</title>
    <meta property="og:title" content="City council approves new budget" />
  </head>
  <body>
    <article>
      <h1>City council approves new budget</h1>
      <p>
        The city council approved a new annual budget on Tuesday after a long debate
        about public transport and housing across the metropolitan area.
      </p>
      <p>
        Officials said the plan invests in schools, cycling lanes, and local health
        services for residents in every neighbourhood.
      </p>
    </article>
  </body>
</html>
"""


async def test_rss_service_parses_entries():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"]
        return httpx.Response(200, content=FEED_XML)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = RSSService(client=client)
        entries = await service.fetch_entries("https://example.com/feed.xml", language="fi")

    assert len(entries) == 2
    assert entries[0].title == "First post"
    assert entries[0].url == "https://example.com/first"
    assert entries[0].summary == "Hello world"
    assert entries[0].external_id == "post-1"
    assert entries[0].language == "fi"
    assert entries[0].published_at is not None
    assert entries[0].published_at.tzinfo is not None
    assert entries[1].summary is None


async def test_rss_service_respects_limit():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=FEED_XML))
    ) as client:
        service = RSSService(client=client)
        entries = await service.fetch_entries("https://example.com/feed.xml", limit=1)
    assert len(entries) == 1


async def test_rss_service_rejects_private_urls():
    service = RSSService()
    with pytest.raises(BadRequestError):
        await service.fetch_entries("http://127.0.0.1/feed.xml")


async def test_rss_service_reports_http_errors():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(503, content=b"unavailable"))
    ) as client:
        service = RSSService(client=client)
        with pytest.raises(Exception) as excinfo:
            await service.fetch_entries("https://example.com/feed.xml")
    assert "Failed to fetch RSS feed" in str(excinfo.value)


def test_extraction_from_html():
    service = ExtractionService()
    result = service.extract_from_html(ARTICLE_HTML, url="https://example.com/story")

    assert result.title == "City council approves new budget"
    assert result.content is not None
    assert "public transport" in result.content.lower()
    assert "cycling lanes" in result.content.lower()
    assert result.language == "en"


async def test_extraction_rejects_private_urls():
    service = ExtractionService()
    with pytest.raises(BadRequestError):
        await service.extract("http://10.0.0.5/story")


def test_embeddings_are_deterministic_and_normalized():
    embedder = EmbeddingService()
    first = embedder.embed("Climate policy in Finland")
    second = embedder.embed("Climate policy in Finland")
    other = embedder.embed("Football match report")

    assert first == second
    assert len(first) == 384
    assert first != other
    assert embedder.embed("") == []


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/feed",
        "http://127.0.0.1/feed",
        "http://10.1.1.1/feed",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/feed",
        "file:///etc/passwd",
        "not-a-url",
    ],
)
def test_public_url_validation_rejects_unsafe_targets(url):
    with pytest.raises(ValueError):
        validate_public_url(url)


def test_public_url_validation_allows_public_hosts():
    assert validate_public_url("https://example.com/feed.xml") == "https://example.com/feed.xml"
    assert validate_http_url("https://example.com/feed.xml") == "https://example.com/feed.xml"


def test_tokenize_query_deduplicates_and_limits():
    assert tokenize_query("Climate climate POLICY energy", max_tokens=3) == [
        "climate",
        "policy",
        "energy",
    ]
    assert tokenize_query("!!! ???") == []


def test_slugify_handles_unicode_and_symbols():
    assert slugify("Helsingin Sanomat") == "helsingin-sanomat"
    assert slugify("Ääni") == "aani"
    assert slugify("!!!").startswith("item-")
