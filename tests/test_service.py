from __future__ import annotations

import re
import time
from typing import Any, AsyncGenerator
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from httpx import AsyncClient
from pytest_httpx import HTTPXMock

from qbo_mcp.config import Settings
from qbo_mcp.qbo_client import QBOClient
from qbo_mcp.service import QBOService
from qbo_mcp.token_store import TokenBundle, TokenStore

QUERY_URL = re.compile(
    r"https://sandbox-quickbooks\.api\.intuit\.com/v3/company/9999/query\?.*"
)


class TestInvoiceOperations:
    async def test_find_invoice_by_doc_number_escapes_and_returns_first_match(
        self, http: httpx.AsyncClient, httpx_mock: HTTPXMock
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
        self, http: httpx.AsyncClient, httpx_mock: HTTPXMock
    ) -> None:
        # Given a service over a fresh-token client and QBO returning no invoices
        service = QBOService(QBOClient(_settings(), _store(http), http))
        httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {}})

        # When an unknown DocNumber is resolved
        invoice = await service.find_invoice_by_doc_number("9999")

        # Then None is returned rather than an error or empty object
        assert invoice is None

    async def test_get_invoices_validates_id_builds_dates_and_orders(
        self, http: httpx.AsyncClient, httpx_mock: HTTPXMock
    ) -> None:
        # Given a service over a fresh-token client and QBO returning an invoice list
        service = QBOService(QBOClient(_settings(), _store(http), http))
        httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {"Invoice": [{"Id": "9"}]}})

        # When listing a customer's invoices with both date bounds
        invoices = await service.get_invoices("1", from_date="2026-01-01", to_date="2026-12-31")

        # Then the SQL filtered on CustomerRef, bounded TxnDate, and ordered newest-first
        sent_sql = parse_qs(urlparse(str(httpx_mock.get_requests()[-1].url)).query)["query"][0]
        assert "CustomerRef = '1'" in sent_sql
        assert "TxnDate >= '2026-01-01'" in sent_sql
        assert "TxnDate <= '2026-12-31'" in sent_sql
        assert "ORDER BY TxnDate DESC MAXRESULTS 50" in sent_sql
        assert invoices == [{"Id": "9"}]

    async def test_get_invoices_rejects_non_numeric_customer_id(
        self, http: httpx.AsyncClient, httpx_mock: HTTPXMock
    ) -> None:
        # Given a service over a fresh-token client (no QBO call should be made)
        service = QBOService(QBOClient(_settings(), _store(http), http))

        # When a non-numeric customer id is passed
        # Then it raises ValueError before any request goes out
        with pytest.raises(ValueError):
            await service.get_invoices("1; DROP TABLE")


class TestCustomerOperations:
    async def test_search_customers_escapes_name_and_filters_active(
        self, http: httpx.AsyncClient, httpx_mock: HTTPXMock
    ) -> None:
        # Given a service over a fresh-token client and QBO returning a customer list
        service = QBOService(QBOClient(_settings(), _store(http), http))
        httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {"Customer": [{"Id": "58"}]}})

        # When searching with a name containing a single quote
        customers = await service.search_customers("Amy's")

        # Then the SQL escaped the name, filtered Active customers, and capped at 20
        sent_sql = parse_qs(urlparse(str(httpx_mock.get_requests()[-1].url)).query)["query"][0]
        assert "DisplayName LIKE '%Amy''s%'" in sent_sql
        assert "Active = true" in sent_sql
        assert "MAXRESULTS 20" in sent_sql
        assert customers == [{"Id": "58"}]

    async def test_search_customers_returns_empty_list_when_none_match(
        self, http: httpx.AsyncClient, httpx_mock: HTTPXMock
    ) -> None:
        # Given a service over a fresh-token client and QBO returning no customers
        service = QBOService(QBOClient(_settings(), _store(http), http))
        httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {}})

        # When searching for a name that matches nothing
        customers = await service.search_customers("Nobody")

        # Then an empty list is returned rather than an error
        assert customers == []


class TestItemOperations:
    async def test_list_items_filters_active_sellable_types_without_name(
        self, http: httpx.AsyncClient, httpx_mock: HTTPXMock
    ) -> None:
        # Given a service over a fresh-token client and QBO returning an item list
        service = QBOService(QBOClient(_settings(), _store(http), http))
        httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {"Item": [{"Id": "4"}]}})

        # When listing items with no name filter
        items = await service.list_items()

        # Then the SQL constrained type + Active and added no Name clause
        sent_sql = parse_qs(urlparse(str(httpx_mock.get_requests()[-1].url)).query)["query"][0]
        assert "Type IN ('Service', 'NonInventory', 'Inventory')" in sent_sql
        assert "Active = true" in sent_sql
        assert "Name LIKE" not in sent_sql
        assert items == [{"Id": "4"}]

    async def test_list_items_escapes_and_appends_name_filter(
        self, http: httpx.AsyncClient, httpx_mock: HTTPXMock
    ) -> None:
        # Given a service over a fresh-token client and QBO returning an item list
        service = QBOService(QBOClient(_settings(), _store(http), http))
        httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {"Item": [{"Id": "4"}]}})

        # When listing items with a name containing a single quote
        items = await service.list_items("O'Design")

        # Then the SQL appended an escaped Name LIKE clause
        sent_sql = parse_qs(urlparse(str(httpx_mock.get_requests()[-1].url)).query)["query"][0]
        assert "Name LIKE '%O''Design%'" in sent_sql
        assert items == [{"Id": "4"}]


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
async def http() -> AsyncGenerator[AsyncClient, Any]:
    async with httpx.AsyncClient() as client:
        yield client
