from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from fastmcp import Client
from pytest_httpx import HTTPXMock

from qbo_mcp.server import mcp
from qbo_mcp.token_store import TokenBundle

QUERY_URL = re.compile(
    r"https://sandbox-quickbooks\.api\.intuit\.com/v3/company/9999/query\?.*"
)
UPSTASH_GET_URL = "https://upstash.test/get/qbo:tokens"


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
        assert result["total"] == 348.75  # 387.5 - 38.75

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

    @staticmethod
    def _invoice_list_fixture() -> list[dict[str, Any]]:
        return [
            {
                "Id": "67",
                "DocNumber": "1021",
                "TxnDate": "2026-04-25",
                "DueDate": "2026-05-25",
                "TotalAmt": 459.0,
                "Balance": 239.0,
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
            "TotalAmt": 348.75,
            "Balance": 348.75,
            "EmailStatus": "NotSet",
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


def _mock_token(httpx_mock: HTTPXMock) -> None:
    bundle = TokenBundle(
        access_token="access-1",
        refresh_token="refresh-1",
        access_expires_at=int(time.time()) + 3600,
    )
    httpx_mock.add_response(url=UPSTASH_GET_URL, json={"result": bundle.model_dump_json()})

