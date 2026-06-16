from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pytest_httpx import HTTPXMock

from qbo_mcp.config import Settings
from qbo_mcp.qbo_client import QBOClient
from qbo_mcp.service import QBOService
from qbo_mcp.token_store import TokenBundle, TokenStore

QUERY_URL = re.compile(
    r"https://sandbox-quickbooks\.api\.intuit\.com/v3/company/9999/query\?.*"
)


async def test_find_invoice_by_doc_number_escapes_and_returns_first_match(
    http: httpx.AsyncClient, httpx_mock: HTTPXMock
) -> None:
    # Given a service over a fresh-token client and QBO returning an invoice for the query
    service = QBOService(QBOClient(_settings(), _store(http), http))
    httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {"Invoice": [{"Id": "130"}]}})

    # When a DocNumber containing a single quote is resolved
    invoice = await service.find_invoice_by_doc_number("O'1037")

    # Then the outbound SQL filtered on the escaped DocNumber and the first match is returned
    sent_sql = parse_qs(urlparse(str(httpx_mock.get_requests()[-1].url)).query)["query"][0]
    assert "DocNumber = 'O''1037'" in sent_sql
    assert invoice == {"Id": "130"}


async def test_find_invoice_by_doc_number_returns_none_when_absent(
    http: httpx.AsyncClient, httpx_mock: HTTPXMock
) -> None:
    # Given a service over a fresh-token client and QBO returning no invoices
    service = QBOService(QBOClient(_settings(), _store(http), http))
    httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {}})

    # When an unknown DocNumber is resolved
    invoice = await service.find_invoice_by_doc_number("9999")

    # Then None is returned rather than an error or empty object
    assert invoice is None


# --- helpers and fixtures --------------------------------------------------


class InMemoryTokenStore(TokenStore):
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


@pytest.fixture
async def http() -> httpx.AsyncClient:
    async with httpx.AsyncClient() as client:
        yield client
