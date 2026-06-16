"""Invoice tools, mounted onto the root server.

A standalone `FastMCP` sub-server (the FastMCP analog of a FastAPI router); the
root server in `server.py` mounts it with no namespace so the tool names stay
unprefixed.
"""
from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from .shared import _format_error, _qbo

INVOICE_DEEP_LINK = "https://app.qbo.intuit.com/app/invoice?txnId={id}"

invoices = FastMCP(name="invoices")


@invoices.tool(
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


# --- helpers ---------------------------------------------------------------


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
