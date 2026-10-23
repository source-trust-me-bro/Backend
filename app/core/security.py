"""Clerk JWT verification for the API acting as an OAuth 2.0 resource server."""

import asyncio
import logging
from functools import lru_cache
from typing import Any

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError, PyJWKClient, PyJWKClientError

from app.config import Settings, get_settings
from app.core.exceptions import UnauthorizedError
from app.schemas.user import ClerkPrincipal

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="ClerkBearer",
    description="Clerk-issued RS256 session token.",
)

_UNAUTHORIZED_HEADERS = {"WWW-Authenticate": 'Bearer error="invalid_token"'}


@lru_cache(maxsize=8)
def get_jwks_client(jwks_url: str, cache_seconds: int = 300, timeout: float = 10.0) -> PyJWKClient:
    return PyJWKClient(
        jwks_url,
        cache_keys=True,
        lifespan=cache_seconds,
        timeout=timeout,
    )


def principal_from_claims(claims: dict[str, Any]) -> ClerkPrincipal:
    sub = claims.get("sub")
    if not sub:
        raise UnauthorizedError(
            "Clerk token is missing the 'sub' claim.", headers=_UNAUTHORIZED_HEADERS
        )

    first_name = claims.get("first_name") or claims.get("given_name")
    last_name = claims.get("last_name") or claims.get("family_name")
    full_name = claims.get("full_name") or claims.get("name")
    if not full_name:
        full_name = " ".join(part for part in (first_name, last_name) if part) or None

    return ClerkPrincipal(
        sub=str(sub),
        email=claims.get("email"),
        display_name=full_name,
        username=claims.get("username"),
        image_url=claims.get("image_url") or claims.get("picture"),
    )


def verify_clerk_token(token: str, settings: Settings) -> ClerkPrincipal:
    if not settings.jwks_url:
        raise UnauthorizedError(
            "Clerk authentication is not configured on this server.",
            code="auth_not_configured",
            status_code=503,
        )

    try:
        header = jwt.get_unverified_header(token)
    except InvalidTokenError as exc:
        raise UnauthorizedError("Malformed bearer token.", headers=_UNAUTHORIZED_HEADERS) from exc

    token_type = str(header.get("typ") or "JWT").upper()
    if header.get("alg") != "RS256" or token_type != "JWT":
        raise UnauthorizedError(
            "Only RS256-signed Clerk session tokens are accepted.", headers=_UNAUTHORIZED_HEADERS
        )

    try:
        client = get_jwks_client(
            settings.jwks_url,
            settings.clerk_jwks_cache_seconds,
            settings.http_timeout_seconds,
        )
        signing_key = client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=settings.clerk_issuer or None,
            options={
                "verify_aud": False,
                "require": ["exp", "iat", "sub", "iss"],
            },
            leeway=30,
        )
    except (PyJWKClientError, InvalidTokenError) as exc:
        logger.info("Rejected Clerk token: %s", exc)
        raise UnauthorizedError(
            "Invalid or expired Clerk token.", headers=_UNAUTHORIZED_HEADERS
        ) from exc
    except Exception as exc:
        logger.warning("Unexpected Clerk token verification failure", exc_info=True)
        raise UnauthorizedError(
            "Clerk token could not be verified.", headers=_UNAUTHORIZED_HEADERS
        ) from exc

    return principal_from_claims(claims)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> ClerkPrincipal:
    """Validate the bearer token against Clerk's JWKS and return the caller."""

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError("Missing bearer token.", headers=_UNAUTHORIZED_HEADERS)
    return await asyncio.to_thread(verify_clerk_token, credentials.credentials, settings)
