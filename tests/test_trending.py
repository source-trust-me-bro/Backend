from datetime import timedelta

from app.core.utils import utcnow


async def _read(client, authenticate, article, *, sub: str, times: int = 1):
    authenticate(sub)
    for _ in range(times):
        response = await client.post(f"/api/v1/library/{article['_id']}/read")
        assert response.status_code == 204


async def test_trending_ranks_by_reads(client, authenticate, make_article):
    quiet = await make_article(title="Quiet")
    popular = await make_article(title="Popular")
    middle = await make_article(title="Middle")

    await _read(client, authenticate, quiet, sub="user_a")
    await _read(client, authenticate, popular, sub="user_a", times=3)
    await _read(client, authenticate, popular, sub="user_b")
    await _read(client, authenticate, middle, sub="user_b", times=2)

    response = await client.get("/api/v1/articles/trending", params={"limit": 3})

    assert response.status_code == 200
    assert [item["title"] for item in response.json()] == ["Popular", "Middle", "Quiet"]


async def test_trending_breaks_ties_by_distinct_readers(client, authenticate, make_article):
    solo = await make_article(title="One reader twice")
    shared = await make_article(title="Two readers once each")

    await _read(client, authenticate, solo, sub="user_a", times=2)
    await _read(client, authenticate, shared, sub="user_a")
    await _read(client, authenticate, shared, sub="user_b")

    response = await client.get("/api/v1/articles/trending", params={"limit": 2})

    assert [item["title"] for item in response.json()] == [
        "Two readers once each",
        "One reader twice",
    ]


async def test_trending_ignores_reads_older_than_a_week(
    client, authenticate, database, make_article
):
    stale = await make_article(title="Stale", published_at=utcnow() - timedelta(days=30))
    fresh = await make_article(title="Fresh")
    await _read(client, authenticate, stale, sub="user_a", times=5)
    await _read(client, authenticate, fresh, sub="user_a")
    await database["user_articles"].update_one(
        {"article_id": stale["_id"]},
        {"$set": {"last_read_at": utcnow() - timedelta(days=8)}},
    )

    response = await client.get("/api/v1/articles/trending", params={"limit": 2})

    assert [item["title"] for item in response.json()] == ["Fresh", "Stale"]


async def test_trending_pads_with_newest_without_duplicates(client, authenticate, make_article):
    now = utcnow()
    read = await make_article(title="Read once", published_at=now - timedelta(days=5))
    await make_article(title="Newest", published_at=now)
    await make_article(title="Older", published_at=now - timedelta(days=1))
    await _read(client, authenticate, read, sub="user_a")

    response = await client.get("/api/v1/articles/trending", params={"limit": 3})

    assert [item["title"] for item in response.json()] == ["Read once", "Newest", "Older"]


async def test_trending_filters_by_language(client, authenticate, make_article):
    finnish = await make_article(title="Suomi", language="fi")
    english = await make_article(title="English", language="en")
    await _read(client, authenticate, finnish, sub="user_a", times=4)
    await _read(client, authenticate, english, sub="user_a")

    response = await client.get("/api/v1/articles/trending", params={"language": "en"})

    assert [item["title"] for item in response.json()] == ["English"]


async def test_trending_respects_limit_and_validates_it(client, make_article):
    for index in range(4):
        await make_article(title=f"Story {index}")

    assert len((await client.get("/api/v1/articles/trending", params={"limit": 2})).json()) == 2
    assert (await client.get("/api/v1/articles/trending", params={"limit": 0})).status_code == 422
    assert (await client.get("/api/v1/articles/trending", params={"limit": 21})).status_code == 422


async def test_trending_is_empty_without_articles(client):
    response = await client.get("/api/v1/articles/trending")

    assert response.status_code == 200
    assert response.json() == []


async def test_trending_is_not_shadowed_by_article_id_route(client):
    response = await client.get("/api/v1/articles/trending")

    assert response.status_code == 200
