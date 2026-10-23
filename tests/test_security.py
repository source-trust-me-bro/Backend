from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import Settings
from app.core import security
from app.core.exceptions import UnauthorizedError
from app.core.security import get_current_user

PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_KEY = PRIVATE_KEY.public_key()


class StubJWKClient:
    def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
        return SimpleNamespace(key=PUBLIC_KEY)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        clerk_issuer="https://issuer.test",
        clerk_jwks_url="https://issuer.test/.well-known/jwks.json",
    )


@pytest.fixture
def stub_jwks(monkeypatch):
    monkeypatch.setattr(security, "get_jwks_client", lambda *args, **kwargs: StubJWKClient())


def build_token(settings: Settings, **claims) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": "user_123",
        "iss": settings.clerk_issuer,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "email": "reader@example.com",
        "name": "Test Reader",
    }
    payload.update(claims)
    return jwt.encode(payload, PRIVATE_KEY, algorithm="RS256")


def test_verifies_valid_clerk_token(settings, stub_jwks):
    principal = security.verify_clerk_token(build_token(settings), settings)
    assert principal.sub == "user_123"
    assert principal.email == "reader@example.com"
    assert principal.display_name == "Test Reader"


def test_rejects_expired_token(settings, stub_jwks):
    expired = build_token(settings, exp=datetime.now(UTC) - timedelta(minutes=5))
    with pytest.raises(UnauthorizedError):
        security.verify_clerk_token(expired, settings)


def test_rejects_wrong_issuer(settings, stub_jwks):
    forged = build_token(settings, iss="https://attacker.test")
    with pytest.raises(UnauthorizedError):
        security.verify_clerk_token(forged, settings)


def test_rejects_non_rs256_algorithm(settings):
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "user_123",
            "iss": settings.clerk_issuer,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        "shared-secret-key-that-is-long-enough-for-hs256",
        algorithm="HS256",
    )
    with pytest.raises(UnauthorizedError):
        security.verify_clerk_token(token, settings)


def test_rejects_missing_sub_claim(settings, stub_jwks):
    token_without_sub = jwt.encode(
        {
            "iss": settings.clerk_issuer,
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        PRIVATE_KEY,
        algorithm="RS256",
    )
    with pytest.raises(UnauthorizedError):
        security.verify_clerk_token(token_without_sub, settings)


async def test_api_rejects_missing_bearer_token(app, client):
    app.dependency_overrides.pop(get_current_user, None)
    response = await client.get("/api/v1/users/me")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


async def test_api_rejects_malformed_token(app, client):
    app.dependency_overrides.pop(get_current_user, None)
    response = await client.get(
        "/api/v1/sources", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"
