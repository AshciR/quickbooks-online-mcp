"""FastMCP server exposing QuickBooks Online tools over streamable HTTP.

Runs on `0.0.0.0:$PORT` (default 8080). MCP clients authenticate with a static
bearer token (`MCP_BEARER_TOKEN`) via `Authorization: Bearer <token>`. A plain,
unauthenticated `GET /health` route returns ``ok`` for Render health checks.

The tools live in per-entity packages under `qbo_mcp` (`customers`, `invoices`,
`items`), each pairing its `tools.py` sub-server (the FastMCP analog of a FastAPI
router) with its `service.py`. The sub-servers are `mount`ed here with no namespace,
so their names stay unprefixed (`get_invoice`, `search_customers`, …). The root
server owns auth, `/health`, and the process entrypoint.

Local testing
-------------
Start the server::

    uv run python -m qbo_mcp.server

Inspect it with the MCP Inspector (https://github.com/modelcontextprotocol/inspector)::

    npx @modelcontextprotocol/inspector

  → Transport: "Streamable HTTP"
  → URL: http://localhost:8080/mcp
  → Header: Authorization: Bearer <MCP_BEARER_TOKEN>

Or wire it into Claude Code::

    claude mcp add --transport http qbo http://localhost:8080/mcp \\
      --header "Authorization: Bearer <MCP_BEARER_TOKEN>"
"""
from __future__ import annotations

import importlib.resources
import os

from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.server.auth.providers.workos import AuthKitProvider
from mcp.types import Icon
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from .config import Settings, get_settings
from .customers.tools import customers
from .invoices.tools import invoices
from .items.tools import items

DEFAULT_PORT = 8080

# The connector logo, advertised to MCP clients and served over /icon.png. Packaged
# under qbo_mcp/assets so importlib.resources finds it at runtime (see pyproject
# force-include). 512x512 RGBA PNG.
ICON_BYTES = (importlib.resources.files("qbo_mcp") / "assets" / "icon.png").read_bytes()
ICON_SIZES = ["512x512"]


def build_auth(settings: Settings) -> AuthProvider:
    """Construct the inbound MCP-client auth provider for the configured mode.

    `bearer` (default) gates with the shared static `MCP_BEARER_TOKEN` — fine for
    local dev and header-capable clients. `oauth` runs a WorkOS AuthKit provider so
    Claude Desktop (which only speaks OAuth) can connect via DCR against an invite-only
    directory. Each mode fails fast here if its required config is absent, rather than
    booting a server with no/partial auth.
    """
    if settings.mcp_auth_mode == "oauth":
        if not settings.authkit_domain or not settings.mcp_server_base_url:
            raise ValueError(
                "MCP_AUTH_MODE=oauth requires AUTHKIT_DOMAIN and MCP_SERVER_BASE_URL"
            )
        return AuthKitProvider(
            authkit_domain=settings.authkit_domain,
            base_url=settings.mcp_server_base_url,
        )

    if not settings.mcp_bearer_token:
        raise ValueError("MCP_AUTH_MODE=bearer requires MCP_BEARER_TOKEN")
    return StaticTokenVerifier(
        tokens={settings.mcp_bearer_token: {"client_id": "qbo-mcp", "scopes": []}}
    )


def build_icons(settings: Settings) -> list[Icon] | None:
    """Advertise the connector logo as an absolute URL, or `None` when unavailable.

    The advertised `src` must be an absolute URL the client can fetch, so it's only
    well-formed when `mcp_server_base_url` is set (oauth mode today; #9). In bearer
    mode that's typically unset, so we advertise no icon — the `/icon.png` route is
    still served, it just isn't pointed at from the `initialize` response. Returning
    `None` keeps bearer mode booting without `mcp_server_base_url`.
    """
    if not settings.mcp_server_base_url:
        return None
    return [
        Icon(
            src=f"{settings.mcp_server_base_url}/icon.png",
            mimeType="image/png",
            sizes=ICON_SIZES,
        )
    ]


_settings = get_settings()
mcp = FastMCP(
    name="quickbooks-online",
    auth=build_auth(_settings),
    icons=build_icons(_settings),
    website_url=_settings.mcp_server_base_url,
)

# Mount the per-entity tool sub-servers with no namespace (tool names stay unprefixed).
# The root server's bearer auth governs every mounted tool.
mcp.mount(invoices)
mcp.mount(customers)
mcp.mount(items)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> PlainTextResponse:
    """Unauthenticated liveness probe for Render. Returns 200 ``ok``."""
    return PlainTextResponse("ok")


@mcp.custom_route("/icon.png", methods=["GET"])
async def icon(request: Request) -> Response:
    """Unauthenticated connector logo (like `/health`). The URL advertised in the
    `initialize` response's `serverInfo.icons` points here so MCP clients (e.g.
    Claude Desktop) can fetch it without a token."""
    return Response(ICON_BYTES, media_type="image/png")


def main() -> None:
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    mcp.run(transport="http", host="0.0.0.0", port=port)


# The __main__ guard MUST be the last statement in the file: running as a module
# invokes main() here, and mcp.run() blocks — any definitions below would never execute.
if __name__ == "__main__":
    main()
