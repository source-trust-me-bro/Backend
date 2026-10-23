from bson import ObjectId

from tests.helpers import become_admin, become_editor


async def test_first_request_provisions_reader_profile(client, database):
    response = await client.get("/api/v1/users/me")
    assert response.status_code == 200
    body = response.json()
    assert body["clerk_user_id"] == "user_reader_1"
    assert body["email"] == "user_reader_1@example.com"
    assert body["roles"] == ["reader"]
    assert body["preferred_language"] == "en"
    assert body["topic_ids"] == []
    assert body["community_id"] is None

    stored = await database["users"].find_one({"clerk_user_id": "user_reader_1"})
    assert stored is not None
    assert stored["roles"] == ["reader"]


async def test_profile_sync_is_idempotent(client, database):
    await client.get("/api/v1/users/me")
    await client.get("/api/v1/users/me")
    assert await database["users"].count_documents({"clerk_user_id": "user_reader_1"}) == 1


async def test_patch_preferences(client, database):
    await client.get("/api/v1/users/me")
    response = await client.patch(
        "/api/v1/users/me",
        json={"preferred_language": "fi", "display_name": "Updated Reader"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["preferred_language"] == "fi"
    assert body["display_name"] == "Updated Reader"

    stored = await database["users"].find_one({"clerk_user_id": "user_reader_1"})
    assert stored["preferred_language"] == "fi"


async def test_patch_rejects_unsupported_language(client):
    await client.get("/api/v1/users/me")
    response = await client.patch("/api/v1/users/me", json={"preferred_language": "de"})
    assert response.status_code == 422


async def test_reader_cannot_escalate_roles(client, database):
    await client.get("/api/v1/users/me")
    response = await client.patch("/api/v1/users/me", json={"roles": ["admin"]})
    assert response.status_code == 403
    stored = await database["users"].find_one({"clerk_user_id": "user_reader_1"})
    assert stored["roles"] == ["reader"]


async def test_patch_validates_community_and_topics(client):
    await client.get("/api/v1/users/me")
    missing_community = await client.patch(
        "/api/v1/users/me", json={"community_id": str(ObjectId())}
    )
    assert missing_community.status_code == 404

    missing_topic = await client.patch("/api/v1/users/me", json={"topic_ids": [str(ObjectId())]})
    assert missing_topic.status_code == 400


async def test_admin_assigns_roles(client, database, authenticate, grant_roles):
    await become_admin(client, authenticate, grant_roles)

    authenticate("user_target")
    await client.get("/api/v1/users/me")

    authenticate("user_admin_1")
    response = await client.patch("/api/v1/users/user_target/roles", json={"roles": ["editor"]})
    assert response.status_code == 200
    assert response.json()["roles"] == ["editor"]

    stored = await database["users"].find_one({"clerk_user_id": "user_target"})
    assert stored["roles"] == ["editor"]


async def test_editor_cannot_assign_roles(client, authenticate, grant_roles):
    await become_editor(client, authenticate, grant_roles)
    response = await client.patch("/api/v1/users/user_reader_1/roles", json={"roles": ["admin"]})
    assert response.status_code == 403


async def test_admin_can_update_own_roles_via_patch_me(client, authenticate, grant_roles):
    await become_admin(client, authenticate, grant_roles)
    response = await client.patch("/api/v1/users/me", json={"roles": ["reader", "editor", "admin"]})
    assert response.status_code == 200
    assert sorted(response.json()["roles"]) == ["admin", "editor", "reader"]
