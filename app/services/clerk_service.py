"""Looks up Clerk user details that the default session token does not carry."""

import logging
import time
from typing import Any

import httpx

from app.config import Settings
from app.schemas.user import ClerkPrincipal

logger = logging.getLogger(__name__)

CLERK_API_BASE = "https://api.clerk.com/v1"
_CACHE_TTL_SECONDS = 600
_FAILURE_TTL_SECONDS = 60

# user id -> (expires_at, principal or None when the lookup failed recently)
_cache: dict[str, tuple[float, ClerkPrincipal | None]] = {}


def principal_from_user(user: dict[str, Any]) -> ClerkPrincipal:
    """Map a Clerk Backend API user object to a principal."""

    primary_id = user.get("primary_email_address_id")
    addresses = user.get("email_addresses") or []
    email = next(
        (item.get("email_address") for item in addresses if item.get("id") == primary_id),
        None,
    )
    if email is None and addresses:
        email = addresses[0].get("email_address")

    full_name = " ".join(part for part in (user.get("first_name"), user.get("last_name")) if part)
    return ClerkPrincipal(
        sub=str(user["id"]),
        email=email,
        display_name=full_name or user.get("username"),
        username=user.get("username"),
        image_url=user.get("image_url"),
    )


async def fetch_clerk_user(user_id: str, settings: Settings) -> ClerkPrincipal | None:
    """Fetch the user from Clerk, caching results so requests stay cheap."""

    if not settings.clerk_secret_key:
        return None

    now = time.monotonic()
    cached = _cache.get(user_id)
    if cached and cached[0] > now:
        return cached[1]

    try:
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            response = await client.get(
                f"{CLERK_API_BASE}/users/{user_id}",
                headers={"Authorization": f"Bearer {settings.clerk_secret_key}"},
            )
        response.raise_for_status()
        principal = principal_from_user(response.json())
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.warning("Could not load Clerk user %s: %s", user_id, exc)
        _cache[user_id] = (now + _FAILURE_TTL_SECONDS, None)
        return None

    _cache[user_id] = (now + _CACHE_TTL_SECONDS, principal)
    return principal
