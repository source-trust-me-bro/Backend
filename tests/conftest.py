import os

os.environ["STARTUP_DB_CHECK"] = "false"
os.environ["CLERK_ISSUER"] = "https://test-instance.clerk.accounts.dev"
os.environ["CLERK_JWKS_URL"] = "https://test-instance.clerk.accounts.dev/.well-known/jwks.json"
os.environ["NEWS_LANGUAGES"] = "en,fi,sv"

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from app.core.security import get_current_user
from app.database import ensure_indexes, get_database
from app.main import app as application
from app.schemas.user import ClerkPrincipal

DEFAULT_SUB = "user_reader_1"


@pytest.fixture
async def database() -> AsyncIterator[Any]:
    client = AsyncMongoMockClient()
    database = client["multilingual_news_test"]
    await ensure_indexes(database)
    yield database
    client.close()


@pytest.fixture
def app(database: Any) -> AsyncIterator[FastAPI]:
    application.dependency_overrides[get_database] = lambda: database
    yield application
    application.dependency_overrides.clear()


@pytest.fixture
def authenticate(app: FastAPI) -> Callable[..., ClerkPrincipal]:
    def _authenticate(
        sub: str = DEFAULT_SUB, email: str | None = None, display_name: str = "Test Reader"
    ) -> ClerkPrincipal:
        principal = ClerkPrincipal(
            sub=sub,
            email=email or f"{sub}@example.com",
            display_name=display_name,
        )
        app.dependency_overrides[get_current_user] = lambda: principal
        return principal

    return _authenticate


@pytest.fixture
async def client(
    app: FastAPI, authenticate: Callable[..., ClerkPrincipal]
) -> AsyncIterator[AsyncClient]:
    authenticate()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
            yield http_client


@pytest.fixture
def grant_roles(database: Any, client: AsyncClient) -> Callable[..., Awaitable[None]]:
    async def _grant(roles: list[str], *, sub: str = DEFAULT_SUB) -> None:
        if await database["users"].find_one({"clerk_user_id": sub}) is None:
            await client.get("/api/v1/users/me")
        await database["users"].update_one({"clerk_user_id": sub}, {"$set": {"roles": roles}})

    return _grant


@pytest.fixture
def make_article(database: Any) -> Callable[..., Awaitable[dict[str, Any]]]:
    async def _make(**overrides: Any) -> dict[str, Any]:
        from tests.helpers import article_document

        document = article_document(**overrides)
        result = await database["articles"].insert_one(document)
        document["_id"] = result.inserted_id
        return document

    return _make
