from __future__ import annotations

import re
import time
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from pytest_httpx import HTTPXMock

from qbo_mcp.config import get_settings
from qbo_mcp.server import build_auth, mcp
from qbo_mcp.token_store import TokenBundle

_BASE = r"https://sandbox-quickbooks\.api\.intuit\.com/v3/company/9999"
QUERY_URL = re.compile(rf"{_BASE}/query\?.*")
ITEM_READ_URL = re.compile(rf"{_BASE}/item/\d+\?.*")
INVOICE_CREATE_URL = re.compile(rf"{_BASE}/invoice\?.*")
UPSTASH_GET_URL = "https://upstash.test/get/qbo:tokens:sandbox"


class TestInvoiceTools:
    async def test_server_registers_get_invoice_tool(self) -> None:
        # Given the in-memory MCP server
        # When its tool catalog is listed
        async with _client() as client:
            tools = await client.list_tools()

        # Then get_invoice is registered with a single required string doc_number param
        by_name = {tool.name: tool for tool in tools}
        assert "get_invoice" in by_name
        schema = by_name["get_invoice"].inputSchema
        assert schema["required"] == ["doc_number"]
        assert schema["properties"]["doc_number"]["type"] == "string"

    async def test_get_invoice_queries_by_doc_number_and_maps_fields(self, httpx_mock: HTTPXMock) -> None:
        # Given a fresh token and QBO returning the invoice for a DocNumber query
        _mock_token(httpx_mock)
        httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {"Invoice": [self._invoice_fixture()]}})

        # When get_invoice is called with the human-facing document number
        result = await _call_tool("get_invoice", {"doc_number": "1037"})

        # Then the outbound query filtered on the escaped DocNumber, and the trimmed
        # header, sales lines, computed subtotal, and deep link are returned
        sent_url = httpx_mock.get_requests()[-1].url
        sent_params = parse_qs(urlparse(str(sent_url)).query)
        sent_sql = sent_params["query"][0]
        assert "DocNumber = '1037'" in sent_sql
        assert result["doc_number"] == "1037"
        assert result["customer"] == "Amy's Bird Sanctuary"
        assert result["email_status"] == "NotSet"
        assert result["deep_link"] == "https://app.qbo.intuit.com/app/invoice?txnId=130"
        # The DescriptionOnly text line is kept inline in its original position...
        assert [line["description"] for line in result["lines"]] == [
            "Custom design",
            "Service Period: May 2026",
            "Hosting",
        ]
        # ...with null numerics so it doesn't read as a priced item
        text_line = result["lines"][1]
        assert text_line["qty"] is None and text_line["unit_price"] is None and text_line["amount"] is None
        # subtotal/discount come straight from QBO's own lines, total from TotalAmt
        assert result["subtotal"] == 387.5  # SubTotalLineDetail.Amount, not a re-sum
        assert result["discount"] == 38.75  # DiscountLineDetail.Amount
        assert result["tax"] == 31.25  # TxnTaxDetail.TotalTax
        assert result["total"] == 380.0  # 387.5 - 38.75 + 31.25

    async def test_get_invoice_without_tax_reports_zero(self, httpx_mock: HTTPXMock) -> None:
        # Given a fresh token and QBO returning an invoice that carries no TxnTaxDetail
        _mock_token(httpx_mock)
        invoice = self._invoice_fixture()
        del invoice["TxnTaxDetail"]
        httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {"Invoice": [invoice]}})

        # When get_invoice is called for that document number
        result = await _call_tool("get_invoice", {"doc_number": "1037"})

        # Then tax is reported as 0 (numeric), not null or a missing key
        assert result["tax"] == 0

    async def test_get_invoice_not_found_returns_message(self, httpx_mock: HTTPXMock) -> None:
        # Given a fresh token and QBO returning no invoices for the DocNumber query
        _mock_token(httpx_mock)
        httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {}})

        # When get_invoice is called with an unknown document number
        result = await _call_tool("get_invoice", {"doc_number": "9999"})

        # Then it returns a readable not-found message, not an error or empty invoice
        assert result == "No invoice found with document number '9999'."

    async def test_get_invoice_auth_expired_returns_bootstrap_string(self, httpx_mock: HTTPXMock) -> None:
        # Given Upstash holding no token bundle (QBO auth never bootstrapped)
        httpx_mock.add_response(url=UPSTASH_GET_URL, json={"result": None})

        # When the get_invoice tool is called
        result = await _call_tool("get_invoice", {"doc_number": "1037"})

        # Then it returns the re-run-bootstrap message as a string, not a raised traceback
        assert result == "QBO authorization expired — re-run scripts/bootstrap_oauth.py"

    async def test_get_invoices_validates_id_and_summarizes(self, httpx_mock: HTTPXMock) -> None:
        # Given a fresh token and QBO returning a customer's invoices
        _mock_token(httpx_mock)
        httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {"Invoice": self._invoice_list_fixture()}})

        # When get_invoices is called for a customer with the default 'all' status
        result = await _call_tool("get_invoices", {"customer_id": "1"})

        # Then the query filtered on the customer and each row carries a one-line summary
        sent_sql = parse_qs(urlparse(str(httpx_mock.get_requests()[-1].url)).query)["query"][0]
        assert "CustomerRef = '1'" in sent_sql
        assert [r["doc_number"] for r in result] == ["1021", "1001"]
        assert result[0]["summary"] == "Rock Fountain; Pump"
        assert result[1]["summary"] == "Weekly Gardening Service"
        # Tax (TxnTaxDetail.TotalTax) is surfaced; an untaxed invoice reports 0, not null
        assert result[0]["tax"] == 25.0
        assert result[1]["tax"] == 0

    async def test_get_invoices_status_open_filters_on_balance(self, httpx_mock: HTTPXMock) -> None:
        # Given a fresh token and QBO returning one open (balance>0) and one paid (balance=0) invoice
        _mock_token(httpx_mock)
        httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {"Invoice": self._invoice_list_fixture()}})

        # When get_invoices is called with status='open'
        result = await _call_tool("get_invoices", {"customer_id": "1", "status": "open"})

        # Then only the invoice with a non-zero balance is returned
        assert [r["doc_number"] for r in result] == ["1021"]
        assert result[0]["balance"] == 239.0

    async def test_get_invoices_status_paid_filters_on_balance(self, httpx_mock: HTTPXMock) -> None:
        # Given a fresh token and QBO returning one open and one paid invoice
        _mock_token(httpx_mock)
        httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {"Invoice": self._invoice_list_fixture()}})

        # When get_invoices is called with status='paid'
        result = await _call_tool("get_invoices", {"customer_id": "1", "status": "paid"})

        # Then only the fully-paid (zero balance) invoice is returned
        assert [r["doc_number"] for r in result] == ["1001"]
        assert result[0]["balance"] == 0

    async def test_get_invoices_rejects_non_numeric_customer_id(self) -> None:
        # Given: no QBO mocks — validation must fail before any token load or query

        # When get_invoices is called with a non-numeric customer id
        result = await _call_tool("get_invoices", {"customer_id": "abc"})

        # Then it returns a readable invalid-input string, not a raised traceback
        assert result.startswith("Invalid input:")

    async def test_server_registers_create_invoice_tool(self) -> None:
        # Given the in-memory MCP server
        # When its tool catalog is listed
        async with _client() as client:
            tools = await client.list_tools()

        # Then create_invoice is registered requiring customer_id and lines
        by_name = {tool.name: tool for tool in tools}
        assert "create_invoice" in by_name
        schema = by_name["create_invoice"].inputSchema
        assert set(schema["required"]) == {"customer_id", "lines"}

    async def test_create_invoice_returns_id_doc_number_and_deep_link(self, httpx_mock: HTTPXMock) -> None:
        # Given a fresh token (loaded once per QBO call: Item read + create), the Item
        # price read, and QBO returning the created invoice
        _mock_token(httpx_mock, times=2)
        httpx_mock.add_response(url=ITEM_READ_URL, json={"Item": {"Id": "4", "UnitPrice": 75}})
        httpx_mock.add_response(
            url=INVOICE_CREATE_URL,
            json={"Invoice": {"Id": "145", "DocNumber": "1038", "TotalAmt": 150.0}},
        )

        # When create_invoice is called with a single line that omits unit_price
        result = await _call_tool(
            "create_invoice",
            {"customer_id": "1", "lines": [{"item_id": "4", "quantity": 2}]},
        )

        # Then the tool returns the trimmed created-invoice shape with a deep link
        assert result == {
            "id": "145",
            "doc_number": "1038",
            "total": 150.0,
            "deep_link": "https://app.qbo.intuit.com/app/invoice?txnId=145",
        }

    async def test_create_invoice_rejects_non_numeric_customer_id(self) -> None:
        # Given: no QBO mocks — validation must fail before any token load or write

        # When create_invoice is called with a non-numeric customer id
        result = await _call_tool(
            "create_invoice",
            {"customer_id": "abc", "lines": [{"item_id": "4", "quantity": 1}]},
        )

        # Then it returns a readable invalid-input string, not a raised traceback
        assert result.startswith("Invalid input:")

    @staticmethod
    def _invoice_list_fixture() -> list[dict[str, Any]]:
        return [
            {
                "Id": "67",
                "DocNumber": "1021",
                "TxnDate": "2026-04-25",
                "DueDate": "2026-05-25",
                "TotalAmt": 484.0,
                "Balance": 239.0,
                "TxnTaxDetail": {"TotalTax": 25.0, "TxnTaxCodeRef": {"value": "2"}},
                "Line": [
                    {"DetailType": "SalesItemLineDetail", "Description": "Rock Fountain", "Amount": 275.0},
                    {"DetailType": "SalesItemLineDetail", "Description": "Pump", "Amount": 184.0},
                    {"DetailType": "SubTotalLineDetail", "Amount": 459.0},
                ],
            },
            {
                "Id": "9",
                "DocNumber": "1001",
                "TxnDate": "2026-05-14",
                "DueDate": "2026-06-13",
                "TotalAmt": 108.0,
                "Balance": 0,
                "Line": [
                    {"DetailType": "SalesItemLineDetail", "Description": "Weekly Gardening Service", "Amount": 108.0},
                    {"DetailType": "SubTotalLineDetail", "Amount": 108.0},
                ],
            },
        ]

    @staticmethod
    def _invoice_fixture() -> dict[str, Any]:
        return {
            "Id": "130",
            "DocNumber": "1037",
            "TxnDate": "2026-06-01",
            "DueDate": "2026-07-01",
            "CustomerRef": {"value": "58", "name": "Amy's Bird Sanctuary"},
            "TotalAmt": 380.0,
            "Balance": 380.0,
            "EmailStatus": "NotSet",
            "TxnTaxDetail": {"TotalTax": 31.25, "TxnTaxCodeRef": {"value": "2"}},
            "Line": [
                {
                    "DetailType": "SalesItemLineDetail",
                    "Amount": 250.0,
                    "Description": "Custom design",
                    "SalesItemLineDetail": {
                        "ItemRef": {"value": "1", "name": "Design"},
                        "Qty": 5,
                        "UnitPrice": 50,
                    },
                },
                {
                    "DetailType": "DescriptionOnly",
                    "Description": "Service Period: May 2026",
                    "DescriptionLineDetail": {},
                },
                {
                    "DetailType": "SalesItemLineDetail",
                    "Amount": 137.5,
                    "Description": "Hosting",
                    "SalesItemLineDetail": {
                        "ItemRef": {"value": "2", "name": "Hosting"},
                        "Qty": 2.75,
                        "UnitPrice": 50,
                    },
                },
                {"DetailType": "SubTotalLineDetail", "Amount": 387.5},
                {
                    "DetailType": "DiscountLineDetail",
                    "Amount": 38.75,
                    "DiscountLineDetail": {"PercentBased": True, "DiscountPercent": 10},
                },
            ],
        }


class TestCustomerTools:
    async def test_server_registers_search_customers_tool(self) -> None:
        # Given the in-memory MCP server
        # When its tool catalog is listed
        async with _client() as client:
            tools = await client.list_tools()

        # Then search_customers is registered with a single required string name param
        by_name = {tool.name: tool for tool in tools}
        assert "search_customers" in by_name
        schema = by_name["search_customers"].inputSchema
        assert schema["required"] == ["name"]
        assert schema["properties"]["name"]["type"] == "string"

    async def test_search_customers_escapes_name_and_maps_fields(self, httpx_mock: HTTPXMock) -> None:
        # Given a fresh token and QBO returning a customer for a DisplayName query
        _mock_token(httpx_mock)
        httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {"Customer": [self._customer_fixture()]}})

        # When search_customers is called with a name containing a single quote
        result = await _call_tool("search_customers", {"name": "Amy's"})

        # Then the outbound query escaped the name and the trimmed customer fields are returned
        sent_sql = parse_qs(urlparse(str(httpx_mock.get_requests()[-1].url)).query)["query"][0]
        assert "DisplayName LIKE '%Amy''s%'" in sent_sql
        assert result == [
            {
                "id": "58",
                "display_name": "Amy's Bird Sanctuary",
                "company_name": "Amy's Bird Sanctuary",
                "email": "Birds@Intuit.com",
                "balance": 239.0,
            }
        ]

    async def test_search_customers_auth_expired_returns_bootstrap_string(self, httpx_mock: HTTPXMock) -> None:
        # Given Upstash holding no token bundle (QBO auth never bootstrapped)
        httpx_mock.add_response(url=UPSTASH_GET_URL, json={"result": None})

        # When the search_customers tool is called
        result = await _call_tool("search_customers", {"name": "Amy"})

        # Then it returns the re-run-bootstrap message as a string, not a raised traceback
        assert result == "QBO authorization expired — re-run scripts/bootstrap_oauth.py"

    @staticmethod
    def _customer_fixture() -> dict[str, Any]:
        return {
            "Id": "58",
            "DisplayName": "Amy's Bird Sanctuary",
            "CompanyName": "Amy's Bird Sanctuary",
            "PrimaryEmailAddr": {"Address": "Birds@Intuit.com"},
            "Balance": 239.0,
        }


class TestItemTools:
    async def test_server_registers_list_items_tool(self) -> None:
        # Given the in-memory MCP server
        # When its tool catalog is listed
        async with _client() as client:
            tools = await client.list_tools()

        # Then list_items is registered with an optional name param (nothing required)
        by_name = {tool.name: tool for tool in tools}
        assert "list_items" in by_name
        schema = by_name["list_items"].inputSchema
        assert schema.get("required", []) == []
        assert "name" in schema["properties"]

    async def test_list_items_maps_fields_without_name(self, httpx_mock: HTTPXMock) -> None:
        # Given a fresh token and QBO returning an item for the catalog query
        _mock_token(httpx_mock)
        httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {"Item": [self._item_fixture()]}})

        # When list_items is called with no name filter
        result = await _call_tool("list_items", {})

        # Then no Name clause was sent and the trimmed item fields are returned
        sent_sql = parse_qs(urlparse(str(httpx_mock.get_requests()[-1].url)).query)["query"][0]
        assert "Name LIKE" not in sent_sql
        assert result == [
            {
                "id": "4",
                "name": "Design",
                "type": "Service",
                "description": "Custom Design",
                "unit_price": 75,
            }
        ]

    async def test_list_items_passes_name_filter(self, httpx_mock: HTTPXMock) -> None:
        # Given a fresh token and QBO returning an item for the filtered query
        _mock_token(httpx_mock)
        httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {"Item": [self._item_fixture()]}})

        # When list_items is called with a name filter
        result = await _call_tool("list_items", {"name": "Design"})

        # Then the outbound query carried an escaped Name LIKE clause
        sent_sql = parse_qs(urlparse(str(httpx_mock.get_requests()[-1].url)).query)["query"][0]
        assert "Name LIKE '%Design%'" in sent_sql
        assert result[0]["id"] == "4"

    @staticmethod
    def _item_fixture() -> dict[str, Any]:
        return {
            "Id": "4",
            "Name": "Design",
            "Type": "Service",
            "Description": "Custom Design",
            "UnitPrice": 75,
        }


class TestAuthMode:
    async def test_oauth_mode_returns_no_auth(self) -> None:
        # Given the bearer token is still present in the environment (conftest-seeded)
        assert get_settings().mcp_bearer_token

        # When the auth gate is built for oauth mode
        auth = build_auth("oauth")

        # Then there is no inbound gate (Horizon supplies OAuth) and the token is ignored,
        # and a server constructs cleanly with auth=None
        assert auth is None
        assert FastMCP(name="quickbooks-online", auth=auth) is not None

    async def test_bearer_mode_returns_static_token_verifier(self) -> None:
        # Given the conftest-seeded MCP_BEARER_TOKEN

        # When the auth gate is built for bearer mode
        auth = build_auth("bearer")

        # Then the existing static-bearer gate is returned, unchanged
        assert isinstance(auth, StaticTokenVerifier)

    async def test_bearer_mode_missing_token_raises_actionable_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Given bearer mode is requested but settings carry no MCP_BEARER_TOKEN.
        # (Patch the settings the server reads — a real .env supplies the token, so
        # clearing the env var alone wouldn't make it absent.)
        monkeypatch.setattr(
            "qbo_mcp.server.get_settings", lambda: SimpleNamespace(mcp_bearer_token=None)
        )

        # When the auth gate is built for bearer mode
        # Then it fails fast with a clear, actionable message naming the missing var
        with pytest.raises(RuntimeError, match="MCP_BEARER_TOKEN"):
            build_auth("bearer")

    async def test_unknown_mode_raises(self) -> None:
        # Given: no preconditions

        # When the auth gate is built for an unrecognized mode
        # Then it fails closed rather than silently running unauthenticated
        with pytest.raises(RuntimeError, match="Unknown MCP_AUTH_MODE"):
            build_auth("foo")


async def test_health_route_returns_ok_unauthenticated() -> None:
    # Given the server's ASGI app and no Authorization header
    transport = httpx.ASGITransport(app=mcp.http_app())

    # When GET /health is requested
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")

    # Then it responds 200 with a plain "ok" body
    assert resp.status_code == 200
    assert resp.text == "ok"


# --- helpers and fixtures --------------------------------------------------


def _client() -> Client:
    # The in-memory FastMCPTransport bypasses the server's bearer auth (and rejects
    # an `auth=` arg) — bearer enforcement only applies over the HTTP transport.
    return Client(mcp)


async def _call_tool(name: str, args: dict[str, Any]) -> Any:
    async with _client() as client:
        result = await client.call_tool(name, args)
    return result.data


def _mock_token(httpx_mock: HTTPXMock, times: int = 1) -> None:
    # Each QBO request loads the token, so a tool that makes N QBO calls (e.g.
    # create_invoice does an Item read + the create) needs N single-use responses.
    bundle = TokenBundle(
        access_token="access-1",
        refresh_token="refresh-1",
        access_expires_at=int(time.time()) + 3600,
    )
    for _ in range(times):
        httpx_mock.add_response(url=UPSTASH_GET_URL, json={"result": bundle.model_dump_json()})

