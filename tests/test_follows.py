from bson import ObjectId

from tests.helpers import become_editor, create_source


async def _two_sources(client, authenticate, grant_roles):
    await become_editor(client, authenticate, grant_roles)
    kaleva = await create_source(client, name="Kaleva", feed_url="https://kaleva.example/feed.xml")
    yle = await create_source(client, name="Yle", feed_url="https://yle.example/feed.xml")
    authenticate("user_reader_1")
    return kaleva, yle


async def test_follow_and_unfollow_source(client, authenticate, grant_roles):
    kaleva, _yle = await _two_sources(client, authenticate, grant_roles)

    followed = await client.put(f"/api/v1/follows/sources/{kaleva['id']}", json={"value": True})
    assert followed.status_code == 200
    assert followed.json() == {"following": True}

    listing = await client.get("/api/v1/follows/sources")
    assert [item["id"] for item in listing.json()] == [kaleva["id"]]

    profile = await client.get("/api/v1/users/me")
    assert profile.json()["followed_source_ids"] == [kaleva["id"]]

    unfollowed = await client.put(f"/api/v1/follows/sources/{kaleva['id']}", json={"value": False})
    assert unfollowed.json() == {"following": False}
    assert (await client.get("/api/v1/follows/sources")).json() == []


async def test_following_twice_does_not_duplicate(client, authenticate, grant_roles):
    kaleva, _yle = await _two_sources(client, authenticate, grant_roles)

    for _ in range(2):
        await client.put(f"/api/v1/follows/sources/{kaleva['id']}", json={"value": True})

    profile = await client.get("/api/v1/users/me")
    assert profile.json()["followed_source_ids"] == [kaleva["id"]]


async def test_followed_sources_are_sorted_by_name(client, authenticate, grant_roles):
    kaleva, yle = await _two_sources(client, authenticate, grant_roles)

    await client.put(f"/api/v1/follows/sources/{yle['id']}", json={"value": True})
    await client.put(f"/api/v1/follows/sources/{kaleva['id']}", json={"value": True})

    listing = await client.get("/api/v1/follows/sources")
    assert [item["name"] for item in listing.json()] == ["Kaleva", "Yle"]


async def test_follow_unknown_source_is_404(client):
    missing = await client.put(f"/api/v1/follows/sources/{ObjectId()}", json={"value": True})
    invalid = await client.put("/api/v1/follows/sources/not-an-id", json={"value": True})

    assert missing.status_code == 404
    assert invalid.status_code == 404


async def test_deleted_sources_drop_out_of_the_followed_list(client, authenticate, grant_roles):
    kaleva, _yle = await _two_sources(client, authenticate, grant_roles)
    await client.put(f"/api/v1/follows/sources/{kaleva['id']}", json={"value": True})

    await become_editor(client, authenticate, grant_roles)
    await client.delete(f"/api/v1/sources/{kaleva['id']}")
    authenticate("user_reader_1")

    assert (await client.get("/api/v1/follows/sources")).json() == []


async def test_following_feed_only_returns_followed_sources(
    client, authenticate, grant_roles, make_article
):
    kaleva, yle = await _two_sources(client, authenticate, grant_roles)
    mine = await make_article(title="From Kaleva", source_id=ObjectId(kaleva["id"]))
    await make_article(title="From Yle", source_id=ObjectId(yle["id"]))
    await make_article(title="No source")

    await client.put(f"/api/v1/follows/sources/{kaleva['id']}", json={"value": True})
    feed = await client.get("/api/v1/feeds/following")

    assert feed.status_code == 200
    assert feed.json()["total"] == 1
    assert feed.json()["items"][0]["id"] == str(mine["_id"])


async def test_following_feed_is_empty_without_follows(client, make_article):
    await make_article(title="Anything")

    feed = await client.get("/api/v1/feeds/following")

    assert feed.status_code == 200
    assert feed.json()["total"] == 0
    assert feed.json()["items"] == []


async def test_following_feed_is_newest_first_and_paginated(
    client, authenticate, grant_roles, make_article
):
    from datetime import timedelta

    from app.core.utils import utcnow

    kaleva, _yle = await _two_sources(client, authenticate, grant_roles)
    source_id = ObjectId(kaleva["id"])
    now = utcnow()
    await make_article(title="Oldest", source_id=source_id, published_at=now - timedelta(days=2))
    newest = await make_article(title="Newest", source_id=source_id, published_at=now)
    await client.put(f"/api/v1/follows/sources/{kaleva['id']}", json={"value": True})

    feed = await client.get("/api/v1/feeds/following", params={"page_size": 1})

    assert feed.json()["total"] == 2
    assert feed.json()["pages"] == 2
    assert feed.json()["items"][0]["id"] == str(newest["_id"])
