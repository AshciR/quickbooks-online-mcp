import os

# Provide stub env vars before Settings is constructed anywhere.
os.environ.setdefault("INTUIT_CLIENT_ID", "test-client")
os.environ.setdefault("INTUIT_CLIENT_SECRET", "test-secret")
os.environ.setdefault("QBO_REALM_ID", "9999")
os.environ.setdefault("QBO_ENVIRONMENT", "sandbox")
os.environ.setdefault("UPSTASH_REDIS_REST_URL", "https://upstash.test")
os.environ.setdefault("UPSTASH_REDIS_REST_TOKEN", "upstash-token")
os.environ.setdefault("MCP_BEARER_TOKEN", "mcp-token")

import time  # noqa: E402
from typing import Any, AsyncGenerator  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from httpx import AsyncClient  # noqa: E402

from qbo_mcp.config import Settings  # noqa: E402
from qbo_mcp.qbo_client import QBOClient  # noqa: E402
from qbo_mcp.token_store import TokenBundle, TokenStore  # noqa: E402


@pytest.fixture
async def http() -> AsyncGenerator[AsyncClient, Any]:
    """A bare httpx.AsyncClient for tests that build a QBOClient by hand."""
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture
def qbo_client(http: AsyncClient) -> QBOClient:
    """A QBOClient backed by an in-memory store holding a fresh (non-expiring) token.

    The seam the per-domain service tests construct their service over (e.g.
    `CustomerService(qbo_client)`); the fresh token means no refresh fires, so the
    only outbound calls are the QBO requests the test mocks with pytest-httpx.
    """
    settings = _settings()
    return QBOClient(settings, _store(http), http)


# --- helpers ---------------------------------------------------------------


class InMemoryTokenStore(TokenStore):
    """A TokenStore that keeps the bundle in memory, so service tests never hit Upstash."""

    def __init__(self, bundle: TokenBundle | None, settings: Settings, http: httpx.AsyncClient) -> None:
        super().__init__(settings, http)
        self.bundle = bundle

    async def load(self) -> TokenBundle | None:
        return self.bundle

    async def save(self, bundle: TokenBundle) -> None:
        self.bundle = bundle


def _settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def _store(http: httpx.AsyncClient) -> InMemoryTokenStore:
    bundle = TokenBundle(
        access_token="access-1",
        refresh_token="refresh-1",
        access_expires_at=int(time.time()) + 3600,
    )
    return InMemoryTokenStore(bundle, _settings(), http)
