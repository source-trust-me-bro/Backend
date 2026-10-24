"""Server-to-server client for the agentic newsroom service."""

import asyncio
import hashlib
from typing import Any

import httpx

from app.config import Settings
from app.core.exceptions import AppError, NotFoundError, UpstreamError

_clients: dict[tuple[str, int], httpx.AsyncClient] = {}


def _new_client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout, limits=httpx.Limits(max_keepalive_connections=20, max_connections=100)
    )


def _shared_client(base: str, timeout: float) -> httpx.AsyncClient:
    """One connection pool per event loop, kept alive between requests. A new client for every
    call means a new connection every time."""

    key = (base, id(asyncio.get_running_loop()))
    client = _clients.get(key)
    if client is None or client.is_closed:
        client = _clients[key] = _new_client(timeout)
    return client


async def close_newsroom_clients() -> None:
    clients = list(_clients.values())
    _clients.clear()
    for client in clients:
        await client.aclose()


def user_ref(clerk_user_id: str) -> str:
    """Pseudonymous reference: the newsroom never receives account ids, names or e-mail."""

    return hashlib.sha256(f"kalevashorts:{clerk_user_id}".encode()).hexdigest()[:40]


class NewsroomClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.newsroom_url:
            raise UpstreamError(
                "The newsroom service is not configured.",
                code="newsroom_not_configured",
                status_code=503,
            )
        self._base = settings.newsroom_url.rstrip("/")
        self._key = settings.newsroom_api_key
        self._timeout = settings.newsroom_timeout_seconds

    async def request(
        self,
        method: str,
        path: str,
        *,
        ref: str | None = None,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        headers = {"X-Api-Key": self._key} if self._key else {}
        if ref:
            headers["X-User-Ref"] = ref
        try:
            response = await _shared_client(self._base, self._timeout).request(
                method, f"{self._base}{path}", headers=headers, json=json, params=params
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(
                "The newsroom service could not be reached.",
                code="newsroom_unreachable",
                status_code=503,
            ) from exc

        if response.status_code == 404:
            raise NotFoundError(_detail(response))
        if 400 <= response.status_code < 500:
            raise AppError(
                _detail(response), code="newsroom_rejected", status_code=response.status_code
            )
        if response.status_code >= 500:
            raise UpstreamError("The newsroom service failed.", status_code=502)
        return response.json()


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
        if isinstance(detail, str):
            return detail
    except ValueError:
        pass
    return f"The newsroom service returned {response.status_code}."
