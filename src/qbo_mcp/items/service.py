"""Business-level QuickBooks Online item-catalog operations.

`ItemService` turns the generic transport primitives on `QBOClient` (`query`)
into typed, entity-aware item operations — the layer the item MCP tools call.
Keeping it separate from `QBOClient` keeps HTTP/auth/retry concerns out of the
domain logic and the domain logic out of the transport.

`QBOClient.query` takes raw QBO SQL, so the contract lives here: free text MUST be
escaped before it reaches the SQL, using `escape_qbo_string` from `qbo_client`.
MCP tools call these typed methods — never `client.query` directly.
"""
from __future__ import annotations

from typing import Any

from ..qbo_client import QBOClient, escape_qbo_string


class ItemService:
    def __init__(self, client: QBOClient) -> None:
        self._client = client

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
