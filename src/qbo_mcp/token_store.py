from __future__ import annotations

from urllib.parse import quote

import httpx
from pydantic import BaseModel

from .config import Settings

TOKEN_KEY = "qbo:tokens"


class TokenStore:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http

    async def load(self) -> TokenBundle | None:
        resp = await self._http.get(f"{self._base}/get/{TOKEN_KEY}", headers=self._headers)
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result")
        if result is None:
            return None
        return TokenBundle.model_validate_json(result)

    async def save(self, bundle: TokenBundle) -> None:
        value = quote(bundle.model_dump_json(), safe="")
        resp = await self._http.post(
            f"{self._base}/set/{TOKEN_KEY}/{value}",
            headers=self._headers,
        )
        resp.raise_for_status()

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._settings.upstash_redis_rest_token}"}

    @property
    def _base(self) -> str:
        return self._settings.upstash_redis_rest_url.rstrip("/")


# --- helpers ---------------------------------------------------------------


class TokenBundle(BaseModel):
    access_token: str
    refresh_token: str
    access_expires_at: int
