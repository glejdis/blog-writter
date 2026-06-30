# syntax=docker/dockerfile:1
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    BLOG_WRITER_UI_HOST=0.0.0.0 \
    BLOG_WRITER_UI_PORT=8000

# Build tools cover the few dependencies that may need to compile from source.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Project metadata first for better layer caching.
COPY pyproject.toml constraints.txt README.md ./

# Application source (editable install keeps PROJECT_ROOT == /app so the UI's
# static assets and drafts/ output resolve correctly).
COPY src ./src
COPY ui ./ui
COPY mcp_servers ./mcp_servers
COPY knowledge_base ./knowledge_base

# --pre is required: several deps (agent-framework-azure-ai, pydantic) ship as
# pre-releases only, matching the documented uv `--prerelease=allow` install.
RUN python -m pip install --upgrade pip \
    && python -m pip install --pre -e ".[ui]"

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["blog-writer", "ui", "--host", "0.0.0.0", "--port", "8000"]
