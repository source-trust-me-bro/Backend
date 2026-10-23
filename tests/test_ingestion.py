from datetime import UTC, datetime

from bson import ObjectId

from app.api.deps import get_extraction_service, get_rss_service
from app.core.exceptions import UpstreamError
from app.services.extraction_service import ExtractionResult
from app.services.rss_service import FeedEntry
from tests.helpers import become_editor, create_source


class FakeRSSService:
    def __init__(self, entries=None, error=None):
        self.entries = entries or []
        self.error = error

    async def fetch_entries(self, feed_url, *, language=None, limit=20):
        if self.error is not None:
            raise self.error
        return self.entries[:limit]


class FakeExtractionService:
    def __init__(
        self,
        *,
        title="Extracted title",
        content="Extracted body text for the article.",
        author=None,
        language="en",
    ):
        self.title = title
        self.content = content
        self.author = author
        self.language = language

    async def extract(self, url, *, fallback_language=None):
        return ExtractionResult(
            title=self.title,
            content=self.content,
            author=self.author,
            language=self.language,
        )


def sample_entries():
    return [
        FeedEntry(
            title="First story",
            url="https://example.com/first",
            summary="Summary of the first story.",
            published_at=datetime(2024, 5, 1, 9, 0, tzinfo=UTC),
            external_id="first",
        ),
        FeedEntry(
            title="Second story",
            url="https://example.com/second",
            summary="Summary of the second story.",
            external_id="second",
        ),
    ]


async def setup_editor_with_source(client, authenticate, grant_roles):
    await become_editor(client, authenticate, grant_roles)
    return await create_source(client)


async def test_ingestion_job_creates_articles(client, app, authenticate, grant_roles, database):
    source = await setup_editor_with_source(client, authenticate, grant_roles)
    app.dependency_overrides[get_rss_service] = lambda: FakeRSSService(sample_entries())
    app.dependency_overrides[get_extraction_service] = lambda: FakeExtractionService()

    response = await client.post("/api/v1/ingestion/jobs", json={"source_id": source["id"]})
    assert response.status_code == 201
    job = response.json()
    assert job["status"] == "completed"
    assert job["stats"] == {
        "fetched": 2,
        "created": 2,
        "updated": 0,
        "skipped": 0,
        "failed": 0,
    }

    stored = await database["articles"].find_one({"url": "https://example.com/first"})
    assert stored is not None
    assert stored["title"] == "First story"
    assert stored["content"] == "Extracted body text for the article."
    assert stored["summary"] == "Summary of the first story."
    assert stored["language"] == "en"
    assert stored["embedding"]
    assert stored["published_at"] is not None


async def test_reingestion_updates_instead_of_duplicating(
    client, app, authenticate, grant_roles, database
):
    source = await setup_editor_with_source(client, authenticate, grant_roles)
    app.dependency_overrides[get_rss_service] = lambda: FakeRSSService(sample_entries())
    app.dependency_overrides[get_extraction_service] = lambda: FakeExtractionService()

    await client.post("/api/v1/ingestion/jobs", json={"source_id": source["id"]})
    second = await client.post("/api/v1/ingestion/jobs", json={"source_id": source["id"]})
    assert second.status_code == 201
    assert second.json()["stats"]["updated"] == 2
    assert second.json()["stats"]["created"] == 0
    assert await database["articles"].count_documents({}) == 2


async def test_ingestion_jobs_are_listed(client, app, authenticate, grant_roles):
    source = await setup_editor_with_source(client, authenticate, grant_roles)
    app.dependency_overrides[get_rss_service] = lambda: FakeRSSService(sample_entries())
    app.dependency_overrides[get_extraction_service] = lambda: FakeExtractionService()
    await client.post("/api/v1/ingestion/jobs", json={"source_id": source["id"]})

    listing = await client.get("/api/v1/ingestion/jobs", params={"source_id": source["id"]})
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["status"] == "completed"


async def test_feed_failure_marks_job_failed(client, app, authenticate, grant_roles, database):
    source = await setup_editor_with_source(client, authenticate, grant_roles)
    app.dependency_overrides[get_rss_service] = lambda: FakeRSSService(
        error=UpstreamError("Feed is unavailable")
    )

    response = await client.post("/api/v1/ingestion/jobs", json={"source_id": source["id"]})
    assert response.status_code == 502
    assert response.json()["code"] == "upstream_error"

    job = await database["ingestion_jobs"].find_one({"source_id": ObjectId(source["id"])})
    assert job["status"] == "failed"
    assert "unavailable" in job["error"]


async def test_ingestion_requires_editor_and_existing_source(client, authenticate, grant_roles):
    missing_source = await client.post(
        "/api/v1/ingestion/jobs", json={"source_id": str(ObjectId())}
    )
    assert missing_source.status_code == 403

    await become_editor(client, authenticate, grant_roles)
    missing_source = await client.post(
        "/api/v1/ingestion/jobs", json={"source_id": str(ObjectId())}
    )
    assert missing_source.status_code == 404


async def test_manual_article_with_inline_content(client, app, authenticate, grant_roles, database):
    source = await setup_editor_with_source(client, authenticate, grant_roles)
    response = await client.post(
        "/api/v1/ingestion/articles",
        json={
            "source_id": source["id"],
            "title": "Manual editorial",
            "content": "A manually supplied article body with enough words to detect language.",
            "language": "en",
            "author": "Staff Writer",
        },
    )
    assert response.status_code == 201
    article = response.json()
    assert article["source_name"] == "Test Source"
    assert article["url"].startswith("manual://article/")
    assert article["summary"]

    stored = await database["articles"].find_one({"_id": ObjectId(article["id"])})
    assert stored["author"] == "Staff Writer"
    assert stored["embedding"]


async def test_manual_article_from_url(client, app, authenticate, grant_roles):
    await setup_editor_with_source(client, authenticate, grant_roles)
    app.dependency_overrides[get_extraction_service] = lambda: FakeExtractionService(
        title="Fetched headline", content="Fetched body content.", author="Reporter"
    )

    response = await client.post(
        "/api/v1/ingestion/articles",
        json={"url": "https://example.com/story"},
    )
    assert response.status_code == 201
    article = response.json()
    assert article["title"] == "Fetched headline"
    assert article["author"] == "Reporter"
    assert article["content"] == "Fetched body content."
    assert article["source_name"] == "manual"


async def test_manual_article_validation(client, authenticate, grant_roles):
    await become_editor(client, authenticate, grant_roles)

    missing_content = await client.post(
        "/api/v1/ingestion/articles", json={"title": "No body", "content": None}
    )
    assert missing_content.status_code == 422

    unknown_topic = await client.post(
        "/api/v1/ingestion/articles",
        json={
            "title": "Topic test",
            "content": "Some article body text.",
            "topic_ids": [str(ObjectId())],
        },
    )
    assert unknown_topic.status_code == 400
