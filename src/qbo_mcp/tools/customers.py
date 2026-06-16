"""Customer tools, mounted onto the root server (see `invoices.py` for the pattern)."""
from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from .shared import _format_error, _qbo

customers = FastMCP(name="customers")


@customers.tool(
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
            results = await service.search_customers(name)
        return [_fmt_customer(c) for c in results]
    except Exception as exc:  # noqa: BLE001 — tools must never leak tracebacks
        return _format_error(exc)


# --- helpers ---------------------------------------------------------------


def _fmt_customer(cust: dict[str, Any]) -> dict[str, Any]:
    """Trim a raw QBO Customer object to the fields search_customers surfaces."""
    return {
        "id": cust.get("Id"),
        "display_name": cust.get("DisplayName"),
        "company_name": cust.get("CompanyName"),
        "email": (cust.get("PrimaryEmailAddr") or {}).get("Address"),
        "balance": cust.get("Balance"),
    }
