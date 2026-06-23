"""Invoice tools, mounted onto the root server.

A standalone `FastMCP` sub-server (the FastMCP analog of a FastAPI router); the
root server in `server.py` mounts it with no namespace so the tool names stay
unprefixed.
"""
from __future__ import annotations

from typing import Any, Literal

from fastmcp import FastMCP

from ..service import LineInput
from .shared import _format_error, _qbo

INVOICE_DEEP_LINK = "https://app.qbo.intuit.com/app/invoice?txnId={id}"

invoices = FastMCP(name="invoices")


@invoices.tool(
    name="get_invoice",
    description=(
        "Retrieve the full detail of a single QuickBooks invoice by its human-facing "
        "document number (e.g. \"1010\" printed on the invoice) — NOT QuickBooks' internal "
        "Id. Returns the header fields, every line item (description, quantity, unit price, "
        "amount), the subtotal/tax/total/balance (subtotal + tax == total; tax is "
        "TxnTaxDetail.TotalTax, 0 when untaxed), the email-delivery status, and a deep_link "
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


@invoices.tool(
    name="get_invoices",
    description=(
        "List a customer's invoices, most recent first (up to 50). customer_id is "
        "QuickBooks' internal Customer Id — get it from search_customers, not a name. "
        "Optional status filters to 'open' (balance still owed) or 'paid' (fully paid); "
        "default 'all'. Optional from_date/to_date (ISO YYYY-MM-DD) bound the invoice date "
        "inclusively. Each result has doc_number, id, txn_date, due_date, total, tax, balance, "
        "and a one-line summary of its line items; call get_invoice with a doc_number for "
        "full detail. On failure returns a human-readable error string."
    ),
    tags={"invoices", "read"},
)
async def get_invoices(
    customer_id: str,
    status: Literal["all", "open", "paid"] = "all",
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict[str, Any]] | str:
    """List a customer's invoices with optional status/date filters (see decorator `description`)."""
    try:
        async with _qbo() as service:
            results = await service.get_invoices(customer_id, from_date, to_date)
        return [_fmt_invoice_summary(inv) for inv in results if _matches_status(inv, status)]
    except Exception as exc:  # noqa: BLE001 — tools must never leak tracebacks
        return _format_error(exc)


@invoices.tool(
    name="create_invoice",
    description=(
        "Create a new invoice in QuickBooks for a customer. customer_id is QuickBooks' "
        "internal Customer Id from search_customers; each line's item_id comes from "
        "list_items. Per line: quantity (default 1), optional unit_price (omit to use the "
        "item's catalog price), and optional description. Optional due_date (ISO YYYY-MM-DD) "
        "and customer_memo. IMPORTANT: this WRITES to QuickBooks — before calling it, show "
        "the user the customer, every line item, quantity, and price, and get explicit "
        "confirmation. Returns the created invoice's id, doc_number, total, and a deep_link "
        "to open it. On failure returns a human-readable error string."
    ),
    tags={"invoices", "write"},
)
async def create_invoice(
    customer_id: str,
    lines: list[LineInput],
    due_date: str | None = None,
    customer_memo: str | None = None,
) -> dict[str, Any] | str:
    """Create an invoice from confirmed line items (see decorator `description`)."""
    try:
        async with _qbo() as service:
            invoice = await service.create_invoice(customer_id, lines, due_date, customer_memo)
        invoice_id = invoice.get("Id", "")
        return {
            "id": invoice_id,
            "doc_number": invoice.get("DocNumber"),
            "total": invoice.get("TotalAmt"),
            "deep_link": INVOICE_DEEP_LINK.format(id=invoice_id),
        }
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
        "tax": _total_tax(inv),
        "total": inv.get("TotalAmt"),
        "balance": inv.get("Balance"),
        "email_status": inv.get("EmailStatus"),
        "deep_link": INVOICE_DEEP_LINK.format(id=invoice_id),
    }


def _fmt_invoice_summary(inv: dict[str, Any]) -> dict[str, Any]:
    """Reduce a raw QBO Invoice to the list-row shape get_invoices returns.

    Lighter than `_fmt_invoice`: header fields plus a one-line `summary` joining the
    line-item descriptions (via `_line_descriptions`). Use get_invoice for full lines.
    """
    descriptions = _line_descriptions(inv)
    return {
        "doc_number": inv.get("DocNumber"),
        "id": inv.get("Id"),
        "txn_date": inv.get("TxnDate"),
        "due_date": inv.get("DueDate"),
        "total": inv.get("TotalAmt"),
        "tax": _total_tax(inv),
        "balance": inv.get("Balance"),
        "summary": "; ".join(descriptions),
    }


def _line_descriptions(inv: dict[str, Any]) -> list[str]:
    """Ordered, non-empty descriptions of an invoice's item/text lines.

    Walks the same `SalesItemLineDetail` / `DescriptionOnly` lines `_fmt_invoice`
    surfaces, skipping QBO's computed SubTotal/Discount lines and blank descriptions.
    """
    out: list[str] = []
    for line in inv.get("Line", []):
        if line.get("DetailType") in ("SalesItemLineDetail", "DescriptionOnly"):
            desc = line.get("Description")
            if desc:
                out.append(desc)
    return out


def _total_tax(inv: dict[str, Any]) -> float:
    """TxnTaxDetail.TotalTax, or 0 when the invoice carries no tax.

    Sales tax lives outside the `Line` array, so the sum of line `amount`s is the
    pre-tax subtotal; exposing this lets a consumer reconcile subtotal + tax == total.
    """
    return (inv.get("TxnTaxDetail") or {}).get("TotalTax") or 0


def _matches_status(inv: dict[str, Any], status: Literal["all", "open", "paid"]) -> bool:
    """Filter on QBO's own Balance: open == balance owed (>0), paid == settled (0)."""
    if status == "all":
        return True
    balance = inv.get("Balance") or 0
    return balance > 0 if status == "open" else balance == 0
