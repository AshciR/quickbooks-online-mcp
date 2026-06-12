"""QBO smoke test: force a token refresh and read CompanyInfo."""
from __future__ import annotations

import asyncio
import sys

import httpx
from dotenv import load_dotenv

sys.path.insert(0, "src")

from qbo_mcp.config import Settings  # noqa: E402
from qbo_mcp.qbo_client import QBOAuthExpiredError, QBOClient  # noqa: E402
from qbo_mcp.token_store import TokenStore  # noqa: E402


def main() -> None:
    load_dotenv()
    try:
        name = asyncio.run(_run())
    except QBOAuthExpiredError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"Smoke test failed: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Company: {name}")


# --- helpers ---------------------------------------------------------------


async def _run() -> str:
    settings = Settings()  # type: ignore[call-arg]
    async with httpx.AsyncClient(timeout=30.0) as http:
        store = TokenStore(settings, http)
        bundle = await store.load()
        if bundle is None:
            raise QBOAuthExpiredError()
        client = QBOClient(settings, store, http)
        await client._refresh(bundle)
        body = await client.read("companyinfo", settings.qbo_realm_id)
        return body.get("CompanyInfo", {}).get("CompanyName", "<unknown>")


if __name__ == "__main__":
    main()
