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


# --------------------- speed: fewer writes, pooled calls


async def test_a_repeat_request_does_not_write_to_the_user_profile(database):
    import asyncio
    from datetime import timedelta

    from app.repositories.users import UserRepository
    from app.schemas.user import ClerkPrincipal

    repo = UserRepository(database)
    principal = ClerkPrincipal(sub="user_a", email="a@example.com", display_name="Ann")

    first = await repo.sync_from_principal(principal, default_language="en")
    second = await repo.sync_from_principal(principal, default_language="en")

    assert second["updated_at"] == first["updated_at"]  # nothing was written the second time

    await asyncio.sleep(0.02)  # timestamps are stored to the millisecond
    changed = principal.model_copy(update={"display_name": "Ann Lee"})
    third = await repo.sync_from_principal(changed, default_language="en")
    assert third["display_name"] == "Ann Lee" and third["updated_at"] > first["updated_at"]

    # a stale last_seen_at is refreshed
    stale = third["last_seen_at"] - timedelta(minutes=10)
    users = database["users"]
    await users.update_one({"clerk_user_id": "user_a"}, {"$set": {"last_seen_at": stale}})
    fourth = await repo.sync_from_principal(changed, default_language="en")
    assert fourth["last_seen_at"] > stale + timedelta(minutes=9)  # refreshed to about now


async def test_calls_to_the_newsroom_share_one_connection_pool(monkeypatch):
    import httpx

    from app.config import Settings
    from app.services import newsroom_client
    from app.services.newsroom_client import NewsroomClient, close_newsroom_clients

    created: list[int] = []

    def fake_client(timeout: float) -> httpx.AsyncClient:
        created.append(1)
        return httpx.AsyncClient(
            timeout=timeout,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True})),
        )

    monkeypatch.setattr(newsroom_client, "_new_client", fake_client)
    client = NewsroomClient(Settings(newsroom_url="http://newsroom.test", newsroom_api_key="k"))

    for _ in range(4):
        assert await client.request("GET", "/health") == {"ok": True}
    await close_newsroom_clients()
    await client.request("GET", "/health")  # after closing, a fresh pool is made

    assert len(created) == 2  # one pool for the four calls, one after it was closed
    await close_newsroom_clients()
