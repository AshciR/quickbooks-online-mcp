from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from pytest_httpx import HTTPXMock

from tests.conftest import UPSTASH_GET_URL, _call_tool, _client, _mock_token

_BASE = r"https://sandbox-quickbooks\.api\.intuit\.com/v3/company/9999"
QUERY_URL = re.compile(rf"{_BASE}/query\?.*")
ITEM_READ_URL = re.compile(rf"{_BASE}/item/\d+\?.*")
INVOICE_CREATE_URL = re.compile(rf"{_BASE}/invoice\?.*")


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
