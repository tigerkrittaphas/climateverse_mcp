# Hosted ClimateVerse MCP server (streamable HTTP + Cognito OAuth).
FROM ghcr.io/astral-sh/uv:0.8-python3.12-bookworm-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first so code changes reuse the cached layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra aws --no-install-project
COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra aws --no-editable

FROM python:3.12-slim-bookworm
RUN useradd --system --uid 10001 mcp
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    CLIMATEVERSE_TRANSPORT=http \
    CLIMATEVERSE_HTTP_HOST=0.0.0.0 \
    CLIMATEVERSE_HTTP_PORT=8000 \
    FASTMCP_HOME=/tmp/fastmcp
USER mcp
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
CMD ["climateverse-mcp"]
