"""Shared plumbing for the mounted tool sub-servers.

Every per-entity tool module (`invoices`, `customers`, `items`) builds a
`QBOService` the same way and turns QBO exceptions into readable strings the same
way. Those two concerns live here so the tool modules only hold entity logic.
"""
from __future__ import annotations

import contextlib
from typing import AsyncIterator

import httpx

from ..config import get_settings
from ..qbo_client import (
    QBOAuthExpiredError,
    QBOClient,
    QBOFaultError,
    QBORateLimitError,
)
from ..service import QBOService
from ..token_store import TokenStore


@contextlib.asynccontextmanager
async def _qbo() -> AsyncIterator[QBOService]:
    """Yield a QBOService over a QBOClient backed by a fresh httpx client.

    Mirrors smoke_test.py's wiring; the service is the seam every tool calls.
    """
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30.0) as http:
        store = TokenStore(settings, http)
        yield QBOService(QBOClient(settings, store, http))


def _format_error(exc: Exception) -> str:
    """Map a QBO client exception to a human-readable tool result string."""
    if isinstance(exc, QBOAuthExpiredError):
        return str(exc)
    if isinstance(exc, QBORateLimitError):
        return "QuickBooks rate limit exceeded — please wait a moment and try again."
    if isinstance(exc, QBOFaultError):
        return f"QuickBooks error: {exc.message} ({exc.detail})"
    if isinstance(exc, ValueError):
        return f"Invalid input: {exc}"
    return f"Unexpected error talking to QuickBooks: {exc}"
