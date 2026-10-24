"""Articles are stored one collection per language, so a reader can fetch just one language."""

from datetime import UTC, datetime, timedelta

from bson import ObjectId

from app.repositories.articles import (
    ArticleRepository,
    collection_name_for,
    language_code,
    migrate_legacy_articles,
)
from tests.helpers import article_document

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def at(minutes: int) -> datetime:
    return NOW - timedelta(minutes=minutes)


async def names(database) -> list[str]:
    return sorted(n for n in await database.list_collection_names() if n.startswith("articles"))


async def test_each_language_gets_its_own_collection(database):
    repo = ArticleRepository(database)
    for language in ("en", "ar", "fi", "se"):
        await repo.upsert(article_document(language=language, url=f"https://x.test/{language}"))

    assert await names(database) == ["articles_ar", "articles_en", "articles_fi", "articles_se"]
    for language in ("en", "ar", "fi", "se"):
        collection = database[f"articles_{language}"]
        assert await collection.count_documents({}) == 1
        assert (await collection.find_one({}))["language"] == language


async def test_the_same_link_can_exist_once_per_language(database):
    repo = ArticleRepository(database)

    _, created_en = await repo.upsert(article_document(language="en", url="https://x.test/same"))
    _, created_fi = await repo.upsert(article_document(language="fi", url="https://x.test/same"))
    _, created_again = await repo.upsert(
        article_document(language="en", url="https://x.test/same", title="Edited")
    )

    assert created_en and created_fi and not created_again
    assert await database["articles_en"].count_documents({}) == 1
    assert (await database["articles_en"].find_one({}))["title"] == "Edited"


async def test_asking_for_english_reads_only_the_english_collection(client, make_article):
    await make_article(language="en", title="English one")
    await make_article(language="ar", title="Arabic one")
    await make_article(language="se", title="Sámi one")

    english = (await client.get("/api/v1/articles", params={"language": "en"})).json()
    arabic = (await client.get("/api/v1/articles", params={"language": "ar"})).json()

    assert [a["title"] for a in english["items"]] == ["English one"]
    assert [a["title"] for a in arabic["items"]] == ["Arabic one"]
    assert english["total"] == 1


async def test_a_language_with_no_stories_is_empty_not_an_error(client, make_article):
    await make_article(language="en")

    response = await client.get("/api/v1/articles", params={"language": "uk"})

    assert response.status_code == 200
    assert response.json()["items"] == [] and response.json()["total"] == 0


async def test_without_a_language_all_collections_are_merged_newest_first_and_paged(
    client, make_article
):
    await make_article(language="en", title="en-1", published_at=at(1))
    await make_article(language="ar", title="ar-2", published_at=at(2))
    await make_article(language="fi", title="fi-3", published_at=at(3))
    await make_article(language="en", title="en-4", published_at=at(4))
    await make_article(language="se", title="se-5", published_at=at(5))

    everything = (await client.get("/api/v1/articles", params={"page_size": 10})).json()
    second_page = (await client.get("/api/v1/articles", params={"page": 2, "page_size": 2})).json()

    assert everything["total"] == 5
    assert [a["title"] for a in everything["items"]] == ["en-1", "ar-2", "fi-3", "en-4", "se-5"]
    assert [a["title"] for a in second_page["items"]] == ["fi-3", "en-4"]
    params = {"sort": "oldest", "page_size": 2}
    oldest = (await client.get("/api/v1/articles", params=params)).json()
    assert [a["title"] for a in oldest["items"]] == ["se-5", "en-4"]


async def test_an_article_is_found_by_id_whatever_its_language(client, make_article):
    arabic = await make_article(language="ar", title="Arabic one")
    english = await make_article(language="en", title="English one")

    got_ar = await client.get(f"/api/v1/articles/{arabic['_id']}")
    got_en = await client.get(f"/api/v1/articles/{english['_id']}")
    missing = await client.get(f"/api/v1/articles/{ObjectId()}")

    assert got_ar.json()["title"] == "Arabic one" and got_ar.json()["language"] == "ar"
    assert got_en.json()["title"] == "English one"
    assert missing.status_code == 404


async def test_related_stories_come_from_the_same_language(client, make_article, database):
    topic = ObjectId()
    base = await make_article(language="en", title="Base", topic_ids=[topic])
    await make_article(language="en", title="Same language", topic_ids=[topic])
    await make_article(language="fi", title="Other language", topic_ids=[topic])

    related = (await client.get(f"/api/v1/articles/{base['_id']}/related")).json()

    assert [a["title"] for a in related] == ["Same language"]


async def test_saved_items_still_work_across_language_collections(client, make_article):
    en = await make_article(language="en", title="English one")
    ar = await make_article(language="ar", title="Arabic one")
    for article in (en, ar):
        await client.put(f"/api/v1/library/{article['_id']}/saved", json={"value": True})

    saved = (await client.get("/api/v1/library/saved")).json()

    assert {a["title"] for a in saved["items"]} == {"English one", "Arabic one"}


async def test_language_codes_are_made_safe_for_collection_names():
    assert collection_name_for("EN") == "articles_en"
    assert collection_name_for("zh-Hant") == "articles_zh-hant"
    assert collection_name_for(None) == "articles_und"
    assert language_code("../admin") == "und"
    assert language_code("$where") == "und"
    assert collection_name_for("english-language-long") == "articles_und"


async def test_the_old_single_collection_is_split_by_language_keeping_ids(database):
    legacy = database["articles"]
    docs = [
        article_document(language="en", url="https://x.test/1"),
        article_document(language="ar", url="https://x.test/2"),
        article_document(language="en", url="https://x.test/3"),
        article_document(language="??", url="https://x.test/4"),
    ]
    ids = [(await legacy.insert_one(d)).inserted_id for d in docs]

    moved = await migrate_legacy_articles(database)

    assert moved == {"en": 2, "ar": 1, "und": 1}
    assert "articles" not in await database.list_collection_names()  # emptied, so dropped
    assert await database["articles_en"].count_documents({"_id": {"$in": [ids[0], ids[2]]}}) == 2
    assert (await database["articles_ar"].find_one({"_id": ids[1]}))["url"] == "https://x.test/2"
    assert await migrate_legacy_articles(database) == {}  # running it again does nothing


async def test_migration_does_not_duplicate_what_was_already_moved(database):
    repo = ArticleRepository(database)
    existing, _ = await repo.upsert(article_document(language="en", url="https://x.test/1"))
    legacy_copy = article_document(language="en", url="https://x.test/1")
    await database["articles"].insert_one(legacy_copy)

    await database["user_articles"].insert_one(
        {"clerk_user_id": "u1", "article_id": legacy_copy["_id"], "saved": True}
    )

    await migrate_legacy_articles(database)

    assert await database["articles_en"].count_documents({}) == 1
    assert (await database["articles_en"].find_one({}))["_id"] == existing["_id"]
    assert "articles" not in await database.list_collection_names()
    saved = await database["user_articles"].find_one({"clerk_user_id": "u1"})
    assert saved["article_id"] == existing["_id"]  # saved item follows the surviving article


async def test_the_language_list_shows_every_supported_language_with_its_story_count(
    client, make_article
):
    await make_article(language="en")
    await make_article(language="en")
    await make_article(language="ar")

    response = await client.get("/api/v1/languages")

    assert response.status_code == 200
    languages = {item["code"]: item for item in response.json()}
    assert set(languages) == {"en", "fi", "sv", "ar", "se", "uk", "ru"}  # all supported, even empty
    assert languages["en"]["articles"] == 2 and languages["ar"]["articles"] == 1
    assert languages["fi"]["articles"] == 0
    assert languages["ar"]["rtl"] is True and languages["en"]["rtl"] is False
    assert languages["ar"]["label"] == "العربية" and languages["se"]["label"] == "Davvisámegiella"
