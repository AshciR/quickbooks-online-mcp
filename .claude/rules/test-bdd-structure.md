---
paths:
  - "tests/**/*.py"
---

# Test files: BDD Given / When / Then

Every test function in `tests/` MUST be structured into three phases marked with explicit comments, in this order:

- `# Given` — fixtures, mocks, inputs, and any state the system-under-test depends on.
- `# When` — the single action being exercised (usually one call to the SUT).
- `# Then` — assertions about the outcome (return value, raised exception, recorded side effects).

Rules:

- All three labels are required in every test, including parametrized tests and one-liners.
- If a phase has no setup, still write the label with a short reason, e.g. `# Given: no preconditions`.
- Do not collapse phases or drop labels because the test is short — the comments are the rule, not the structure they document.
- When `When` and `Then` are inseparable (e.g. `pytest.raises` wrapping the call), place the two comments back-to-back above the `with` block; do not split the `with` statement.

Example:

```python
async def test_429_raises_rate_limit_error(http, httpx_mock):
    # Given a fresh token and the QBO API returning 429
    store = InMemoryTokenStore(_fresh_bundle(), _settings(), http)
    client = QBOClient(_settings(), store, http)
    httpx_mock.add_response(url=_company_url() + "?minorversion=75", status_code=429, json={})

    # When the client reads CompanyInfo
    # Then it raises QBORateLimitError
    with pytest.raises(QBORateLimitError):
        await client.read("companyinfo", "9999")
```

# Testing the FastMCP server (`tests/test_server.py`)

Drive tools through FastMCP's in-memory client rather than over HTTP:

```python
from fastmcp import Client

async with Client(mcp) as c:
    result = await c.call_tool("get_invoice", {"invoice_id": "130"})
```

Gotchas, in order of how much time they cost:

- **The in-memory transport bypasses the server's bearer auth** — and actively rejects an
  `auth=` arg (`ValueError: This transport does not support auth`). So construct the client as
  plain `Client(mcp)`; the `StaticTokenVerifier`/`MCP_BEARER_TOKEN` check only applies over the
  HTTP transport, which isn't worth standing up in unit tests. (Note: this contradicts what the
  FastMCP testing docs *say* — verify FastMCP behavior by running it, not from the docs prose.)
- **Never open the `Client` inside a pytest fixture** — it causes hard-to-diagnose event-loop
  errors. Open it inside each test (a plain helper function called from the test is fine).
- **Read structured returns from `result.data`** (not `result.content`).
- `@mcp.custom_route` endpoints like `/health` sit outside the MCP machinery — test them with
  `httpx.ASGITransport(app=mcp.http_app())`, asserting status and body directly.
- The server builds its own `TokenStore`/`httpx.AsyncClient` per call, so mock at the HTTP
  boundary with `pytest-httpx`: stub Upstash `GET /get/qbo:tokens` → a non-expired bundle and
  the QBO endpoint the tool hits. (Same boundary the client tests mock.)
- **Tools return readable error strings instead of raising** (auth-expired, faults, bad input),
  so assert on the returned string — `pytest.raises` is wrong for the tool layer.
