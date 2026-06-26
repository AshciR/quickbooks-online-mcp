# MCP tools: explicit `@mcp.tool(...)` decorator arguments

Every FastMCP tool in `src/qbo_mcp/<entity>/tools.py` (the per-domain sub-servers
mounted by `src/qbo_mcp/server.py`) MUST be registered with **explicit
decorator arguments** — never the bare `@mcp.tool` form. Pass, at minimum, an
explicit `name`, `description`, and `tags`; add `meta` where useful.

```python
@mcp.tool(
    name="search_customers",                 # explicit tool name shown to the LLM
    description="Find QuickBooks customers by (partial) display name.",  # what the LLM sees
    tags={"customers", "read"},               # organization / filtering
    meta={"version": "1.0"},                  # optional custom metadata
)
async def search_customers(name: str) -> list[dict[str, Any]] | str:
    """Internal docstring — ignored by the LLM when `description` is set above.

    Keep the full workflow guidance in `description`; this docstring documents the
    function for human readers of the code.
    """
    ...
```

Rules:

- **`description` is the LLM-facing contract.** Put the workflow guidance there
  (e.g. "`customer_id` comes from `search_customers`", "confirm line items before
  `create_invoice` writes"). FastMCP ignores the function docstring once
  `description` is provided, so don't rely on the docstring to teach the LLM.
- **`name`** is explicit even when it matches the function name — so a rename of the
  Python function can't silently change the tool name clients depend on.
- **`tags`** is a set; use `"read"` for read-only tools and `"write"` for mutating
  ones (e.g. `create_invoice`), plus an entity tag (`"customers"`, `"items"`,
  `"invoices"`).
- **`meta`** is optional free-form metadata (e.g. `{"version": ...}`).
- The function docstring may stay for human readers but is not the source of truth
  for the LLM — keep the two consistent.
