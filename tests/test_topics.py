from tests.helpers import become_editor, create_topic


async def test_reader_cannot_create_topic(client):
    response = await client.post("/api/v1/topics", json={"name": "Politics", "language": "en"})
    assert response.status_code == 403


async def test_topic_tree_and_updates(client, authenticate, grant_roles):
    await become_editor(client, authenticate, grant_roles)

    parent = await create_topic(client, name="Politics", keywords=["election", "parliament"])
    child = await create_topic(client, name="Elections", parent_id=parent["id"], keywords=["Vote"])

    tree = await client.get("/api/v1/topics/tree")
    assert tree.status_code == 200
    nodes = tree.json()
    assert len(nodes) == 1
    assert nodes[0]["name"] == "Politics"
    assert nodes[0]["slug"] == "politics"
    assert nodes[0]["keywords"] == ["election", "parliament"]
    assert len(nodes[0]["children"]) == 1
    assert nodes[0]["children"][0]["id"] == child["id"]

    filtered = await client.get("/api/v1/topics/tree", params={"language": "fi"})
    assert filtered.json() == []

    patched = await client.patch(
        f"/api/v1/topics/{parent['id']}",
        json={"name": "National politics", "keywords": ["Budget", "budget"]},
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "National politics"
    assert patched.json()["keywords"] == ["budget"]
    assert patched.json()["slug"] == "politics"


async def test_topic_slug_conflicts(client, authenticate, grant_roles):
    await become_editor(client, authenticate, grant_roles)
    await create_topic(client, name="Economy")
    response = await client.post("/api/v1/topics", json={"name": "Economy", "language": "en"})
    assert response.status_code == 409


async def test_topic_parent_validation(client, authenticate, grant_roles):
    await become_editor(client, authenticate, grant_roles)
    missing_parent = await client.post(
        "/api/v1/topics",
        json={
            "name": "Orphan",
            "language": "en",
            "parent_id": "64b7f5b2f1f3a2c4d5e6f7a8",
        },
    )
    assert missing_parent.status_code == 404

    topic = await create_topic(client, name="Culture")
    self_parent = await client.patch(
        f"/api/v1/topics/{topic['id']}", json={"parent_id": topic["id"]}
    )
    assert self_parent.status_code == 400

    cleared = await client.patch(f"/api/v1/topics/{topic['id']}", json={"parent_id": None})
    assert cleared.status_code == 200
    assert cleared.json()["parent_id"] is None


async def test_unknown_topic_update(client, authenticate, grant_roles):
    await become_editor(client, authenticate, grant_roles)
    response = await client.patch("/api/v1/topics/not-an-id", json={"name": "Nope"})
    assert response.status_code == 404


async def test_topic_branch_relationship_is_symmetric_and_visible(
    client, authenticate, grant_roles
):
    await become_editor(client, authenticate, grant_roles)
    parent = await create_topic(client, name="Climate")
    child = await create_topic(client, name="Emissions")
    await create_topic(client, name="Sports")

    response = await client.post(
        f"/api/v1/topics/{parent['id']}/branches", json={"branch_id": child["id"]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["branches"] == [child["id"]]

    mirrored = await client.post(
        f"/api/v1/topics/{child['id']}/branches", json={"branch_id": parent["id"]}
    )
    assert mirrored.status_code == 200
    assert mirrored.json()["branches"] == [parent["id"]]

    tree = await client.get("/api/v1/topics/tree")
    branches_by_name = {node["name"]: node["branches"] for node in tree.json()}
    assert branches_by_name["Climate"] == [child["id"]]
    assert branches_by_name["Emissions"] == [parent["id"]]
    assert branches_by_name["Sports"] == []


async def test_topic_branch_rejects_self_link(client, authenticate, grant_roles):
    await become_editor(client, authenticate, grant_roles)
    topic = await create_topic(client, name="Culture")
    response = await client.post(
        f"/api/v1/topics/{topic['id']}/branches", json={"branch_id": topic["id"]}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "bad_request"


async def test_topic_branch_rejects_unknown_topic_ids(client, authenticate, grant_roles):
    await become_editor(client, authenticate, grant_roles)
    topic = await create_topic(client, name="Culture")

    missing = await client.post(
        f"/api/v1/topics/{topic['id']}/branches",
        json={"branch_id": "64b7f5b2f1f3a2c4d5e6f7a8"},
    )
    assert missing.status_code == 404

    unknown_source = await client.post(
        "/api/v1/topics/64b7f5b2f1f3a2c4d5e6f7a8/branches",
        json={"branch_id": topic["id"]},
    )
    assert unknown_source.status_code == 404

    malformed = await client.post(
        f"/api/v1/topics/{topic['id']}/branches", json={"branch_id": "not-an-id"}
    )
    assert malformed.status_code == 404


async def test_topic_branch_duplicates_are_idempotent(client, authenticate, grant_roles, database):
    from bson import ObjectId

    await become_editor(client, authenticate, grant_roles)
    first = await create_topic(client, name="Climate")
    second = await create_topic(client, name="Emissions")
    third = await create_topic(client, name="Sports")
    await client.post(f"/api/v1/topics/{first['id']}/branches", json={"branch_id": third["id"]})

    repeat = await client.post(
        f"/api/v1/topics/{first['id']}/branches", json={"branch_id": second["id"]}
    )
    assert repeat.status_code == 200
    again = await client.post(
        f"/api/v1/topics/{first['id']}/branches", json={"branch_id": second["id"]}
    )
    assert again.status_code == 200

    assert again.json()["branches"] == [third["id"], second["id"]]
    assert again.json() == repeat.json()

    stored = await database["topics"].find_one({"_id": ObjectId(first["id"])})
    stored_ids = [str(value) for value in stored["branches"]]
    assert stored_ids == [third["id"], second["id"]]
    assert len(stored_ids) == len(set(stored_ids))

    mirrored = await database["topics"].find_one({"_id": ObjectId(second["id"])})
    assert mirrored["branches"] == [ObjectId(first["id"])]
    unrelated = await database["topics"].find_one({"_id": ObjectId(third["id"])})
    assert unrelated["branches"] == [ObjectId(first["id"])]


async def test_topic_branch_write_failure_keeps_both_sides_consistent(
    client, app, authenticate, grant_roles, monkeypatch, database
):
    from bson import ObjectId
    from httpx import ASGITransport, AsyncClient

    from app.repositories.topics import TopicRepository

    await become_editor(client, authenticate, grant_roles)
    first = await create_topic(client, name="Climate")
    second = await create_topic(client, name="Emissions")
    original_write = TopicRepository._add_branch_write

    async def failing_write(self, topic_id, branch_object_id):
        if topic_id == ObjectId(second["id"]):
            raise RuntimeError("simulated database failure")
        return await original_write(self, topic_id, branch_object_id)

    monkeypatch.setattr(TopicRepository, "_add_branch_write", failing_write)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        response = await http.post(
            f"/api/v1/topics/{first['id']}/branches", json={"branch_id": second["id"]}
        )
    assert response.status_code == 500

    stored_first = await database["topics"].find_one({"_id": ObjectId(first["id"])})
    stored_second = await database["topics"].find_one({"_id": ObjectId(second["id"])})
    assert stored_first["branches"] == []
    assert stored_second.get("branches", []) == []

    # A repeated request after removing the fault still links both sides.
    monkeypatch.setattr(TopicRepository, "_add_branch_write", original_write)
    retry = await client.post(
        f"/api/v1/topics/{first['id']}/branches", json={"branch_id": second["id"]}
    )
    assert retry.status_code == 200
    assert retry.json()["branches"] == [second["id"]]


async def test_topic_branch_migration_backfills_legacy_topics(database):
    from app.database import run_migrations

    result = await database["topics"].insert_one(
        {"name": "Legacy", "slug": "legacy", "keywords": []}
    )
    await run_migrations(database)
    legacy = await database["topics"].find_one({"_id": result.inserted_id})
    assert legacy["branches"] == []

    # Idempotent: running it again does not duplicate or error.
    await run_migrations(database)
    still_legacy = await database["topics"].find_one({"_id": result.inserted_id})
    assert still_legacy["branches"] == []
