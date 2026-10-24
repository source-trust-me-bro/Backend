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


# ------------------------------------------------ topics copied from the newsroom automatically


class FakeNewsroomTopics:
    """Stands in for the newsroom service: serves a topic list and counts how often it is asked."""

    def __init__(self, topics, fail=False):
        self.topics = topics
        self.fail = fail
        self.calls = 0

    def __call__(self, settings):
        return self

    async def request(self, method, path, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("newsroom is down")
        assert path == "/export/topics"
        return {"topics": self.topics}


def newsroom_topics():
    return [
        {"id": "topic_politics", "name": "Politics", "parent_id": None, "keywords": ["vote"],
         "description": "", "branches": ["topic_eu"]},
        {"id": "topic_eu", "name": "EU policy", "parent_id": "topic_politics", "keywords": [],
         "description": "", "branches": ["topic_politics"]},
        {"id": "topic_finland", "name": "Finland", "parent_id": None, "keywords": [],
         "description": "", "branches": []},
    ]


def configure(app, monkeypatch, fake):
    import time

    from app.config import Settings, get_settings
    from app.services import newsroom_sync

    monkeypatch.setattr(newsroom_sync, "NewsroomClient", fake)
    newsroom_sync._last_topic_attempt.clear()
    app.dependency_overrides[get_settings] = lambda: Settings(newsroom_url="http://newsroom.test")
    return newsroom_sync, time


async def settle():
    import asyncio

    from app.services import newsroom_sync

    for task in list(newsroom_sync._background):
        await task
    await asyncio.sleep(0)


async def names_in(client):
    tree = (await client.get("/api/v1/topics/tree")).json()
    out = []

    def walk(nodes):
        for node in nodes:
            out.append(node["name"])
            walk(node.get("children", []))

    walk(tree)
    return sorted(out), tree


async def test_the_first_read_of_an_empty_tree_fills_it_from_the_newsroom(
    client, app, monkeypatch
):
    fake = FakeNewsroomTopics(newsroom_topics())
    configure(app, monkeypatch, fake)

    names, tree = await names_in(client)

    assert names == ["EU policy", "Finland", "Politics"]
    assert fake.calls == 1
    politics = next(node for node in tree if node["name"] == "Politics")
    assert [child["name"] for child in politics["children"]] == ["EU policy"]  # the parent is kept


async def test_reads_within_a_minute_do_not_ask_the_newsroom_again(client, app, monkeypatch):
    fake = FakeNewsroomTopics(newsroom_topics())
    configure(app, monkeypatch, fake)

    await names_in(client)
    await names_in(client)
    await names_in(client)

    assert fake.calls == 1


async def test_new_newsroom_topics_appear_later_without_slowing_the_page(
    client, app, monkeypatch
):
    fake = FakeNewsroomTopics(newsroom_topics())
    sync, _ = configure(app, monkeypatch, fake)
    await names_in(client)

    fake.topics = [
        *newsroom_topics(),
        {"id": "topic_bus", "name": "Bus timetables", "parent_id": "topic_finland",
         "keywords": [], "description": "", "branches": []},
    ]
    sync._last_topic_attempt.clear()  # a minute has passed
    during, _ = await names_in(client)  # this read starts the sync in the background
    await settle()
    after, _ = await names_in(client)

    assert "Bus timetables" not in during  # the page did not wait for the newsroom
    assert "Bus timetables" in after and fake.calls == 2


async def test_a_newsroom_that_is_down_never_breaks_the_page(client, app, monkeypatch):
    fake = FakeNewsroomTopics(newsroom_topics(), fail=True)
    configure(app, monkeypatch, fake)

    response = await client.get("/api/v1/topics/tree")

    assert response.status_code == 200 and response.json() == []
    assert fake.calls == 1


async def test_without_a_newsroom_configured_the_tree_just_reads_the_database(client):
    response = await client.get("/api/v1/topics/tree")

    assert response.status_code == 200


async def test_copying_topics_twice_writes_nothing_the_second_time(database):
    from app.services.newsroom_sync import sync_topic_docs

    topics = database["topics"]
    first = await sync_topic_docs(topics, newsroom_topics())
    before = {d["newsroom_id"]: d["updated_at"] async for d in topics.find({})}
    second = await sync_topic_docs(topics, newsroom_topics())
    after = {d["newsroom_id"]: d["updated_at"] async for d in topics.find({})}

    assert first == second and len(first) == 3
    assert before == after  # untouched: no writes
    assert await topics.count_documents({}) == 3
