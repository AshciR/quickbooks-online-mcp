#!/usr/bin/env bash
# Start the QBO FastMCP server (streamable HTTP).
#
# Listens on 0.0.0.0:$PORT (default 8080); clients authenticate with the
# MCP_BEARER_TOKEN bearer. Env is read by pydantic-settings from the dotenv
# file named by ENV_FILE (default .env); set ENV_FILE=.env.prod to run against
# the production company.
#
#   ./scripts/start_server.sh                    # .env, port 8080
#   ENV_FILE=.env.prod ./scripts/start_server.sh # production
#   PORT=9000 ./scripts/start_server.sh
set -euo pipefail

cd "$(dirname "$0")/.."
export ENV_FILE="${ENV_FILE:-.env}"
exec uv run python -m qbo_mcp.server
