# QuickBooks Online MCP

Foundation for a FastMCP server that exposes QuickBooks Online operations.
This repository currently provides the auth + API client groundwork; the
FastMCP server wiring will be layered on top in a later step.

## What's here

- `src/qbo_mcp/config.py` — typed env-var settings.
- `src/qbo_mcp/token_store.py` — Upstash Redis REST persistence for the
  OAuth token bundle.
- `src/qbo_mcp/qbo_client.py` — async QBO API client with auto-refresh,
  refresh-token rotation, retry-on-401, rate-limit + Fault handling, and
  strict parameter validation.
- `scripts/bootstrap_oauth.py` — one-time OAuth flow.
- `scripts/smoke_test.py` — verifies tokens + connectivity.

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

## Tests

```bash
uv run pytest -q
```

## Project layout

```
src/qbo_mcp/        # library code
scripts/            # bootstrap + smoke test
tests/              # pytest suite (mocked httpx)
```
