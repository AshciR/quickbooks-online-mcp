from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.server.auth.providers.workos import AuthKitProvider

from qbo_mcp.config import Settings, get_settings
from qbo_mcp.server import build_auth, mcp


class TestBuildAuth:
    def test_bearer_mode_returns_static_token_verifier(self) -> None:
        # Given settings in the default bearer mode with a token set
        settings = _auth_settings(mcp_auth_mode="bearer", mcp_bearer_token="secret")

        # When the auth provider is built
        auth = build_auth(settings)

        # Then it's a StaticTokenVerifier that accepts that exact token
        assert isinstance(auth, StaticTokenVerifier)
        assert "secret" in auth.tokens

    def test_bearer_mode_without_token_fails_fast(self) -> None:
        # Given bearer mode but no MCP_BEARER_TOKEN configured
        settings = _auth_settings(mcp_auth_mode="bearer", mcp_bearer_token=None)

        # When the auth provider is built
        # Then it raises rather than booting an unauthenticated server
        with pytest.raises(ValueError, match="MCP_BEARER_TOKEN"):
            build_auth(settings)

    def test_oauth_mode_returns_authkit_provider(self) -> None:
        # Given oauth mode with both AuthKit settings present
        settings = _auth_settings(
            mcp_auth_mode="oauth",
            authkit_domain="https://example.authkit.app",
            mcp_server_base_url="https://app.onrender.com",
        )

        # When the auth provider is built
        auth = build_auth(settings)

        # Then it's a WorkOS AuthKit provider (serves OAuth metadata, not auth=None)
        # wired to the configured AuthKit domain and public base URL (the latter
        # normalized to a trailing-slash AnyHttpUrl by the provider).
        assert isinstance(auth, AuthKitProvider)
        assert auth.authkit_domain == "https://example.authkit.app"
        assert str(auth.base_url) == "https://app.onrender.com/"

    def test_oauth_mode_missing_config_fails_fast(self) -> None:
        # Given oauth mode with the AuthKit domain absent
        settings = _auth_settings(
            mcp_auth_mode="oauth",
            authkit_domain=None,
            mcp_server_base_url="https://app.onrender.com",
        )

        # When the auth provider is built
        # Then it raises naming the required oauth config
        with pytest.raises(ValueError, match="AUTHKIT_DOMAIN and MCP_SERVER_BASE_URL"):
            build_auth(settings)


async def test_health_route_returns_ok_unauthenticated() -> None:
    # Given the server's ASGI app and no Authorization header
    transport = httpx.ASGITransport(app=mcp.http_app())

    # When GET /health is requested
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")

    # Then it responds 200 with a plain "ok" body
    assert resp.status_code == 200
    assert resp.text == "ok"


# --- helpers and fixtures --------------------------------------------------


def _auth_settings(**overrides: Any) -> Settings:
    # Start from the conftest-seeded settings and override only the auth fields under
    # test. model_copy(update=...) takes field names and skips re-validation, so we can
    # set mcp_bearer_token=None (which the env-loaded Settings would otherwise carry).
    return get_settings().model_copy(update=overrides)
