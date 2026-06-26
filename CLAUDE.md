# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Dependency management is via `uv` (Python 3.12, `src` layout).

```bash
uv sync                                  # install runtime + dev deps
uv run pytest -q                         # full test suite
uv run pytest tests/test_qbo_client.py::test_429_raises_rate_limit_error
uv run python scripts/bootstrap_oauth.py # one-time QBO OAuth handshake (writes tokens to Upstash)
uv run python scripts/smoke_test.py      # forces a refresh + reads CompanyInfo
```

There is no MCP server entrypoint yet — see "Scope" below.

## Scope of this repo

The README describes a FastMCP server, but the server itself is **deliberately not yet implemented**. What exists today is the auth + API client foundation that an MCP server will sit on top of. When adding the FastMCP layer, fetch current docs from `https://gofastmcp.com/llms.txt` first — the FastMCP API has changed across versions, don't rely on memorized patterns.

## Development workflow

When introducing a new idea or pattern in this codebase (a new layer, integration, or
class of feature), build the **thinnest end-to-end vertical slice first** and sanity-check
it — its test plus a live run — *before* writing the rest of the feature. Prove the whole
path through one representative case so the wiring, auth, error handling, and test harness
are all confirmed; only then fan out to the remaining cases, which become mechanical repeats
of the proven pattern. Example: the FastMCP server landed `get_invoice` (read-only, safe to
run live) end-to-end first, then the other four tools followed.

## File layout rule

When creating or editing a Python file, order definitions top-down: the **primary / public function (or class)** comes first, immediately under the imports, so a reader sees the entrypoint without scrolling. **Helper functions, private utilities, and pytest fixtures go at the bottom** of the file. This applies to source modules and test modules alike — e.g. in a test file, the `test_*` functions come first and the `_fresh_bundle()` / `_company_url()` / `@pytest.fixture` helpers come last. Module-level constants stay near the top (under imports, above the primary definition).

## Architecture

The modules in `src/qbo_mcp/` form a layered stack — `config` → `token_store` → `qbo_client` (generic transport) → per-domain `<entity>/service.py` (business logic) → per-domain `<entity>/tools.py` (MCP tools) → `server` (mounts the tools) — and understanding their contract matters because the invariants are easy to break. Each entity owns a self-contained package (`customers/`, `invoices/`, `items/`) pairing its `service.py` with its `tools.py`; the domain-agnostic plumbing (`_qbo` client builder, `_format_error`) lives in `shared.py`:

**`config.py`** — `pydantic_settings.BaseSettings` reads 7 env vars from `.env`. `Settings.qbo_base_url` switches sandbox vs production host. `get_settings()` is `lru_cache`d.

**`token_store.py`** — `TokenStore` persists a `TokenBundle(access_token, refresh_token, access_expires_at)` as a single JSON blob under Redis key `"qbo:tokens"` via Upstash's REST API (path-style `GET /get/<key>` and `POST /set/<key>/<urlencoded-value>`, authed with the Upstash bearer token). No Redis driver — just `httpx`.

**`qbo_client.py`** — `QBOClient` wraps every QBO API call with these non-negotiable behaviors:

- **Refresh-token rotation persistence.** Intuit rotates `refresh_token` on every refresh response and invalidates the old one. `_refresh()` MUST write the new bundle to `TokenStore` before returning. Skipping the save (or only saving when the value differs from a stale local copy) bricks the auth and forces a re-run of `bootstrap_oauth.py`. If you change `_refresh`, keep this contract.
- **`minorversion=75`** is merged into every request's query params inside `_request()`. Don't add it per-call.
- **Auto-refresh window** of 300 s (`REFRESH_SKEW_SECONDS`) on top of one-shot 401-retry. A 401 forces one refresh + one retry; a second 401 propagates as `httpx.HTTPStatusError`.
- **Error taxonomy:** 429 → `QBORateLimitError`; QBO `Fault` body (in 4xx OR 200) → `QBOFaultError(message, detail)`; missing tokens OR `invalid_grant` on refresh → `QBOAuthExpiredError` with the fixed message `"QBO authorization expired — re-run scripts/bootstrap_oauth.py"`.
- **`create()` sends `Request-Id: uuid4().hex`** so retries can't double-post transactions. Any new mutating helper must do the same.
- **`query(sql)` is a generic transport primitive** — the only entrypoint that takes raw QBO SQL. It does no validation or escaping itself. Building SQL from caller input is the job of the per-domain service layer (`<entity>/service.py`), not the transport: see below.

**`<entity>/service.py`** — one service class per domain (`CustomerService`, `InvoiceService`, `ItemService`), each `__init__(self, client: QBOClient)`, holding only that entity's business-level operations (e.g. `InvoiceService.find_invoice_by_doc_number`/`get_invoices`/`create_invoice`, `CustomerService.search_customers`, `ItemService.list_items`). `LineInput` lives in `invoices/service.py`. Each composes the generic `QBOClient` primitives (`read`, `create`, `query`) and is the seam its tools call — a tool wraps the `_qbo()`-yielded `QBOClient` in its domain service (e.g. `CustomerService(client)`). The validation contract lives here: any method building SQL from caller input MUST validate ids/dates and escape free text first, using `validate_id` (`^\d+$`), `validate_date` (`date.fromisoformat`), and `escape_qbo_string` (doubles single quotes) from `qbo_client`. Tools call these typed service methods — never `client.query` directly. (`InvoiceService._build_line_entry` reads an Item via the generic `client.read("item", …)`, not `ItemService` — a cross-entity transport read, not a service dependency.)

`scripts/bootstrap_oauth.py` runs the one-time auth-code flow on `localhost:8000/callback`, exchanges the code, writes the bundle to Upstash, and prints the `realmId` to put in `.env`. `scripts/smoke_test.py` deliberately calls `_refresh` before the read to exercise rotation end-to-end.

## Testing

`tests/conftest.py` seeds the 7 env vars before `Settings()` is constructed anywhere, and provides the shared service-test fixtures: an `http` `AsyncClient` and a `qbo_client` (a `QBOClient` over an in-memory, fresh-token `InMemoryTokenStore`). The tests mirror the source layout: `tests/<entity>/test_service.py` constructs its service over that `qbo_client` fixture (e.g. `CustomerService(qbo_client)`), and `tests/<entity>/test_tool.py` drives the mounted tools through FastMCP's in-memory client using the shared `_client`/`_call_tool`/`_mock_token` helpers in `conftest.py` (imported via `from tests.conftest import ...`). `tests/test_server.py` holds only the server-level tests (`build_auth`, `/health`). `tests/test_qbo_client.py` uses `pytest-httpx` to mock both Intuit's token endpoint and the QBO API, with its own `InMemoryTokenStore` subclass (so it can assert on persisted bundles without hitting Upstash). `pyproject.toml` sets `asyncio_mode = "auto"` and `pythonpath = ["src"]`. The test suite specifically covers the invariants listed above — when changing client behavior, expect to update the matching test, not delete it.
