"""Server-to-server client for the agentic newsroom service."""

import hashlib
from typing import Any

import httpx

from app.config import Settings
from app.core.exceptions import AppError, NotFoundError, UpstreamError


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
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
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
