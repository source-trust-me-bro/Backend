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
