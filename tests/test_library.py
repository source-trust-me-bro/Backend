from datetime import timedelta

from bson import ObjectId

from app.core.utils import utcnow


async def test_library_state_starts_empty(client):
    response = await client.get("/api/v1/library/state")

    assert response.status_code == 200
    assert response.json() == {"saved": [], "downloaded": []}


async def test_save_and_unsave_article(client, make_article):
    article = await make_article(title="Keep me")
    article_id = str(article["_id"])

    saved = await client.put(f"/api/v1/library/{article_id}/saved", json={"value": True})
    assert saved.status_code == 200
    assert saved.json() == {"saved": True}

    state = await client.get("/api/v1/library/state")
    assert state.json() == {"saved": [article_id], "downloaded": []}

    listing = await client.get("/api/v1/library/saved")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == article_id

    removed = await client.put(f"/api/v1/library/{article_id}/saved", json={"value": False})
    assert removed.json() == {"saved": False}
    assert (await client.get("/api/v1/library/saved")).json()["total"] == 0


async def test_saved_is_ordered_by_most_recently_saved(client, make_article):
    first = str((await make_article(title="First"))["_id"])
    second = str((await make_article(title="Second"))["_id"])

    await client.put(f"/api/v1/library/{first}/saved", json={"value": True})
    await client.put(f"/api/v1/library/{second}/saved", json={"value": True})

    listing = await client.get("/api/v1/library/saved")

    assert [item["id"] for item in listing.json()["items"]] == [second, first]


async def test_saved_listing_paginates_and_skips_deleted_articles(client, database, make_article):
    articles = [await make_article(title=f"Story {index}") for index in range(3)]
    for article in articles:
        await client.put(f"/api/v1/library/{article['_id']}/saved", json={"value": True})
    await database["articles"].delete_one({"_id": articles[0]["_id"]})

    first_page = await client.get("/api/v1/library/saved", params={"page_size": 1})
    second_page = await client.get("/api/v1/library/saved", params={"page_size": 1, "page": 2})

    assert first_page.json()["total"] == 2
    assert first_page.json()["pages"] == 2
    assert first_page.json()["items"][0]["id"] == str(articles[2]["_id"])
    assert second_page.json()["items"][0]["id"] == str(articles[1]["_id"])


async def test_download_flag_and_clear(client, make_article):
    article_id = str((await make_article())["_id"])

    marked = await client.put(f"/api/v1/library/{article_id}/downloaded", json={"value": True})
    assert marked.json() == {"downloaded": True}
    assert (await client.get("/api/v1/library/state")).json()["downloaded"] == [article_id]
    assert (await client.get("/api/v1/library/downloaded")).json()["total"] == 1

    cleared = await client.delete("/api/v1/library/downloaded")
    assert cleared.status_code == 204
    assert (await client.get("/api/v1/library/downloaded")).json()["total"] == 0


async def test_clearing_downloads_keeps_saved_articles(client, make_article):
    article_id = str((await make_article())["_id"])
    await client.put(f"/api/v1/library/{article_id}/saved", json={"value": True})
    await client.put(f"/api/v1/library/{article_id}/downloaded", json={"value": True})

    await client.delete("/api/v1/library/downloaded")

    assert (await client.get("/api/v1/library/state")).json() == {
        "saved": [article_id],
        "downloaded": [],
    }


async def test_read_records_history_and_clear_removes_it(client, database, make_article):
    older = str((await make_article(title="Older"))["_id"])
    newer = str((await make_article(title="Newer"))["_id"])

    assert (await client.post(f"/api/v1/library/{older}/read")).status_code == 204
    assert (await client.post(f"/api/v1/library/{newer}/read")).status_code == 204
    await client.post(f"/api/v1/library/{older}/read")

    history = await client.get("/api/v1/library/history")
    assert [item["id"] for item in history.json()["items"]] == [older, newer]

    row = await database["user_articles"].find_one({"article_id": ObjectId(older)})
    assert row["read_count"] == 2

    cleared = await client.delete("/api/v1/library/history")
    assert cleared.status_code == 204
    assert (await client.get("/api/v1/library/history")).json()["total"] == 0


async def test_clearing_history_keeps_saved_state(client, make_article):
    article_id = str((await make_article())["_id"])
    await client.put(f"/api/v1/library/{article_id}/saved", json={"value": True})
    await client.post(f"/api/v1/library/{article_id}/read")

    await client.delete("/api/v1/library/history")

    assert (await client.get("/api/v1/library/saved")).json()["total"] == 1
    assert (await client.get("/api/v1/library/history")).json()["total"] == 0


async def test_library_is_private_to_each_user(client, authenticate, make_article):
    article_id = str((await make_article())["_id"])
    await client.put(f"/api/v1/library/{article_id}/saved", json={"value": True})
    await client.post(f"/api/v1/library/{article_id}/read")

    authenticate("user_other_1")

    assert (await client.get("/api/v1/library/state")).json() == {"saved": [], "downloaded": []}
    assert (await client.get("/api/v1/library/history")).json()["total"] == 0


async def test_library_writes_404_for_unknown_articles(client):
    missing = str(ObjectId())

    for path, method in (
        (f"/api/v1/library/{missing}/saved", "put"),
        (f"/api/v1/library/{missing}/downloaded", "put"),
    ):
        response = await getattr(client, method)(path, json={"value": True})
        assert response.status_code == 404

    assert (await client.post(f"/api/v1/library/{missing}/read")).status_code == 404
    assert (
        await client.put("/api/v1/library/not-an-id/saved", json={"value": True})
    ).status_code == 404


async def test_library_requires_a_boolean_value(client, make_article):
    article_id = str((await make_article())["_id"])

    response = await client.put(f"/api/v1/library/{article_id}/saved", json={})

    assert response.status_code == 422


async def test_library_requires_authentication(app):
    from httpx import ASGITransport, AsyncClient

    from app.core.security import get_current_user

    app.dependency_overrides.pop(get_current_user, None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://anon") as anon:
        assert (await anon.get("/api/v1/library/state")).status_code == 401
        assert (await anon.get("/api/v1/library/saved")).status_code == 401


async def test_history_orders_by_last_read(client, database, make_article):
    first = await make_article(title="First")
    second = await make_article(title="Second")
    await client.post(f"/api/v1/library/{first['_id']}/read")
    await client.post(f"/api/v1/library/{second['_id']}/read")
    await database["user_articles"].update_one(
        {"article_id": second["_id"]},
        {"$set": {"last_read_at": utcnow() - timedelta(days=1)}},
    )

    history = await client.get("/api/v1/library/history")

    assert [item["id"] for item in history.json()["items"]] == [
        str(first["_id"]),
        str(second["_id"]),
    ]
