#!/usr/bin/env bash
# Start the QBO FastMCP server (streamable HTTP).
#
# Listens on 0.0.0.0:$PORT (default 8080); clients authenticate with the
# MCP_BEARER_TOKEN bearer. Env is read from .env by pydantic-settings.
#
#   ./scripts/start_server.sh          # port 8080
#   PORT=9000 ./scripts/start_server.sh
set -euo pipefail

cd "$(dirname "$0")/.."
exec uv run python -m qbo_mcp.server
