"""FastMCP server exposing QuickBooks Online tools over streamable HTTP.

Runs on `0.0.0.0:$PORT` (default 8080). MCP clients authenticate with a static
bearer token (`MCP_BEARER_TOKEN`) via `Authorization: Bearer <token>`. A plain,
unauthenticated `GET /health` route returns ``ok`` for Render health checks.

Local testing
-------------
Start the server::

    uv run python -m qbo_mcp.server

Inspect it with the MCP Inspector (https://github.com/modelcontextprotocol/inspector)::

    npx @modelcontextprotocol/inspector

  → Transport: "Streamable HTTP"
  → URL: http://localhost:8080/mcp
  → Header: Authorization: Bearer <MCP_BEARER_TOKEN>

Or wire it into Claude Code::

    claude mcp add --transport http qbo http://localhost:8080/mcp \\
      --header "Authorization: Bearer <MCP_BEARER_TOKEN>"
"""
from __future__ import annotations

import contextlib
import os
from typing import Any, AsyncIterator

import httpx
from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from .config import get_settings
from .qbo_client import (
    QBOAuthExpiredError,
    QBOClient,
    QBOFaultError,
    QBORateLimitError,
)
from .service import QBOService
from .token_store import TokenStore

DEFAULT_PORT = 8080
INVOICE_DEEP_LINK = "https://app.qbo.intuit.com/app/invoice?txnId={id}"

mcp = FastMCP(
    name="quickbooks-online",
    auth=StaticTokenVerifier(
        tokens={get_settings().mcp_bearer_token: {"client_id": "qbo-mcp", "scopes": []}}
    ),
)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> PlainTextResponse:
    """Unauthenticated liveness probe for Render. Returns 200 ``ok``."""
    return PlainTextResponse("ok")


@mcp.tool(
    name="get_invoice",
    description=(
        "Retrieve the full detail of a single QuickBooks invoice by its human-facing "
        "document number (e.g. \"1010\" printed on the invoice) — NOT QuickBooks' internal "
        "Id. Returns the header fields, every line item (description, quantity, unit price, "
        "amount), the subtotal/total/balance, the email-delivery status, and a deep_link "
        "that opens the invoice in the QuickBooks web app. If no invoice carries that "
        "document number, returns a message saying so. On failure returns a human-readable "
        "error string."
    ),
    tags={"invoices", "read"},
)
async def get_invoice(doc_number: str) -> dict[str, Any] | str:
    """Read one invoice by its human-facing DocNumber (see decorator `description`)."""
    try:
        async with _qbo() as service:
            invoice = await service.find_invoice_by_doc_number(doc_number)
        if invoice is None:
            return f"No invoice found with document number {doc_number!r}."
        return _fmt_invoice(invoice)
    except Exception as exc:  # noqa: BLE001 — tools must never leak tracebacks
        return _format_error(exc)


@mcp.tool(
    name="search_customers",
    description=(
        "Find QuickBooks customers by (partial) display name — the first step before "
        "creating an invoice. Use this to turn a customer name the user typed into the "
        "customer_id that get_invoices and create_invoice require. The name is matched "
        "case-insensitively against the customer's display name; partial matches work. "
        "Returns up to 20 active customers, each with id, display_name, company_name, "
        "email, and balance (their open balance). If several match, show them to the user "
        "and confirm which one they mean. On failure returns a human-readable error string."
    ),
    tags={"customers", "read"},
)
async def search_customers(name: str) -> list[dict[str, Any]] | str:
    """Find active customers by partial display name (see decorator `description`)."""
    try:
        async with _qbo() as service:
            customers = await service.search_customers(name)
        return [_fmt_customer(c) for c in customers]
    except Exception as exc:  # noqa: BLE001 — tools must never leak tracebacks
        return _format_error(exc)


@mcp.tool(
    name="list_items",
    description=(
        "List the active products/services in the QuickBooks item catalog — the source of "
        "the item_id each create_invoice line needs. Pass an optional name substring to "
        "narrow the catalog (case-insensitive, partial match); omit it to list everything. "
        "Returns id, name, type (Service/NonInventory/Inventory), description, and unit_price. "
        "Use the returned unit_price as the default line price unless the user overrides it. "
        "On failure returns a human-readable error string."
    ),
    tags={"items", "read"},
)
async def list_items(name: str | None = None) -> list[dict[str, Any]] | str:
    """List active sellable catalog items, optional name filter (see decorator `description`)."""
    try:
        async with _qbo() as service:
            items = await service.list_items(name)
        return [_fmt_item(i) for i in items]
    except Exception as exc:  # noqa: BLE001 — tools must never leak tracebacks
        return _format_error(exc)


def main() -> None:
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    mcp.run(transport="http", host="0.0.0.0", port=port)


# --- helpers ---------------------------------------------------------------


@contextlib.asynccontextmanager
async def _qbo() -> AsyncIterator[QBOService]:
    """Yield a QBOService over a QBOClient backed by a fresh httpx client.

    Mirrors smoke_test.py's wiring; the service is the seam every tool calls.
    """
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30.0) as http:
        store = TokenStore(settings, http)
        yield QBOService(QBOClient(settings, store, http))


def _fmt_invoice(inv: dict[str, Any]) -> dict[str, Any]:
    """Reduce a raw QBO Invoice object to the trimmed shape the tool returns.

    `inv` is the `"Invoice"` value of a QBO read response, e.g.::

        {
            "Id": "130",
            "DocNumber": "1037",
            "TxnDate": "2026-06-01",
            "DueDate": "2026-07-01",
            "CustomerRef": {"value": "58", "name": "Amy's Bird Sanctuary"},
            "TotalAmt": 387.50,
            "Balance": 387.50,
            "EmailStatus": "NotSet",
            "Line": [
                {
                    "DetailType": "SalesItemLineDetail",
                    "Amount": 250.00,
                    "Description": "Custom design",
                    "SalesItemLineDetail": {
                        "ItemRef": {"value": "1", "name": "Design"},
                        "Qty": 5,
                        "UnitPrice": 50,
                    },
                },
                {"DetailType": "SubTotalLineDetail", "Amount": 387.50, ...},
            ],
        }

    `SalesItemLineDetail` and `DescriptionOnly` lines both become `lines` (the
    latter — free-text rows like "Service Period: May 2026" — with null
    qty/unit_price/amount, kept in their original position). `subtotal` and
    `discount` are read straight from QBO's own `SubTotalLineDetail` /
    `DiscountLineDetail` lines (we don't re-derive them — QBO already computed
    them, and `subtotal - discount` reconciles to `total`/`TotalAmt`).
    """
    lines: list[dict[str, Any]] = []
    subtotal: float | None = None
    discount: float | None = None
    for line in inv.get("Line", []):
        detail_type = line.get("DetailType")
        if detail_type == "SalesItemLineDetail":
            detail = line.get("SalesItemLineDetail") or {}
            lines.append(
                {
                    "description": line.get("Description"),
                    "qty": detail.get("Qty"),
                    "unit_price": detail.get("UnitPrice"),
                    "amount": line.get("Amount"),
                }
            )
        elif detail_type == "DescriptionOnly":
            lines.append(
                {
                    "description": line.get("Description"),
                    "qty": None,
                    "unit_price": None,
                    "amount": None,
                }
            )
        elif detail_type == "SubTotalLineDetail":
            subtotal = line.get("Amount")
        elif detail_type == "DiscountLineDetail":
            discount = line.get("Amount")
    invoice_id = inv.get("Id", "")
    return {
        "doc_number": inv.get("DocNumber"),
        "id": invoice_id,
        "customer": (inv.get("CustomerRef") or {}).get("name"),
        "txn_date": inv.get("TxnDate"),
        "due_date": inv.get("DueDate"),
        "lines": lines,
        "subtotal": subtotal,
        "discount": discount,
        "total": inv.get("TotalAmt"),
        "balance": inv.get("Balance"),
        "email_status": inv.get("EmailStatus"),
        "deep_link": INVOICE_DEEP_LINK.format(id=invoice_id),
    }


def _fmt_customer(cust: dict[str, Any]) -> dict[str, Any]:
    """Trim a raw QBO Customer object to the fields search_customers surfaces."""
    return {
        "id": cust.get("Id"),
        "display_name": cust.get("DisplayName"),
        "company_name": cust.get("CompanyName"),
        "email": (cust.get("PrimaryEmailAddr") or {}).get("Address"),
        "balance": cust.get("Balance"),
    }


def _fmt_item(item: dict[str, Any]) -> dict[str, Any]:
    """Trim a raw QBO Item object to the fields list_items surfaces."""
    return {
        "id": item.get("Id"),
        "name": item.get("Name"),
        "type": item.get("Type"),
        "description": item.get("Description"),
        "unit_price": item.get("UnitPrice"),
    }


def _format_error(exc: Exception) -> str:
    if isinstance(exc, QBOAuthExpiredError):
        return str(exc)
    if isinstance(exc, QBORateLimitError):
        return "QuickBooks rate limit exceeded — please wait a moment and try again."
    if isinstance(exc, QBOFaultError):
        return f"QuickBooks error: {exc.message} ({exc.detail})"
    if isinstance(exc, ValueError):
        return f"Invalid input: {exc}"
    return f"Unexpected error talking to QuickBooks: {exc}"


# The __main__ guard MUST be the last statement in the file: running as a module
# invokes main() here, and mcp.run() blocks — any definitions below would never
# execute (this is what broke the helpers when they sat below this guard).
if __name__ == "__main__":
    main()
