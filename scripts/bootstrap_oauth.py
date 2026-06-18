"""One-time local OAuth flow for QuickBooks Online.

Starts a localhost listener on :8000, opens Intuit's consent page in the
browser, captures the callback, exchanges the code for tokens, and
persists the bundle to Upstash. Prints the realmId for .env.
"""
from __future__ import annotations

import asyncio
import sys
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from dotenv import load_dotenv

sys.path.insert(0, "src")

from qbo_mcp.config import ENV_FILE, Settings  # noqa: E402
from qbo_mcp.token_store import TokenBundle, TokenStore  # noqa: E402

REDIRECT_URI = "http://localhost:8000/callback"
AUTHORIZE_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
SCOPE = "com.intuit.quickbooks.accounting"


def main() -> None:
    load_dotenv(ENV_FILE)
    settings = Settings()  # type: ignore[call-arg]
    state = uuid.uuid4().hex
    authorize_url = f"{AUTHORIZE_URL}?" + urlencode(
        {
            "client_id": settings.intuit_client_id,
            "response_type": "code",
            "scope": SCOPE,
            "redirect_uri": REDIRECT_URI,
            "state": state,
        }
    )
    print(f"Opening browser for QBO consent...\n  {authorize_url}\n")
    webbrowser.open(authorize_url)
    params = _wait_for_callback(state)
    asyncio.run(_exchange_and_store(settings, params["code"]))
    realm = params["realmId"]
    print("Token bundle saved to Upstash.")
    print(f"realmId={realm} — set QBO_REALM_ID={realm} in .env")


# --- helpers ---------------------------------------------------------------


class _CallbackHandler(BaseHTTPRequestHandler):
    captured: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        _CallbackHandler.captured = params
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            b"<html><body><h2>QBO auth complete.</h2>"
            b"<p>You can close this tab.</p></body></html>"
        )

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass


def _wait_for_callback(state: str) -> dict[str, str]:
    server = HTTPServer(("localhost", 8000), _CallbackHandler)
    try:
        while not _CallbackHandler.captured:
            server.handle_request()
    finally:
        server.server_close()
    params = _CallbackHandler.captured
    if params.get("state") != state:
        raise RuntimeError("OAuth state mismatch — aborting")
    if "code" not in params or "realmId" not in params:
        raise RuntimeError(f"Callback missing required fields: {params}")
    return params


async def _exchange_and_store(settings: Settings, code: str) -> TokenBundle:
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(
            TOKEN_URL,
            auth=(settings.intuit_client_id, settings.intuit_client_secret),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
            },
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        body = resp.json()
        bundle = TokenBundle(
            access_token=body["access_token"],
            refresh_token=body["refresh_token"],
            access_expires_at=int(time.time()) + int(body["expires_in"]),
        )
        store = TokenStore(settings, http)
        await store.save(bundle)
        return bundle


if __name__ == "__main__":
    main()
