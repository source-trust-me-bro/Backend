from datetime import UTC, datetime
from uuid import uuid4


def article_document(**overrides):
    now = datetime.now(UTC)
    document = {
        "source_id": None,
        "source_name": "Test Source",
        "url": f"https://news.example.com/{uuid4()}",
        "external_id": None,
        "title": "Test headline",
        "summary": "A short summary of the test article.",
        "content": "Full article content used for search and extraction tests.",
        "language": "en",
        "author": None,
        "published_at": now,
        "fetched_at": now,
        "topic_ids": [],
        "embedding": [],
        "created_at": now,
        "updated_at": now,
    }
    document.update(overrides)
    return document


async def become_editor(client, authenticate, grant_roles, *, sub: str = "user_editor_1"):
    authenticate(sub)
    await grant_roles(["editor"], sub=sub)
    return sub


async def become_admin(client, authenticate, grant_roles, *, sub: str = "user_admin_1"):
    authenticate(sub)
    await grant_roles(["admin"], sub=sub)
    return sub


async def create_source(
    client,
    *,
    name: str = "Test Source",
    feed_url: str = "https://example.com/feed.xml",
    language: str = "en",
    **overrides,
):
    payload = {"name": name, "feed_url": feed_url, "language": language, **overrides}
    response = await client.post("/api/v1/sources", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def create_topic(client, *, name: str, language: str = "en", **overrides):
    payload = {"name": name, "language": language, **overrides}
    response = await client.post("/api/v1/topics", json=payload)
    assert response.status_code == 201, response.text
    return response.json()
