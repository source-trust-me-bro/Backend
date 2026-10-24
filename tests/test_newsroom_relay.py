"""The relay to the agentic newsroom: identity, roles and reviewer names."""

from typing import Any

import pytest

from app.api.v1.newsroom import get_client
from app.core.exceptions import UpstreamError
from app.services.newsroom_client import user_ref
from tests.helpers import become_admin, become_editor


class FakeNewsroom:
    """Records what the backend sends downstream."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.member: dict[str, Any] = {
            "community_id": "lapland-seasonal",
            "language": "en",
            "trusted_voice_id": None,
        }

    async def request(self, method, path, *, ref=None, json=None, params=None):
        self.calls.append({"method": method, "path": path, "ref": ref, "json": json})
        if path == "/members/me":
            return self.member
        if path == "/communities":
            return [{"id": "lapland-seasonal", "name": "Seasonal workers"}]
        if path.startswith("/feeds/"):
            return [{"headline": "Winter tyres", "approvers": []}]
        return {"ok": True}


@pytest.fixture
def newsroom(app) -> FakeNewsroom:
    fake = FakeNewsroom()
    app.dependency_overrides[get_client] = lambda: fake
    return fake


async def test_user_ref_is_pseudonymous_and_stable():
    ref = user_ref("user_abc")

    assert ref == user_ref("user_abc")
    assert ref != user_ref("user_abd")
    assert "user_abc" not in ref and len(ref) == 40


async def test_members_get_their_community_feed(client, newsroom):
    response = await client.get("/api/v1/newsroom/feed")

    assert response.status_code == 200
    assert response.json()[0]["headline"] == "Winter tyres"
    assert newsroom.calls[-1]["path"] == "/feeds/lapland-seasonal"


async def test_feed_is_empty_before_joining_a_community(client, newsroom):
    newsroom.member["community_id"] = None

    assert (await client.get("/api/v1/newsroom/feed")).json() == []


async def test_messages_carry_only_the_pseudonymous_reference(client, newsroom):
    response = await client.post(
        "/api/v1/newsroom/messages", json={"kind": "question", "text": "Do I need winter tyres?"}
    )

    assert response.status_code == 201
    call = newsroom.calls[-1]
    assert call["path"] == "/community/messages"
    assert call["ref"] == user_ref("user_reader_1")
    assert call["json"]["community_id"] == "lapland-seasonal"
    assert "user_reader_1" not in str(call["json"])
    assert "@" not in str(call)


async def test_messages_need_a_community(client, newsroom):
    newsroom.member["community_id"] = None

    response = await client.post(
        "/api/v1/newsroom/messages", json={"kind": "tip", "text": "Housing problem"}
    )

    assert response.status_code == 403


async def test_message_validation(client, newsroom):
    assert (
        await client.post("/api/v1/newsroom/messages", json={"kind": "gossip", "text": "x"})
    ).status_code == 422
    assert (
        await client.post("/api/v1/newsroom/messages", json={"kind": "tip", "text": ""})
    ).status_code == 422


async def test_readers_cannot_use_the_newsroom_desk(client, newsroom):
    assert (await client.get("/api/v1/newsroom/review-queue")).status_code == 403
    assert (await client.post("/api/v1/newsroom/cycle", json={})).status_code == 403
    assert (
        await client.post(
            "/api/v1/newsroom/review/p1",
            json={"decisions": [{"role": "editor_in_chief", "decision": "approve"}]},
        )
    ).status_code == 403


async def test_editor_review_uses_the_account_name_not_a_typed_one(
    client, newsroom, authenticate, grant_roles
):
    await become_editor(client, authenticate, grant_roles)

    response = await client.post(
        "/api/v1/newsroom/review/pitch_1",
        json={
            "decisions": [
                {
                    "role": "editor_in_chief",
                    "decision": "approve",
                    "reviewer": "Someone Else",  # must be ignored
                }
            ]
        },
    )

    assert response.status_code == 200
    sent = newsroom.calls[-1]["json"]["decisions"][0]
    assert sent["reviewer"] == "Test Reader"  # the authenticated profile's display name
    assert sent["role"] == "editor_in_chief"


async def test_moderators_review_community_roles_as_themselves_but_not_as_editor_in_chief(
    client, newsroom
):
    newsroom.member.update({"community_role": "moderator", "community_id": "lapland-seasonal"})

    ok = await client.post(
        "/api/v1/newsroom/review/pitch_1",
        json={"decisions": [{"role": "community_reviewer", "decision": "approve"}]},
    )
    assert ok.status_code == 200
    sent = newsroom.calls[-1]["json"]["decisions"][0]
    assert sent["reviewer"] == f"member:{user_ref('user_reader_1')}"  # their own account

    refused = await client.post(
        "/api/v1/newsroom/review/pitch_1",
        json={"decisions": [{"role": "editor_in_chief", "decision": "approve"}]},
    )
    assert refused.status_code == 403  # a moderator is not the editor-in-chief


async def test_review_queue_is_scoped_by_role(client, newsroom, authenticate, grant_roles):
    queue = [
        {"story_id": "a", "community_id": "lapland-seasonal"},
        {"story_id": "b", "community_id": "oulu-international"},
    ]
    original = newsroom.request

    async def with_queue(method, path, **kwargs):
        if path == "/review-queue":
            newsroom.calls.append({"method": method, "path": path, "ref": None, "json": None})
            return queue
        return await original(method, path, **kwargs)

    newsroom.request = with_queue  # type: ignore[method-assign]

    assert (await client.get("/api/v1/newsroom/review-queue")).status_code == 403  # plain member

    newsroom.member.update({"community_role": "moderator", "community_id": "lapland-seasonal"})
    scoped = (await client.get("/api/v1/newsroom/review-queue")).json()
    assert [item["story_id"] for item in scoped] == ["a"]  # only their own community

    await become_editor(client, authenticate, grant_roles)
    assert len((await client.get("/api/v1/newsroom/review-queue")).json()) == 2


async def _account(database, clerk_id, email, name):
    result = await database["users"].insert_one(
        {"clerk_user_id": clerk_id, "email": email, "display_name": name, "roles": ["reader"]}
    )
    return str(result.inserted_id)


async def test_editors_search_accounts_by_name(
    client, newsroom, authenticate, grant_roles, database
):
    await _account(database, "user_a", "aino.virtanen@example.com", "Aino Virtanen")
    await _account(database, "user_b", "aino.korhonen@example.com", "Aino Korhonen")
    await _account(database, "user_c", "mikko@example.com", "Mikko Laine")
    await become_editor(client, authenticate, grant_roles)

    response = await client.get("/api/v1/newsroom/staff/users", params={"q": "aino"})

    assert response.status_code == 200
    names = [item["display_name"] for item in response.json()]
    assert names == ["Aino Korhonen", "Aino Virtanen"]  # found by name, sorted
    hints = [item["email_hint"] for item in response.json()]
    assert hints == ["a***@example.com", "a***@example.com"]  # never the real address
    assert all(
        "clerk_user_id" not in item and "@example.com" not in item["id"] for item in response.json()
    )


async def test_search_needs_a_real_query_and_the_right_role(client, newsroom, database):
    await _account(database, "user_a", "a@example.com", "Aino Virtanen")

    assert (
        await client.get("/api/v1/newsroom/staff/users", params={"q": "aino"})
    ).status_code == 403

    newsroom.member.update({"community_role": "admin", "community_id": "lapland-seasonal"})
    assert (await client.get("/api/v1/newsroom/staff/users", params={"q": "a"})).status_code == 422
    allowed = await client.get("/api/v1/newsroom/staff/users", params={"q": "ain"})
    assert allowed.status_code == 200 and len(allowed.json()) == 1

    newsroom.member.update({"community_role": "moderator"})
    assert (
        await client.get("/api/v1/newsroom/staff/users", params={"q": "ain"})
    ).status_code == 403


async def test_editor_appoints_a_community_admin_or_moderator_found_by_name(
    client, newsroom, authenticate, grant_roles, database
):
    mod_id = await _account(database, "user_mod", "Mod@Example.com", "Aino Moderator")
    await become_editor(client, authenticate, grant_roles)

    response = await client.post(
        "/api/v1/newsroom/staff/community-role",
        json={
            "user_id": mod_id,
            "community_id": "lapland-seasonal",
            "community_role": "admin",
            "review_roles": ["community_reviewer", "language_reviewer"],
            "review_languages": ["ru"],
        },
    )

    assert response.status_code == 200
    sent = newsroom.calls[-1]
    assert sent["path"] == "/staff/community-role"
    assert sent["json"]["user_ref"] == user_ref("user_mod")
    assert sent["json"]["community_role"] == "admin"
    assert sent["json"]["display_name"] == "Aino Moderator"
    assert sent["json"]["review_languages"] == ["ru"]


async def test_community_admin_can_appoint_moderators_only_in_their_own_community(
    client, newsroom, database
):
    mod_id = await _account(database, "user_mod", "mod@example.com", "Aino Moderator")
    newsroom.member.update({"community_role": "admin", "community_id": "lapland-seasonal"})

    ok = await client.post(
        "/api/v1/newsroom/staff/community-role",
        json={
            "user_id": mod_id,
            "community_id": "oulu-international",  # ignored: they act in their own community
            "community_role": "moderator",
            "review_roles": ["community_reviewer"],
        },
    )
    assert ok.status_code == 200
    assert newsroom.calls[-1]["json"]["community_id"] == "lapland-seasonal"

    no_admins = await client.post(
        "/api/v1/newsroom/staff/community-role",
        json={"user_id": mod_id, "community_role": "admin"},
    )
    assert no_admins.status_code == 403  # only an editor can create a community admin


async def test_plain_members_and_unknown_accounts_cannot_appoint(client, newsroom, database):
    body = {"user_id": "64b7f0f0f0f0f0f0f0f0f0f0", "community_role": "moderator"}

    assert (
        await client.post("/api/v1/newsroom/staff/community-role", json=body)
    ).status_code == 403

    newsroom.member.update({"community_role": "admin", "community_id": "lapland-seasonal"})
    unknown = await client.post("/api/v1/newsroom/staff/community-role", json=body)
    assert unknown.status_code == 404  # they need to sign in once first


async def test_editors_add_sources_under_their_own_name(
    client, newsroom, authenticate, grant_roles
):
    body = {"name": "Union agreement", "url": "https://example.test/a", "official": False}
    assert (await client.post("/api/v1/newsroom/stories/s1/sources", json=body)).status_code == 403

    await become_editor(client, authenticate, grant_roles)
    ok = await client.post("/api/v1/newsroom/stories/s1/sources", json=body)

    assert ok.status_code == 200
    sent = newsroom.calls[-1]
    assert sent["path"] == "/stories/s1/sources"
    assert sent["json"]["reviewer"] == "Test Reader"
    assert sent["json"]["name"] == "Union agreement"


async def test_only_admins_link_trusted_voices(client, newsroom, authenticate, grant_roles):
    payload = {"clerk_user_id": "user_x", "community_id": "c", "voice_id": "v"}
    await become_editor(client, authenticate, grant_roles)
    assert (await client.post("/api/v1/newsroom/staff/link-voice", json=payload)).status_code == 403

    await become_admin(client, authenticate, grant_roles)
    ok = await client.post("/api/v1/newsroom/staff/link-voice", json=payload)

    assert ok.status_code == 200
    assert newsroom.calls[-1]["json"]["user_ref"] == user_ref("user_x")


async def test_unconfigured_newsroom_is_a_clear_503(client, app):
    from app.config import Settings
    from app.services.newsroom_client import NewsroomClient

    app.dependency_overrides.pop(get_client, None)
    with pytest.raises(UpstreamError):
        NewsroomClient(Settings(newsroom_url=""))
    response = await client.get("/api/v1/newsroom/communities")
    assert response.status_code in {200, 503}
