# Multi-stage build for the QBO MCP server.
#
# Stage 1 installs runtime deps + the package into /app/.venv with uv (frozen from
# uv.lock, no dev deps). Stage 2 copies that venv onto a clean slim base and runs as a
# non-root user. Config is supplied at runtime via env vars — no .env* is ever copied in
# (the .dockerignore also excludes them). Render injects $PORT; the server reads it.

# ---- builder ----
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Install dependencies first (cached independently of source churn). README.md is needed
# because pyproject sets `readme = "README.md"` and hatchling reads it when resolving the
# project metadata.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Install the project itself.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---- runtime ----
FROM python:3.12-slim AS runtime

RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/src /app/src

ENV PATH="/app/.venv/bin:$PATH" \
    PORT=8080 \
    PYTHONUNBUFFERED=1

EXPOSE 8080
USER app

# Python-based probe (no curl needed on the slim image) against the unauthenticated
# /health route the server serves on $PORT.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",8080)}/health').status==200 else 1)"

CMD ["python", "-m", "qbo_mcp.server"]
