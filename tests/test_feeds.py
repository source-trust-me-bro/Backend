from bson import ObjectId

from tests.helpers import become_editor, create_topic


async def test_personalized_feed_uses_language_and_topics(
    client, authenticate, grant_roles, make_article
):
    await become_editor(client, authenticate, grant_roles)
    finnish_topic = await create_topic(client, name="Uutiset", language="fi")
    english_topic = await create_topic(client, name="News", language="en")

    finnish_match = await make_article(
        title="Suomalainen uutinen",
        language="fi",
        topic_ids=[ObjectId(finnish_topic["id"])],
    )
    await make_article(
        title="Toinen suomalainen uutinen",
        language="fi",
        topic_ids=[ObjectId(english_topic["id"])],
    )
    await make_article(
        title="English article",
        language="en",
        topic_ids=[ObjectId(english_topic["id"])],
    )

    authenticate("user_reader_1")
    await client.get("/api/v1/users/me")
    updated = await client.patch(
        "/api/v1/users/me",
        json={"preferred_language": "fi", "topic_ids": [finnish_topic["id"]]},
    )
    assert updated.status_code == 200

    feed = await client.get("/api/v1/feeds/personalized")
    assert feed.status_code == 200
    assert feed.json()["total"] == 1
    assert feed.json()["items"][0]["id"] == str(finnish_match["_id"])

    language_override = await client.get("/api/v1/feeds/personalized", params={"language": "en"})
    assert language_override.json()["total"] == 0


async def test_personalized_feed_uses_community_topics(
    client, authenticate, grant_roles, make_article
):
    await become_editor(client, authenticate, grant_roles)
    topic = await create_topic(client, name="Science", language="en")
    community = await client.post(
        "/api/v1/communities",
        json={"name": "Science Readers", "language": "en", "topic_ids": [topic["id"]]},
    )
    assert community.status_code == 201
    community_id = community.json()["id"]

    matching = await make_article(title="Science discovery", topic_ids=[ObjectId(topic["id"])])
    await make_article(title="Other news")

    authenticate("user_reader_1")
    await client.get("/api/v1/users/me")
    updated = await client.patch(
        "/api/v1/users/me",
        json={"preferred_language": "en", "community_id": community_id},
    )
    assert updated.status_code == 200

    feed = await client.get("/api/v1/feeds/personalized")
    assert feed.status_code == 200
    assert feed.json()["total"] == 1
    assert feed.json()["items"][0]["id"] == str(matching["_id"])


async def test_personalized_feed_without_interests_returns_latest(client, make_article):
    newest = await make_article(title="Latest English story")
    await make_article(title="Finnish story", language="fi")

    feed = await client.get("/api/v1/feeds/personalized")
    assert feed.status_code == 200
    assert feed.json()["total"] == 1
    assert feed.json()["items"][0]["id"] == str(newest["_id"])
