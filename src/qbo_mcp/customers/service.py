"""Business-level QuickBooks Online customer operations.

`CustomerService` turns the generic transport primitives on `QBOClient` (`query`)
into typed, entity-aware customer operations — the layer the customer MCP tools
call. Keeping it separate from `QBOClient` keeps HTTP/auth/retry concerns out of
the domain logic and the domain logic out of the transport.

`QBOClient.query` takes raw QBO SQL, so the contract lives here: free text MUST be
escaped before it reaches the SQL, using `escape_qbo_string` from `qbo_client`.
MCP tools call these typed methods — never `client.query` directly.
"""
from __future__ import annotations

from typing import Any

from ..qbo_client import QBOClient, escape_qbo_string


class CustomerService:
    def __init__(self, client: QBOClient) -> None:
        self._client = client

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
