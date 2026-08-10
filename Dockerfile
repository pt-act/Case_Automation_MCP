# Case Automation MCP Server — multi-stage Dockerfile
# Build: docker build -t case-automation-mcp .
# Run:   docker compose up

FROM python:3.12-slim AS base

# System deps for WeasyPrint (optional PDF engine) + LibreOffice (optional)
# libxml2, libpango, libcairo are WeasyPrint runtime deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 libpango-1.0-0 libpangocairo-1.0-0 libcairo2 \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

# Copy project files
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY alembic.ini ./
COPY docs/domain_model.md ./docs/domain_model.md

# Install dependencies (production + pdf extra)
RUN uv sync --frozen --extra pdf

# Install the project itself
RUN uv sync --frozen --extra pdf

# Default environment
ENV CAM_SECRET_BACKEND=env \
    CAM_PDF_ENGINE=auto \
    CAM_DOMAIN_PACK=immigration \
    PYTHONUNBUFFERED=1

# Expose the sidecar port
EXPOSE 8001

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')" || exit 1

# Default command — run the sidecar
# Override in docker-compose for worker and beat
CMD ["uv", "run", "uvicorn", "cam.sidecar.main:app", "--host", "0.0.0.0", "--port", "8001"]
