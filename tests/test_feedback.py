from tests.helpers import become_editor


async def test_submit_feedback_stores_profile_details(client, database):
    response = await client.post(
        "/api/v1/feedback", json={"message": "  Love the app  ", "subject": "Praise"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"]
    assert body["created_at"]

    stored = await database["feedback"].find_one({})
    assert stored["message"] == "Love the app"
    assert stored["subject"] == "Praise"
    assert stored["clerk_user_id"] == "user_reader_1"
    assert stored["email"] == "user_reader_1@example.com"
    assert stored["display_name"] == "Test Reader"


async def test_subject_is_optional(client, database):
    response = await client.post("/api/v1/feedback", json={"message": "No subject"})

    assert response.status_code == 201
    assert (await database["feedback"].find_one({}))["subject"] is None


async def test_feedback_validates_input(client):
    assert (await client.post("/api/v1/feedback", json={})).status_code == 422
    assert (await client.post("/api/v1/feedback", json={"message": "   "})).status_code == 422
    assert (await client.post("/api/v1/feedback", json={"message": "x" * 4001})).status_code == 422
    assert (
        await client.post("/api/v1/feedback", json={"message": "ok", "subject": "s" * 201})
    ).status_code == 422
    assert (await client.post("/api/v1/feedback", json={"message": "x" * 4000})).status_code == 201


async def test_readers_cannot_list_feedback(client):
    await client.post("/api/v1/feedback", json={"message": "Hello"})

    response = await client.get("/api/v1/feedback")

    assert response.status_code == 403


async def test_editors_list_feedback_newest_first(client, authenticate, grant_roles):
    await client.post("/api/v1/feedback", json={"message": "First"})
    await client.post("/api/v1/feedback", json={"message": "Second", "subject": "Re"})
    await become_editor(client, authenticate, grant_roles)

    response = await client.get("/api/v1/feedback")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [item["message"] for item in body["items"]] == ["Second", "First"]
    assert body["items"][0]["subject"] == "Re"
    assert body["items"][0]["email"] == "user_reader_1@example.com"
    assert body["items"][0]["display_name"] == "Test Reader"
    assert body["items"][0]["created_at"]


async def test_feedback_listing_paginates(client, authenticate, grant_roles):
    for index in range(3):
        await client.post("/api/v1/feedback", json={"message": f"Note {index}"})
    await become_editor(client, authenticate, grant_roles)

    page = await client.get("/api/v1/feedback", params={"page_size": 2, "page": 2})

    assert page.json()["total"] == 3
    assert page.json()["pages"] == 2
    assert [item["message"] for item in page.json()["items"]] == ["Note 0"]
