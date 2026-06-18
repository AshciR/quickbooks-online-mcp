#!/usr/bin/env bash
# One-time QBO OAuth handshake against the PRODUCTION company.
#
# Production Intuit keys reject localhost redirect URIs, so the callback goes
# through a public HTTPS ngrok tunnel that forwards to the local listener on
# :8000. Start the tunnel first, in another terminal:
#
#   ngrok http --url=https://sprinkled-wok-uncork.ngrok-free.dev 8000
#
# The /callback path must be registered in the Intuit app's Production redirect
# URIs and match OAUTH_REDIRECT_URI below exactly. After it prints realmId=...,
# put that value in .env.prod as QBO_REALM_ID.
set -euo pipefail

cd "$(dirname "$0")/.."

ENV_FILE=.env.prod \
OAUTH_REDIRECT_URI=https://sprinkled-wok-uncork.ngrok-free.dev/callback \
exec uv run python scripts/bootstrap_oauth.py
