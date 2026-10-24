"""Related news and per-community feeds, read from the newsroom's news tree."""

from bson import ObjectId

from app.services.newsroom_sync import split_story


async def test_same_event_ranks_first_and_the_same_story_is_never_related(client, make_article):
    topic = ObjectId()
    base = await make_article(
        title="Base", topic_ids=[topic], event_id="evt-1", newsroom_story_id="story-1"
    )
    await make_article(title="Same topic", topic_ids=[topic])
    await make_article(title="Same event", event_id="evt-1", source_name="Yle")
    await make_article(
        title="Same story, other edition",
        topic_ids=[topic],
        event_id="evt-1",
        newsroom_story_id="story-1",
    )
    await make_article(title="Unrelated", topic_ids=[ObjectId()])

    response = await client.get(f"/api/v1/articles/{base['_id']}/related")

    assert response.status_code == 200
    related = response.json()
    assert [item["title"] for item in related] == ["Same event", "Same topic"]
    assert "Same news from another outlet" in related[0]["reasons"]
    assert related[1]["reasons"] == ["Same topic"]
    assert all(item["clicks"] is not None for item in related)


async def test_related_favours_the_readers_community(client, make_article):
    topic = ObjectId()
    base = await make_article(title="Base", topic_ids=[topic])
    await make_article(title="Generic", topic_ids=[topic])
    await make_article(title="For students", topic_ids=[topic], community_id="students")

    response = await client.get(
        f"/api/v1/articles/{base['_id']}/related", params={"community": ["students"]}
    )

    titles = [item["title"] for item in response.json()]
    assert titles[0] == "For students"
    assert "For your community" in response.json()[0]["reasons"]


async def test_community_feed_shows_its_own_stories_then_its_topics(client, make_article):
    topic = ObjectId()
    await make_article(title="Own story", topic_ids=[topic], community_id="lapland-seasonal")
    await make_article(title="Same topic elsewhere", topic_ids=[topic])
    await make_article(title="Other group", topic_ids=[ObjectId()], community_id="students")
    await make_article(
        title="Own story in Finnish",
        language="fi",
        topic_ids=[topic],
        community_id="lapland-seasonal",
    )

    response = await client.get(
        "/api/v1/feeds/community/lapland-seasonal", params={"language": "en"}
    )

    assert response.status_code == 200
    feed = response.json()
    assert [item["title"] for item in feed] == ["Own story", "Same topic elsewhere"]
    assert feed[0]["community_id"] == "lapland-seasonal"
    assert "Written for this community" in feed[0]["reasons"]


async def test_newsroom_fields_survive_the_summary(client, make_article):
    article = await make_article(
        image_url="https://img.example.com/photo.jpg",
        basic_info="The facts.",
        explanation="Why it matters.",
        event_id="evt-9",
        community_id="students",
    )

    body = (await client.get(f"/api/v1/articles/{article['_id']}")).json()

    assert body["image_url"] == "https://img.example.com/photo.jpg"
    assert (body["basic_info"], body["explanation"]) == ("The facts.", "Why it matters.")
    assert (body["event_id"], body["community_id"]) == ("evt-9", "students")
    assert body["reasons"] == []


def test_split_story_separates_facts_from_the_explanation():
    body = "## Mitä tapahtui\nFacts here.\n\n## Mitä se tarkoittaa sinulle\nMeaning.\n- Do this"

    assert split_story(body) == ("Facts here.", "Meaning.\n- Do this")
    assert split_story("Just one paragraph.") == (None, "Just one paragraph.")
    assert split_story(None) == (None, None)


async def test_newsroom_sync_stores_sections_photo_event_and_reuses_embeddings(database):
    from app.repositories.articles import ArticleRepository
    from app.repositories.sources import SourceRepository
    from app.repositories.topics import TopicRepository
    from app.services.newsroom_sync import NewsroomSync

    story = {
        "story_id": "s1",
        "community_id": "students",
        "image_url": "https://img.example.com/s1.jpg",
        "event_ids": ["evt-1", "evt-2"],
        "sources": [{"name": "Migri", "url": "https://migri.fi/news/1", "official": True}],
        "versions": [
            {
                "language": "en",
                "headline": "Permit fees rise",
                "standfirst": "From May.",
                "body_md": "## What happened\nFees rise.\n\n## What it means for you\nApply now.",
            }
        ],
    }

    class Client:
        async def request(self, method, path, **kwargs):
            return {"topics": [], "stories": [story]}

    class Embedder:
        calls = 0

        def embed(self, text):
            Embedder.calls += 1
            return [1.0, 0.0]

    sync = NewsroomSync(
        client=Client(),
        article_repo=ArticleRepository(database),
        topic_repo=TopicRepository(database),
        source_repo=SourceRepository(database),
        embedding_service=Embedder(),
    )
    await sync.run()
    await sync.run()

    doc = await database["articles_en"].find_one({"newsroom_story_id": "s1"})
    assert Embedder.calls == 1  # the unchanged story is not embedded again
    assert (doc["basic_info"], doc["explanation"]) == ("Fees rise.", "Apply now.")
    assert (doc["image_url"], doc["event_id"]) == ("https://img.example.com/s1.jpg", "evt-1")

    story["versions"][0]["headline"] = "Permit fees rise in May"
    await sync.run()
    assert Embedder.calls == 2
