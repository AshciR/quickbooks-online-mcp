"""Item-catalog tools, mounted onto the root server (see `invoices/tools.py` for the pattern)."""
from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from ..shared import _format_error, _qbo
from .service import ItemService

items = FastMCP(name="items")


@items.tool(
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
        async with _qbo() as client:
            results = await ItemService(client).list_items(name)
        return [_fmt_item(i) for i in results]
    except Exception as exc:  # noqa: BLE001 — tools must never leak tracebacks
        return _format_error(exc)


# --- helpers ---------------------------------------------------------------


def _fmt_item(item: dict[str, Any]) -> dict[str, Any]:
    """Trim a raw QBO Item object to the fields list_items surfaces."""
    return {
        "id": item.get("Id"),
        "name": item.get("Name"),
        "type": item.get("Type"),
        "description": item.get("Description"),
        "unit_price": item.get("UnitPrice"),
    }
