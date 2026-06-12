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
