from datetime import UTC, datetime

from bson import ObjectId

from tests.helpers import become_editor


async def test_list_articles_filters_and_sorts(client, make_article):
    older = await make_article(
        title="Older story about forests",
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    newer = await make_article(
        title="Newer climate story",
        published_at=datetime(2024, 6, 1, tzinfo=UTC),
    )
    await make_article(title="Finnish story", language="fi")

    response = await client.get("/api/v1/articles", params={"language": "en"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == [str(newer["_id"]), str(older["_id"])]

    ascending = await client.get("/api/v1/articles", params={"language": "en", "sort": "oldest"})
    assert [item["id"] for item in ascending.json()["items"]] == [
        str(older["_id"]),
        str(newer["_id"]),
    ]

    search = await client.get("/api/v1/articles", params={"language": "en", "q": "climate"})
    assert search.json()["total"] == 1
    assert search.json()["items"][0]["id"] == str(newer["_id"])


async def test_list_articles_paginates(client, make_article):
    for index in range(3):
        await make_article(
            title=f"Story {index}",
            published_at=datetime(2024, index + 1, 1, tzinfo=UTC),
        )

    first = await client.get("/api/v1/articles", params={"language": "en", "page_size": 2})
    assert first.json()["total"] == 3
    assert first.json()["pages"] == 2
    assert len(first.json()["items"]) == 2

    second = await client.get(
        "/api/v1/articles", params={"language": "en", "page": 2, "page_size": 2}
    )
    assert len(second.json()["items"]) == 1


async def test_list_articles_by_topic(client, make_article):
    topic_id = ObjectId()
    tagged = await make_article(title="Tagged article", topic_ids=[topic_id])
    await make_article(title="Untagged article")

    response = await client.get(
        "/api/v1/articles", params={"language": "en", "topic_id": str(topic_id)}
    )
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == str(tagged["_id"])


async def test_article_detail_and_not_found(client, make_article):
    article = await make_article(title="Detailed story", content="Full body text.")

    response = await client.get(f"/api/v1/articles/{article['_id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Detailed story"
    assert body["content"] == "Full body text."
    assert body["topic_ids"] == []

    missing = await client.get("/api/v1/articles/not-an-object-id")
    assert missing.status_code == 404


async def test_related_articles_share_topics(client, make_article):
    topic_id = ObjectId()
    article = await make_article(title="Main story", topic_ids=[topic_id])
    related = await make_article(title="Related story", topic_ids=[topic_id])
    await make_article(title="Unrelated story", topic_ids=[ObjectId()])

    response = await client.get(f"/api/v1/articles/{article['_id']}/related")
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()]
    assert ids == [str(related["_id"])]


async def test_translations_lifecycle(client, authenticate, grant_roles, make_article):
    article = await make_article(title="Original headline")
    await become_editor(client, authenticate, grant_roles)

    created = await client.post(
        f"/api/v1/articles/{article['_id']}/translations",
        json={
            "language": "fi",
            "title": "Käännös",
            "summary": "Tiivistelmä",
            "content": "Sisältö",
        },
    )
    assert created.status_code == 201
    translation = created.json()
    assert translation["language"] == "fi"
    assert translation["article_id"] == str(article["_id"])

    updated = await client.post(
        f"/api/v1/articles/{article['_id']}/translations",
        json={"language": "fi", "title": "Päivitetty käännös"},
    )
    assert updated.status_code == 201
    assert updated.json()["title"] == "Päivitetty käännös"

    listing = await client.get(
        f"/api/v1/articles/{article['_id']}/translations", params={"language": "fi"}
    )
    assert len(listing.json()) == 1
    assert listing.json()[0]["title"] == "Päivitetty käännös"

    other_language = await client.get(
        f"/api/v1/articles/{article['_id']}/translations", params={"language": "sv"}
    )
    assert other_language.json() == []

    authenticate("user_reader_1")
    forbidden = await client.post(
        f"/api/v1/articles/{article['_id']}/translations",
        json={"language": "sv", "title": "Översättning"},
    )
    assert forbidden.status_code == 403


async def test_translation_requires_existing_article(client, authenticate, grant_roles):
    await become_editor(client, authenticate, grant_roles)
    response = await client.post(
        "/api/v1/articles/64b7f5b2f1f3a2c4d5e6f7a8/translations",
        json={"language": "fi", "title": "Otsikko"},
    )
    assert response.status_code == 404
