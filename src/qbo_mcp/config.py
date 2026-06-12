from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    intuit_client_id: str = Field(alias="INTUIT_CLIENT_ID")
    intuit_client_secret: str = Field(alias="INTUIT_CLIENT_SECRET")
    qbo_realm_id: str = Field(alias="QBO_REALM_ID")
    qbo_environment: Literal["sandbox", "production"] = Field(alias="QBO_ENVIRONMENT")
    upstash_redis_rest_url: str = Field(alias="UPSTASH_REDIS_REST_URL")
    upstash_redis_rest_token: str = Field(alias="UPSTASH_REDIS_REST_TOKEN")
    mcp_bearer_token: str = Field(alias="MCP_BEARER_TOKEN")

    @property
    def qbo_base_url(self) -> str:
        if self.qbo_environment == "production":
            return "https://quickbooks.api.intuit.com"
        return "https://sandbox-quickbooks.api.intuit.com"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
