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

## File layout rule

When creating or editing a Python file, order definitions top-down: the **primary / public function (or class)** comes first, immediately under the imports, so a reader sees the entrypoint without scrolling. **Helper functions, private utilities, and pytest fixtures go at the bottom** of the file. This applies to source modules and test modules alike — e.g. in a test file, the `test_*` functions come first and the `_fresh_bundle()` / `_company_url()` / `@pytest.fixture` helpers come last. Module-level constants stay near the top (under imports, above the primary definition).

## Architecture

Three modules in `src/qbo_mcp/` form a layered stack; understanding their contract matters because the invariants are easy to break:

**`config.py`** — `pydantic_settings.BaseSettings` reads 7 env vars from `.env`. `Settings.qbo_base_url` switches sandbox vs production host. `get_settings()` is `lru_cache`d.

**`token_store.py`** — `TokenStore` persists a `TokenBundle(access_token, refresh_token, access_expires_at)` as a single JSON blob under Redis key `"qbo:tokens"` via Upstash's REST API (path-style `GET /get/<key>` and `POST /set/<key>/<urlencoded-value>`, authed with the Upstash bearer token). No Redis driver — just `httpx`.

**`qbo_client.py`** — `QBOClient` wraps every QBO API call with these non-negotiable behaviors:

- **Refresh-token rotation persistence.** Intuit rotates `refresh_token` on every refresh response and invalidates the old one. `_refresh()` MUST write the new bundle to `TokenStore` before returning. Skipping the save (or only saving when the value differs from a stale local copy) bricks the auth and forces a re-run of `bootstrap_oauth.py`. If you change `_refresh`, keep this contract.
- **`minorversion=75`** is merged into every request's query params inside `_request()`. Don't add it per-call.
- **Auto-refresh window** of 300 s (`REFRESH_SKEW_SECONDS`) on top of one-shot 401-retry. A 401 forces one refresh + one retry; a second 401 propagates as `httpx.HTTPStatusError`.
- **Error taxonomy:** 429 → `QBORateLimitError`; QBO `Fault` body (in 4xx OR 200) → `QBOFaultError(message, detail)`; missing tokens OR `invalid_grant` on refresh → `QBOAuthExpiredError` with the fixed message `"QBO authorization expired — re-run scripts/bootstrap_oauth.py"`.
- **`create()` sends `Request-Id: uuid4().hex`** so retries can't double-post transactions. Any new mutating helper must do the same.
- **`_query(sql)` is intentionally private.** It is the only entrypoint that takes raw QBO SQL. Public methods build query strings from typed params using the three validators in this module: `validate_id` (`^\d+$`), `validate_date` (`date.fromisoformat`), and `escape_qbo_string` (doubles single quotes). Do not expose `_query` directly via any MCP tool — wrap it in a typed method that validates and escapes first.

`scripts/bootstrap_oauth.py` runs the one-time auth-code flow on `localhost:8000/callback`, exchanges the code, writes the bundle to Upstash, and prints the `realmId` to put in `.env`. `scripts/smoke_test.py` deliberately calls `_refresh` before the read to exercise rotation end-to-end.

## Testing

`tests/conftest.py` seeds the 7 env vars before `Settings()` is constructed anywhere. `tests/test_qbo_client.py` uses `pytest-httpx` to mock both Intuit's token endpoint and the QBO API, with an `InMemoryTokenStore` subclass (so it can assert on persisted bundles without hitting Upstash). `pyproject.toml` sets `asyncio_mode = "auto"` and `pythonpath = ["src"]`. The test suite specifically covers the invariants listed above — when changing client behavior, expect to update the matching test, not delete it.
