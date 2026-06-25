import os
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Which dotenv file to load. Defaults to .env; set ENV_FILE=.env.prod to point
# the same code at a production company without editing .env.
ENV_FILE = os.environ.get("ENV_FILE", ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    intuit_client_id: str = Field(alias="INTUIT_CLIENT_ID")
    intuit_client_secret: str = Field(alias="INTUIT_CLIENT_SECRET")
    qbo_realm_id: str = Field(alias="QBO_REALM_ID")
    qbo_environment: Literal["sandbox", "production"] = Field(alias="QBO_ENVIRONMENT")
    upstash_redis_rest_url: str = Field(alias="UPSTASH_REDIS_REST_URL")
    upstash_redis_rest_token: str = Field(alias="UPSTASH_REDIS_REST_TOKEN")

    # Inbound MCP-client auth. "bearer" gates with a shared static token (local dev,
    # header-capable clients); "oauth" runs a WorkOS AuthKit provider (Claude Desktop,
    # which only speaks OAuth). The three fields below are each required only in their
    # mode — build_auth() fails fast if the active mode's config is missing.
    mcp_auth_mode: Literal["bearer", "oauth"] = Field(default="bearer", alias="MCP_AUTH_MODE")
    mcp_bearer_token: str | None = Field(default=None, alias="MCP_BEARER_TOKEN")
    authkit_domain: str | None = Field(default=None, alias="AUTHKIT_DOMAIN")
    mcp_server_base_url: str | None = Field(default=None, alias="MCP_SERVER_BASE_URL")

    @property
    def qbo_base_url(self) -> str:
        if self.qbo_environment == "production":
            return "https://quickbooks.api.intuit.com"
        return "https://sandbox-quickbooks.api.intuit.com"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
