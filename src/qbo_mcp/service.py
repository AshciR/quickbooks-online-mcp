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

from .qbo_client import QBOClient, escape_qbo_string


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
