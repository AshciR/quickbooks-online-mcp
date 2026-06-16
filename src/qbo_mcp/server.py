"""FastMCP server exposing QuickBooks Online tools over streamable HTTP.

Runs on `0.0.0.0:$PORT` (default 8080). MCP clients authenticate with a static
bearer token (`MCP_BEARER_TOKEN`) via `Authorization: Bearer <token>`. A plain,
unauthenticated `GET /health` route returns ``ok`` for Render health checks.

The tools live in per-entity sub-servers under `qbo_mcp.tools` (the FastMCP analog
of FastAPI routers) and are `mount`ed here with no namespace, so their names stay
unprefixed (`get_invoice`, `search_customers`, …). The root server owns auth,
`/health`, and the process entrypoint.

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

import os

from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from .config import get_settings
from .tools.customers import customers
from .tools.invoices import invoices
from .tools.items import items

DEFAULT_PORT = 8080

mcp = FastMCP(
    name="quickbooks-online",
    auth=StaticTokenVerifier(
        tokens={get_settings().mcp_bearer_token: {"client_id": "qbo-mcp", "scopes": []}}
    ),
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


def main() -> None:
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    mcp.run(transport="http", host="0.0.0.0", port=port)


# The __main__ guard MUST be the last statement in the file: running as a module
# invokes main() here, and mcp.run() blocks — any definitions below would never execute.
if __name__ == "__main__":
    main()
