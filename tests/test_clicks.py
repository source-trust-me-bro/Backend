"""Click counts: unique readers and total clicks per story, shown on every list."""

from bson import ObjectId


async def _click(client, authenticate, article, *, sub: str, kind: str = "outbound"):
    authenticate(sub)
    response = await client.post(
        f"/api/v1/clicks/{article['_id']}", json={"kind": kind, "community_id": "students"}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_one_reader_clicking_twice_is_one_reader_and_two_clicks(
    client, authenticate, make_article
):
    article = await make_article(title="Permit rules change")

    await _click(client, authenticate, article, sub="user_a")
    stats = await _click(client, authenticate, article, sub="user_a")
    assert stats["outbound_users"] == 1
    assert stats["outbound_total"] == 2

    stats = await _click(client, authenticate, article, sub="user_b")
    assert stats["outbound_users"] == 2
    assert stats["outbound_total"] == 3
    assert stats["open_users"] == 0 and stats["share_total"] == 0

    read = await client.get(f"/api/v1/clicks/{article['_id']}")
    assert read.status_code == 200
    assert read.json() == stats


async def test_clicks_on_a_missing_story_or_bad_kind_are_rejected(client, make_article):
    article = await make_article()

    missing = await client.post(f"/api/v1/clicks/{ObjectId()}", json={"kind": "outbound"})
    bad_kind = await client.post(f"/api/v1/clicks/{article['_id']}", json={"kind": "like"})

    assert missing.status_code == 404
    assert bad_kind.status_code == 422


async def test_reading_a_story_counts_as_an_open(client, authenticate, make_article):
    article = await make_article()
    authenticate("user_a")
    for _ in range(2):
        assert (await client.post(f"/api/v1/library/{article['_id']}/read")).status_code == 204

    stats = (await client.get(f"/api/v1/clicks/{article['_id']}")).json()

    assert stats["open_users"] == 1
    assert stats["open_total"] == 2


async def test_lists_and_the_article_page_carry_the_counts(client, authenticate, make_article):
    clicked = await make_article(title="Clicked")
    quiet = await make_article(title="Quiet")
    await _click(client, authenticate, clicked, sub="user_a")

    listed = (await client.get("/api/v1/articles")).json()["items"]
    by_title = {item["title"]: item["clicks"] for item in listed}
    detail = (await client.get(f"/api/v1/articles/{clicked['_id']}")).json()
    trending = (await client.get("/api/v1/articles/trending")).json()

    assert by_title["Clicked"]["outbound_users"] == 1
    assert by_title["Quiet"] == {
        "open_users": 0,
        "open_total": 0,
        "outbound_users": 0,
        "outbound_total": 0,
        "share_users": 0,
        "share_total": 0,
    }
    assert detail["clicks"]["outbound_total"] == 1
    assert all(item["clicks"] is not None for item in trending)
    assert str(quiet["_id"]) in {item["id"] for item in listed}


async def test_top_lists_the_stories_most_readers_clicked(client, authenticate, make_article):
    one = await make_article(title="One reader")
    two = await make_article(title="Two readers")
    await _click(client, authenticate, one, sub="user_a")
    await _click(client, authenticate, one, sub="user_a")
    await _click(client, authenticate, two, sub="user_a")
    await _click(client, authenticate, two, sub="user_b")

    response = await client.get("/api/v1/clicks/top", params={"kind": "outbound"})

    assert response.status_code == 200
    rows = response.json()
    assert [row["article"]["title"] for row in rows] == ["Two readers", "One reader"]
    assert (rows[0]["users"], rows[0]["clicks"]) == (2, 2)
    assert (rows[1]["users"], rows[1]["clicks"]) == (1, 2)
    assert rows[0]["article"]["clicks"]["outbound_users"] == 2
