from tests.helpers import become_editor, create_source


async def test_reader_cannot_create_source(client):
    response = await client.post(
        "/api/v1/sources",
        json={
            "name": "Reader Source",
            "feed_url": "https://example.com/feed.xml",
            "language": "en",
        },
    )
    assert response.status_code == 403


async def test_editor_crud_source(client, authenticate, grant_roles):
    await become_editor(client, authenticate, grant_roles)

    created = await client.post(
        "/api/v1/sources",
        json={
            "name": "Helsingin Sanomat",
            "feed_url": "https://www.hs.fi/rss/",
            "homepage_url": "https://www.hs.fi",
            "language": "fi",
            "country": "FI",
            "description": "Finnish daily newspaper.",
        },
    )
    assert created.status_code == 201
    source = created.json()
    assert source["language"] == "fi"
    assert source["active"] is True
    assert source["created_at"]

    listing = await client.get("/api/v1/sources")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["name"] == "Helsingin Sanomat"

    fetched = await client.get(f"/api/v1/sources/{source['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["feed_url"] == "https://www.hs.fi/rss/"

    patched = await client.patch(
        f"/api/v1/sources/{source['id']}", json={"name": "HS", "active": False}
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "HS"
    assert patched.json()["active"] is False

    deleted = await client.delete(f"/api/v1/sources/{source['id']}")
    assert deleted.status_code == 204

    missing = await client.get(f"/api/v1/sources/{source['id']}")
    assert missing.status_code == 404


async def test_sources_can_be_filtered(client, authenticate, grant_roles):
    await become_editor(client, authenticate, grant_roles)
    await create_source(
        client, name="Finnish", feed_url="https://example.fi/feed.xml", language="fi"
    )
    await create_source(
        client, name="Swedish", feed_url="https://example.se/feed.xml", language="sv"
    )
    await create_source(
        client, name="Inactive", feed_url="https://example.com/feed.xml", active=False
    )

    finnish = await client.get("/api/v1/sources", params={"language": "fi"})
    assert finnish.json()["total"] == 1
    assert finnish.json()["items"][0]["name"] == "Finnish"

    active = await client.get("/api/v1/sources", params={"active": True})
    assert active.json()["total"] == 2

    unsupported = await client.get("/api/v1/sources", params={"language": "de"})
    assert unsupported.status_code == 400


async def test_duplicate_feed_url_conflicts(client, authenticate, grant_roles):
    await become_editor(client, authenticate, grant_roles)
    await create_source(client)
    response = await client.post(
        "/api/v1/sources",
        json={
            "name": "Duplicate",
            "feed_url": "https://example.com/feed.xml",
            "language": "en",
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "conflict"


async def test_source_validation(client, authenticate, grant_roles):
    await become_editor(client, authenticate, grant_roles)

    bad_scheme = await client.post(
        "/api/v1/sources",
        json={"name": "FTP", "feed_url": "ftp://example.com/feed", "language": "en"},
    )
    assert bad_scheme.status_code == 422

    local_feed = await client.post(
        "/api/v1/sources",
        json={"name": "Local", "feed_url": "http://127.0.0.1/feed", "language": "en"},
    )
    assert local_feed.status_code == 422

    bad_language = await client.post(
        "/api/v1/sources",
        json={"name": "German", "feed_url": "https://example.de/feed", "language": "de"},
    )
    assert bad_language.status_code == 422


async def test_source_not_found(client, authenticate, grant_roles):
    await become_editor(client, authenticate, grant_roles)
    response = await client.patch("/api/v1/sources/does-not-exist", json={"name": "Nope"})
    assert response.status_code == 404
