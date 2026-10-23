from bson import ObjectId

from tests.helpers import become_editor, create_topic


async def test_community_lifecycle_and_feed(client, authenticate, grant_roles, make_article):
    await become_editor(client, authenticate, grant_roles)
    environment = await create_topic(client, name="Environment")
    transport = await create_topic(client, name="Transport")

    created = await client.post(
        "/api/v1/communities",
        json={
            "name": "Helsinki News",
            "description": "Local news for Helsinki.",
            "language": "en",
            "topic_ids": [environment["id"]],
        },
    )
    assert created.status_code == 201
    community = created.json()
    assert community["slug"] == "helsinki-news"
    assert community["topic_ids"] == [environment["id"]]

    listed = await client.get("/api/v1/communities")
    assert listed.json()["total"] == 1

    fetched = await client.get(f"/api/v1/communities/{community['id']}")
    assert fetched.status_code == 200

    environment_article = await make_article(
        title="Environment story", topic_ids=[ObjectId(environment["id"])]
    )
    transport_article = await make_article(
        title="Transport story", topic_ids=[ObjectId(transport["id"])]
    )
    await make_article(title="Unrelated story")

    feed = await client.get(f"/api/v1/communities/{community['id']}/feed")
    assert feed.status_code == 200
    assert feed.json()["total"] == 1
    assert feed.json()["items"][0]["id"] == str(environment_article["_id"])

    patched = await client.patch(
        f"/api/v1/communities/{community['id']}",
        json={"topic_ids": [environment["id"], transport["id"]]},
    )
    assert patched.status_code == 200

    expanded = await client.get(f"/api/v1/communities/{community['id']}/feed")
    assert expanded.json()["total"] == 2
    ids = {item["id"] for item in expanded.json()["items"]}
    assert ids == {str(environment_article["_id"]), str(transport_article["_id"])}


async def test_community_editor_assignment(client, authenticate, grant_roles):
    await become_editor(client, authenticate, grant_roles)
    created = await client.post(
        "/api/v1/communities", json={"name": "Open Community", "language": "en"}
    )
    community_id = created.json()["id"]

    authenticate("user_reader_1")
    reader = await client.get("/api/v1/users/me")

    authenticate("user_editor_1")
    patched = await client.patch(
        f"/api/v1/communities/{community_id}",
        json={"editor_user_ids": [reader.json()["id"]]},
    )
    assert patched.status_code == 200
    assert patched.json()["editor_user_ids"] == [reader.json()["id"]]


async def test_community_reference_validation(client, authenticate, grant_roles):
    await become_editor(client, authenticate, grant_roles)
    invalid_topic = await client.post(
        "/api/v1/communities",
        json={"name": "Broken", "language": "en", "topic_ids": [str(ObjectId())]},
    )
    assert invalid_topic.status_code == 400

    invalid_editor = await client.post(
        "/api/v1/communities",
        json={"name": "Broken", "language": "en", "editor_user_ids": [str(ObjectId())]},
    )
    assert invalid_editor.status_code == 400


async def test_reader_cannot_create_community(client):
    response = await client.post(
        "/api/v1/communities", json={"name": "Reader Community", "language": "en"}
    )
    assert response.status_code == 403


async def test_unknown_community(client):
    response = await client.get("/api/v1/communities/64b7f5b2f1f3a2c4d5e6f7a8")
    assert response.status_code == 404

    feed = await client.get("/api/v1/communities/64b7f5b2f1f3a2c4d5e6f7a8/feed")
    assert feed.status_code == 404
