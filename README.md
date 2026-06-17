# QuickBooks Online MCP

A FastMCP server that exposes QuickBooks Online operations (read invoices,
look up customers/items, and create invoices) over streamable HTTP with static
bearer-token auth.

## What's here

- `src/qbo_mcp/config.py` — typed env-var settings.
- `src/qbo_mcp/token_store.py` — Upstash Redis REST persistence for the
  OAuth token bundle.
- `src/qbo_mcp/qbo_client.py` — async QBO API client (generic transport) with
  auto-refresh, refresh-token rotation, retry-on-401, rate-limit + Fault
  handling, and the id/date/escape validators.
- `src/qbo_mcp/service.py` — `QBOService`, the business layer that builds
  validated/escaped queries and invoice payloads on top of the client.
- `src/qbo_mcp/server.py` — root FastMCP server (bearer auth + `/health`) that
  `mount`s the per-entity tool sub-servers in `src/qbo_mcp/tools/`.
- `scripts/bootstrap_oauth.py` — one-time OAuth flow.
- `scripts/smoke_test.py` — verifies tokens + connectivity.

## Tools

| Tool | Purpose |
| --- | --- |
| `search_customers(name)` | Find active customers by partial display name → `customer_id`. |
| `list_items(name?)` | List active catalog items (Service/NonInventory/Inventory) → `item_id` + unit price. |
| `get_invoices(customer_id, status?, from_date?, to_date?)` | A customer's invoices (status `all`/`open`/`paid`, optional ISO date range), newest first, with a one-line summary each. |
| `get_invoice(doc_number)` | Full detail of one invoice by its human-facing document number, with a deep link. |
| `create_invoice(customer_id, lines[], due_date?, customer_memo?)` | Create an invoice; each line's `unit_price` defaults to the item's catalog price. **Confirm details with the user first — this writes to QuickBooks.** |

Workflow the docstrings teach the LLM: `search_customers` → `list_items` →
confirm lines with the user → `create_invoice`.

## Prerequisites

- Python 3.12
- [uv](https://docs.astral.sh/uv/)

## 1. Create an Intuit developer app

1. Sign in at <https://developer.intuit.com> and create a new app under
   "QuickBooks Online and Payments".
2. Enable the **`com.intuit.quickbooks.accounting`** scope.
3. Under **Keys & OAuth**, copy the **Client ID** and **Client Secret**
   (use the Development keys for the sandbox).
4. Add this redirect URI: `http://localhost:8000/callback`.

## 2. Create an Upstash Redis database

1. Sign in at <https://console.upstash.com> and create a Redis database.
2. From the database's REST section, copy the **REST URL** and **REST
   Token**.

## 3. Configure environment

```bash
cp .env.example .env
# Fill in INTUIT_CLIENT_ID, INTUIT_CLIENT_SECRET, UPSTASH_REDIS_REST_URL,
# UPSTASH_REDIS_REST_TOKEN, MCP_BEARER_TOKEN.
# Leave QBO_REALM_ID blank — the bootstrap step prints it.
```

## 4. Install dependencies

```bash
uv sync
```

## 5. Authorize against QBO

```bash
uv run python scripts/bootstrap_oauth.py
```

A browser tab opens for Intuit consent. When it completes, the script
prints something like:

```
realmId=9341454... — set QBO_REALM_ID=9341454... in .env
```

Paste that value into `.env`.

## 6. Smoke test

```bash
uv run python scripts/smoke_test.py
```

Expected output: `Company: <your sandbox company name>`.

## 7. Run the MCP server

```bash
uv run python -m qbo_mcp.server   # serves on 0.0.0.0:$PORT (default 8080)
```

- MCP endpoint: `http://localhost:8080/mcp` (streamable HTTP), authenticated
  with `Authorization: Bearer <MCP_BEARER_TOKEN>`.
- Unauthenticated `GET /health` returns `ok` (for health checks).

Connect from Claude Code:

```bash
claude mcp add --transport http qbo http://localhost:8080/mcp \
  --header "Authorization: Bearer <MCP_BEARER_TOKEN>"
```

Or inspect it with the [MCP Inspector](https://github.com/modelcontextprotocol/inspector)
(`npx @modelcontextprotocol/inspector`) → Transport "Streamable HTTP" → URL
`http://localhost:8080/mcp` → header `Authorization: Bearer <MCP_BEARER_TOKEN>`.

## Tests

```bash
uv run pytest -q
```

## Project layout

```
src/qbo_mcp/        # config, token_store, qbo_client, service, server
src/qbo_mcp/tools/  # per-entity FastMCP sub-servers mounted by server.py
scripts/            # bootstrap + smoke test
tests/              # pytest suite (mocked httpx)
```
