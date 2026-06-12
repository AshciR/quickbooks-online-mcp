from __future__ import annotations

import re
import time
import uuid
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from qbo_mcp.config import Settings
from qbo_mcp.qbo_client import (
    INTUIT_TOKEN_URL,
    QBOAuthExpiredError,
    QBOClient,
    QBOFaultError,
    QBORateLimitError,
)
from qbo_mcp.token_store import TokenBundle, TokenStore


async def test_minorversion_appended_on_every_request(
    http: httpx.AsyncClient, httpx_mock: HTTPXMock
) -> None:
    # Given a client with a fresh token and a mocked QBO endpoint that only matches when minorversion=75 is present
    store = InMemoryTokenStore(_fresh_bundle(), _settings(), http)
    client = QBOClient(_settings(), store, http)
    httpx_mock.add_response(
        url=re.compile(r".*/v3/company/9999/companyinfo/9999\?minorversion=75$"),
        json={"CompanyInfo": {"CompanyName": "Acme"}},
    )

    # When the client reads CompanyInfo
    body = await client.read("companyinfo", "9999")

    # Then the response is returned (proving the request URL carried minorversion=75)
    assert body["CompanyInfo"]["CompanyName"] == "Acme"


async def test_401_triggers_one_refresh_and_retry(
    http: httpx.AsyncClient, httpx_mock: HTTPXMock
) -> None:
    # Given a fresh token, a first call that returns 401, a successful refresh, and a retried call that succeeds
    store = InMemoryTokenStore(_fresh_bundle(), _settings(), http)
    client = QBOClient(_settings(), store, http)
    httpx_mock.add_response(
        url=_company_url() + "?minorversion=75",
        status_code=401,
        json={"fault": "expired"},
    )
    httpx_mock.add_response(
        url=INTUIT_TOKEN_URL,
        json={
            "access_token": "access-2",
            "refresh_token": "refresh-2",
            "expires_in": 3600,
        },
    )
    httpx_mock.add_response(
        url=_company_url() + "?minorversion=75",
        json={"CompanyInfo": {"CompanyName": "Acme"}},
    )

    # When the client reads CompanyInfo
    body = await client.read("companyinfo", "9999")

    # Then the retry returns the body and the rotated access token is persisted
    assert body["CompanyInfo"]["CompanyName"] == "Acme"
    assert store.bundle is not None
    assert store.bundle.access_token == "access-2"


async def test_double_401_propagates(
    http: httpx.AsyncClient, httpx_mock: HTTPXMock
) -> None:
    # Given a fresh token, two consecutive 401s from QBO, and a successful refresh in between
    store = InMemoryTokenStore(_fresh_bundle(), _settings(), http)
    client = QBOClient(_settings(), store, http)
    httpx_mock.add_response(
        url=_company_url() + "?minorversion=75", status_code=401, json={}
    )
    httpx_mock.add_response(
        url=INTUIT_TOKEN_URL,
        json={
            "access_token": "access-2",
            "refresh_token": "refresh-2",
            "expires_in": 3600,
        },
    )
    httpx_mock.add_response(
        url=_company_url() + "?minorversion=75", status_code=401, json={}
    )

    # When the client reads CompanyInfo
    # Then the second 401 is not retried and propagates as HTTPStatusError
    with pytest.raises(httpx.HTTPStatusError):
        await client.read("companyinfo", "9999")


async def test_refresh_persists_rotated_refresh_token(
    http: httpx.AsyncClient, httpx_mock: HTTPXMock
) -> None:
    # Given a fresh token and an Intuit refresh response with rotated access and refresh tokens
    store = InMemoryTokenStore(_fresh_bundle(), _settings(), http)
    client = QBOClient(_settings(), store, http)
    httpx_mock.add_response(
        url=INTUIT_TOKEN_URL,
        json={
            "access_token": "rotated-access",
            "refresh_token": "rotated-refresh",
            "expires_in": 3600,
        },
    )

    # When the client refreshes
    await client._refresh(store.bundle)  # type: ignore[arg-type]

    # Then the rotated tokens are saved exactly once
    assert store.bundle is not None
    assert store.bundle.refresh_token == "rotated-refresh"
    assert store.bundle.access_token == "rotated-access"
    assert len(store.saves) == 1


async def test_refresh_invalid_grant_raises_auth_expired(
    http: httpx.AsyncClient, httpx_mock: HTTPXMock
) -> None:
    # Given an Intuit refresh response with error=invalid_grant
    store = InMemoryTokenStore(_fresh_bundle(), _settings(), http)
    client = QBOClient(_settings(), store, http)
    httpx_mock.add_response(
        url=INTUIT_TOKEN_URL,
        status_code=400,
        json={"error": "invalid_grant"},
    )

    # When the client refreshes
    # Then it raises QBOAuthExpiredError instructing the user to re-run bootstrap
    with pytest.raises(QBOAuthExpiredError):
        await client._refresh(store.bundle)  # type: ignore[arg-type]


async def test_missing_tokens_raises_auth_expired(
    http: httpx.AsyncClient,
) -> None:
    # Given a token store with no bundle persisted
    store = InMemoryTokenStore(None, _settings(), http)
    client = QBOClient(_settings(), store, http)

    # When the client attempts a request
    # Then it raises QBOAuthExpiredError without hitting the network
    with pytest.raises(QBOAuthExpiredError):
        await client.read("companyinfo", "9999")


async def test_429_raises_rate_limit_error(
    http: httpx.AsyncClient, httpx_mock: HTTPXMock
) -> None:
    # Given a fresh token and the QBO API returning 429
    store = InMemoryTokenStore(_fresh_bundle(), _settings(), http)
    client = QBOClient(_settings(), store, http)
    httpx_mock.add_response(
        url=_company_url() + "?minorversion=75",
        status_code=429,
        json={},
    )

    # When the client reads CompanyInfo
    # Then it raises QBORateLimitError
    with pytest.raises(QBORateLimitError):
        await client.read("companyinfo", "9999")


async def test_fault_response_raises_fault_error(
    http: httpx.AsyncClient, httpx_mock: HTTPXMock
) -> None:
    # Given a fresh token and the QBO API returning a 400 with a Fault body
    store = InMemoryTokenStore(_fresh_bundle(), _settings(), http)
    client = QBOClient(_settings(), store, http)
    httpx_mock.add_response(
        url=_company_url() + "?minorversion=75",
        status_code=400,
        json={
            "Fault": {
                "Error": [
                    {
                        "Message": "Object Not Found",
                        "Detail": "Object Not Found: ...",
                        "code": "610",
                    }
                ],
                "type": "ValidationFault",
            }
        },
    )

    # When the client reads CompanyInfo
    # Then it raises QBOFaultError carrying the fault Message and Detail
    with pytest.raises(QBOFaultError) as exc:
        await client.read("companyinfo", "9999")
    assert exc.value.message == "Object Not Found"
    assert "Object Not Found:" in exc.value.detail


async def test_create_sends_uuid4_request_id_and_minorversion(
    http: httpx.AsyncClient, httpx_mock: HTTPXMock
) -> None:
    # Given a fresh token and a mocked invoice-create endpoint
    store = InMemoryTokenStore(_fresh_bundle(), _settings(), http)
    client = QBOClient(_settings(), store, http)
    httpx_mock.add_response(
        url=f"https://sandbox-quickbooks.api.intuit.com/v3/company/9999/invoice?minorversion=75",
        json={"Invoice": {"Id": "1"}},
    )

    # When the client creates an invoice
    await client.create("invoice", {"Line": []})

    # Then the outbound request carries a valid uuid4 hex Request-Id header
    req = httpx_mock.get_requests()[-1]
    request_id = req.headers["Request-Id"]
    assert re.fullmatch(r"[0-9a-f]{32}", request_id)
    uuid.UUID(request_id)  # parseable


async def test_expiring_token_auto_refreshes(
    http: httpx.AsyncClient, httpx_mock: HTTPXMock
) -> None:
    # Given a token that expires within the 300 s refresh skew window
    near_expiry = TokenBundle(
        access_token="old-access",
        refresh_token="old-refresh",
        access_expires_at=int(time.time()) + 60,
    )
    store = InMemoryTokenStore(near_expiry, _settings(), http)
    client = QBOClient(_settings(), store, http)
    httpx_mock.add_response(
        url=INTUIT_TOKEN_URL,
        json={
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        },
    )
    httpx_mock.add_response(
        url=_company_url() + "?minorversion=75",
        json={"CompanyInfo": {"CompanyName": "Acme"}},
    )

    # When the client reads CompanyInfo
    await client.read("companyinfo", "9999")

    # Then the bundle was refreshed before the read went out
    assert store.bundle is not None
    assert store.bundle.access_token == "new-access"


# --- helpers and fixtures --------------------------------------------------


class InMemoryTokenStore(TokenStore):
    def __init__(self, bundle: TokenBundle | None, settings: Settings, http: httpx.AsyncClient) -> None:
        super().__init__(settings, http)
        self.bundle = bundle
        self.saves: list[TokenBundle] = []

    async def load(self) -> TokenBundle | None:
        return self.bundle

    async def save(self, bundle: TokenBundle) -> None:
        self.bundle = bundle
        self.saves.append(bundle)


def _settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def _fresh_bundle() -> TokenBundle:
    return TokenBundle(
        access_token="access-1",
        refresh_token="refresh-1",
        access_expires_at=int(time.time()) + 3600,
    )


def _company_url(realm: str = "9999", suffix: str = "/companyinfo/9999") -> str:
    return f"https://sandbox-quickbooks.api.intuit.com/v3/company/{realm}{suffix}"


@pytest.fixture
async def http() -> httpx.AsyncClient:
    async with httpx.AsyncClient() as client:
        yield client
