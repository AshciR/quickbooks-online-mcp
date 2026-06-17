"""Business-level QuickBooks Online operations.

`QBOService` turns the generic transport primitives on `QBOClient` (`read`,
`create`, `query`) into typed, entity-aware operations — the layer the MCP
tools call. Keeping it separate from `QBOClient` keeps HTTP/auth/retry concerns
out of the domain logic and the domain logic out of the transport.

`QBOClient.query` takes raw QBO SQL, so the contract lives here: any method that
takes an id, a date, or free text MUST validate/escape it before it reaches the
SQL, using `validate_id`, `validate_date`, and `escape_qbo_string` from
`qbo_client`. MCP tools call these typed methods — never `client.query` directly.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .qbo_client import QBOClient, escape_qbo_string, validate_date, validate_id


class LineInput(BaseModel):
    """One requested invoice line, as supplied by the create_invoice tool caller.

    `item_id` is a QBO Item Id (from list_items). When `unit_price` is omitted the
    service looks it up from the Item itself, so callers can rely on catalog pricing.
    """

    item_id: str
    quantity: float = 1
    unit_price: float | None = None
    description: str | None = None


class QBOService:
    def __init__(self, client: QBOClient) -> None:
        self._client = client

    async def find_invoice_by_doc_number(self, doc_number: str) -> dict[str, Any] | None:
        """Resolve a human-facing invoice DocNumber (e.g. "1037") to its full object.

        Users know the document number printed on the invoice, not QBO's internal Id,
        so this builds an escaped `DocNumber` filter and runs it through `query`.
        Returns the first matching invoice, or None when no invoice carries that
        DocNumber.
        """
        sql = f"SELECT * FROM Invoice WHERE DocNumber = '{escape_qbo_string(doc_number)}'"
        body = await self._client.query(sql)
        invoices = body.get("QueryResponse", {}).get("Invoice", [])
        return invoices[0] if invoices else None

    async def search_customers(self, name: str) -> list[dict[str, Any]]:
        """Find active customers whose DisplayName contains `name` (escaped, case-insensitive).

        Returns up to 20 raw QBO Customer objects; the tool layer trims them to the
        fields it surfaces. `name` is free text, so it is escaped before reaching SQL.
        """
        escaped = escape_qbo_string(name)
        sql = (
            f"SELECT * FROM Customer WHERE DisplayName LIKE '%{escaped}%' "
            "AND Active = true MAXRESULTS 20"
        )
        body = await self._client.query(sql)
        return body.get("QueryResponse", {}).get("Customer", [])

    async def list_items(self, name: str | None = None) -> list[dict[str, Any]]:
        """List active sellable items (Service / NonInventory / Inventory).

        These are the catalog entries an invoice line references by Id. An optional
        `name` substring narrows the list (escaped before reaching SQL). Returns raw
        QBO Item objects; the tool layer trims them.
        """
        sql = (
            "SELECT * FROM Item WHERE Type IN ('Service', 'NonInventory', 'Inventory') "
            "AND Active = true"
        )
        if name:
            sql += f" AND Name LIKE '%{escape_qbo_string(name)}%'"
        sql += " MAXRESULTS 100"
        body = await self._client.query(sql)
        return body.get("QueryResponse", {}).get("Item", [])

    async def get_invoices(
        self,
        customer_id: str,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """List a customer's invoices, most recent first (up to 50).

        `customer_id` must be numeric (it is QBO's internal Customer Id from
        `search_customers`) and the optional dates must be ISO `YYYY-MM-DD`; all are
        validated before reaching SQL. The optional dates bound `TxnDate` inclusively.
        Status (open/paid) filtering is left to the caller — see the tool layer.
        Returns raw QBO Invoice objects.
        """
        validate_id(customer_id)
        sql = f"SELECT * FROM Invoice WHERE CustomerRef = '{customer_id}'"
        if from_date:
            validate_date(from_date)
            sql += f" AND TxnDate >= '{from_date}'"
        if to_date:
            validate_date(to_date)
            sql += f" AND TxnDate <= '{to_date}'"
        sql += " ORDER BY TxnDate DESC MAXRESULTS 50"
        body = await self._client.query(sql)
        return body.get("QueryResponse", {}).get("Invoice", [])

    async def create_invoice(
        self,
        customer_id: str,
        lines: list[LineInput],
        due_date: str | None = None,
        customer_memo: str | None = None,
    ) -> dict[str, Any]:
        """Create an invoice for a customer and return the created QBO Invoice object.

        `customer_id` must be numeric and `due_date`, if given, must be ISO. Lines are
        turned into QBO `SalesItemLineDetail` entries by `_build_line_entry` (which fills
        in catalog pricing). The create goes through `QBOClient.create`, which sends a
        Request-Id idempotency header so a retry can't double-post the invoice.
        """
        validate_id(customer_id)
        if due_date:
            validate_date(due_date)
        payload: dict[str, Any] = {
            "Line": [await self._build_line_entry(line) for line in lines],
            "CustomerRef": {"value": customer_id},
        }
        if due_date:
            payload["DueDate"] = due_date
        if customer_memo:
            payload["CustomerMemo"] = {"value": customer_memo}
        body = await self._client.create("invoice", payload)
        return body.get("Invoice", {})

    async def _build_line_entry(self, line: LineInput) -> dict[str, Any]:
        """Turn one `LineInput` into a QBO SalesItemLineDetail line entry.

        Validates the item id, and when `unit_price` is omitted reads the Item to use
        its catalog `UnitPrice`, so `Amount` is always quantity * unit_price.
        """
        validate_id(line.item_id)
        unit_price = line.unit_price
        if unit_price is None:
            item = await self._client.read("item", line.item_id)
            unit_price = (item.get("Item") or {}).get("UnitPrice", 0)
        entry: dict[str, Any] = {
            "DetailType": "SalesItemLineDetail",
            "Amount": line.quantity * unit_price,
            "SalesItemLineDetail": {
                "ItemRef": {"value": line.item_id},
                "Qty": line.quantity,
                "UnitPrice": unit_price,
            },
        }
        if line.description:
            entry["Description"] = line.description
        return entry
