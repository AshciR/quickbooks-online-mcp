from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

import pytest
from pytest_httpx import HTTPXMock

from qbo_mcp.invoices.service import InvoiceService, LineInput
from qbo_mcp.qbo_client import QBOClient

_BASE = r"https://sandbox-quickbooks\.api\.intuit\.com/v3/company/9999"
QUERY_URL = re.compile(rf"{_BASE}/query\?.*")
ITEM_READ_URL = re.compile(rf"{_BASE}/item/\d+\?.*")
INVOICE_CREATE_URL = re.compile(rf"{_BASE}/invoice\?.*")


class TestInvoiceOperations:
    async def test_find_invoice_by_doc_number_escapes_and_returns_first_match(
        self, qbo_client: QBOClient, httpx_mock: HTTPXMock
    ) -> None:
        # Given a service over a fresh-token client and QBO returning an invoice for the query
        service = InvoiceService(qbo_client)
        httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {"Invoice": [{"Id": "130"}]}})

        # When a DocNumber containing a single quote is resolved
        invoice = await service.find_invoice_by_doc_number("O'1037")

        # Then the outbound SQL filtered on the escaped DocNumber and the first match is returned
        sent_sql = parse_qs(urlparse(str(httpx_mock.get_requests()[-1].url)).query)["query"][0]
        assert "DocNumber = 'O''1037'" in sent_sql
        assert invoice == {"Id": "130"}

    async def test_find_invoice_by_doc_number_returns_none_when_absent(
        self, qbo_client: QBOClient, httpx_mock: HTTPXMock
    ) -> None:
        # Given a service over a fresh-token client and QBO returning no invoices
        service = InvoiceService(qbo_client)
        httpx_mock.add_response(url=QUERY_URL, json={"QueryResponse": {}})

        # When an unknown DocNumber is resolved
        invoice = await service.find_invoice_by_doc_number("9999")

        # Then None is returned rather than an error or empty object
        assert invoice is None

    async def test_get_invoices_validates_id_builds_dates_and_orders(
        self, qbo_client: QBOClient, httpx_mock: HTTPXMock
    ) -> None:
        # Given a service over a fresh-token client and QBO returning an invoice list
        service = InvoiceService(qbo_client)
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
        self, qbo_client: QBOClient, httpx_mock: HTTPXMock
    ) -> None:
        # Given a service over a fresh-token client (no QBO call should be made)
        service = InvoiceService(qbo_client)

        # When a non-numeric customer id is passed
        # Then it raises ValueError before any request goes out
        with pytest.raises(ValueError):
            await service.get_invoices("1; DROP TABLE")

    async def test_create_invoice_fills_missing_price_and_builds_payload(
        self, qbo_client: QBOClient, httpx_mock: HTTPXMock
    ) -> None:
        # Given a service whose line omits unit_price, plus the Item read and create responses
        service = InvoiceService(qbo_client)
        httpx_mock.add_response(url=ITEM_READ_URL, json={"Item": {"Id": "4", "UnitPrice": 75}})
        httpx_mock.add_response(
            url=INVOICE_CREATE_URL,
            json={"Invoice": {"Id": "145", "DocNumber": "1038", "TotalAmt": 150.0}},
        )

        # When an invoice is created with quantity 2 and no unit_price
        invoice = await service.create_invoice(
            "1",
            [LineInput(item_id="4", quantity=2, description="Design work")],
            due_date="2026-07-01",
            customer_memo="Thanks",
        )

        # Then the Item was read for its price and the posted payload used it (Amount = 2 * 75)
        post = next(r for r in httpx_mock.get_requests() if r.method == "POST")
        body = json.loads(post.content)
        line = body["Line"][0]
        assert line["Amount"] == 150.0
        assert line["SalesItemLineDetail"] == {
            "ItemRef": {"value": "4"},
            "Qty": 2,
            "UnitPrice": 75,
        }
        assert line["Description"] == "Design work"
        assert body["CustomerRef"] == {"value": "1"}
        assert body["DueDate"] == "2026-07-01"
        assert body["CustomerMemo"] == {"value": "Thanks"}
        # And the created QBO Invoice object is returned
        assert invoice == {"Id": "145", "DocNumber": "1038", "TotalAmt": 150.0}

    async def test_create_invoice_uses_explicit_price_without_item_read(
        self, qbo_client: QBOClient, httpx_mock: HTTPXMock
    ) -> None:
        # Given a line that supplies its own unit_price (no Item read response is registered)
        service = InvoiceService(qbo_client)
        httpx_mock.add_response(url=INVOICE_CREATE_URL, json={"Invoice": {"Id": "146"}})

        # When an invoice is created with an explicit unit_price
        await service.create_invoice("1", [LineInput(item_id="4", quantity=3, unit_price=10.0)])

        # Then no Item read was issued and Amount used the explicit price (3 * 10)
        assert all("/item/" not in str(r.url) for r in httpx_mock.get_requests())
        body = json.loads(next(r for r in httpx_mock.get_requests() if r.method == "POST").content)
        assert body["Line"][0]["Amount"] == 30.0
        assert body["Line"][0]["SalesItemLineDetail"]["UnitPrice"] == 10.0
