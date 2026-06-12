import os

# Provide stub env vars before Settings is constructed anywhere.
os.environ.setdefault("INTUIT_CLIENT_ID", "test-client")
os.environ.setdefault("INTUIT_CLIENT_SECRET", "test-secret")
os.environ.setdefault("QBO_REALM_ID", "9999")
os.environ.setdefault("QBO_ENVIRONMENT", "sandbox")
os.environ.setdefault("UPSTASH_REDIS_REST_URL", "https://upstash.test")
os.environ.setdefault("UPSTASH_REDIS_REST_TOKEN", "upstash-token")
os.environ.setdefault("MCP_BEARER_TOKEN", "mcp-token")
