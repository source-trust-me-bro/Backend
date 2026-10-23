from bson import ObjectId

from app.services.embedding_service import EmbeddingService
from tests.helpers import become_editor, create_topic


async def test_keyword_search_matches_all_tokens(client, make_article):
    hit = await make_article(
        title="Climate summit held in Helsinki",
        summary="Leaders discussed emissions and energy policy.",
    )
    await make_article(title="Local football results")

    response = await client.get("/api/v1/search", params={"q": "climate Helsinki"})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == str(hit["_id"])

    partial = await client.get("/api/v1/search", params={"q": "emissions"})
    assert partial.json()["total"] == 1

    no_match = await client.get("/api/v1/search", params={"q": "volcano"})
    assert no_match.json()["total"] == 0


async def test_keyword_search_language_filter(client, make_article):
    await make_article(title="Shared word story", language="fi")
    await make_article(title="Shared word story", language="en")

    response = await client.get("/api/v1/search", params={"q": "shared", "language": "fi"})
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["language"] == "fi"

    unsupported = await client.get("/api/v1/search", params={"q": "shared", "language": "de"})
    assert unsupported.status_code == 400


async def test_semantic_search_ranks_relevant_articles(client, make_article):
    embedder = EmbeddingService()
    relevant_text = "Climate change policy and emissions in Finland"
    relevant = await make_article(
        title=relevant_text,
        summary="Government updates its climate policy.",
        embedding=embedder.embed(relevant_text),
    )
    football_text = "Football league match report and player transfers"
    await make_article(
        title=football_text,
        summary="Sports coverage.",
        embedding=embedder.embed(football_text),
    )

    response = await client.post(
        "/api/v1/search/semantic",
        json={"query": "climate policy", "language": "en", "limit": 5},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert body["results"][0]["article"]["id"] == str(relevant["_id"])
    assert body["results"][0]["score"] > 0
    scores = [hit["score"] for hit in body["results"]]
    assert scores == sorted(scores, reverse=True)


async def test_semantic_search_honors_community_topics(
    client, authenticate, grant_roles, make_article
):
    await become_editor(client, authenticate, grant_roles)
    topic = await create_topic(client, name="Climate", keywords=["climate"])
    community = await client.post(
        "/api/v1/communities",
        json={
            "name": "Climate Community",
            "language": "en",
            "topic_ids": [topic["id"]],
        },
    )
    assert community.status_code == 201

    embedder = EmbeddingService()
    matching = await make_article(
        title="Climate policy update",
        topic_ids=[ObjectId(topic["id"])],
        embedding=embedder.embed("Climate policy update"),
    )
    await make_article(
        title="Climate in another community",
        embedding=embedder.embed("Climate in another community"),
    )

    response = await client.post(
        "/api/v1/search/semantic",
        json={"query": "climate", "language": "en", "community_id": community.json()["id"]},
    )
    assert response.status_code == 200
    assert [hit["article"]["id"] for hit in response.json()["results"]] == [str(matching["_id"])]


async def test_semantic_search_unknown_community(client):
    response = await client.post(
        "/api/v1/search/semantic",
        json={"query": "climate", "community_id": "64b7f5b2f1f3a2c4d5e6f7a8"},
    )
    assert response.status_code == 404
