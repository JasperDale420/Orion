
# Use Python 3.12 slim bookworm image
FROM python:3.12-slim-bookworm

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT="/opt/pysetup/.venv" \
    VENV_PATH="/opt/pysetup/.venv"

# Prepend venv to path
ENV PATH="$VENV_PATH/bin:$PATH"

# Install system dependencies and uv
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
    curl \
    build-essential \
    libpq-dev \
    git \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir uv

# Setup work directory
WORKDIR /app

# Copy dependency manifest
COPY pyproject.toml uv.lock ./

# Create minimal empire-core stub so the path dependency resolves at build time.
# The real empire-core package is mounted at runtime via docker-compose volumes.
RUN mkdir -p /empire-core/empire_core \
    && echo '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n\n[project]\nname = "empire-core"\nversion = "1.0.0"\nrequires-python = ">=3.12"\ndependencies = ["structlog>=24.1.0","httpx>=0.27","tenacity>=8.2","pydantic-settings>=2.0"]' \
       > /empire-core/pyproject.toml \
    && touch /empire-core/empire_core/__init__.py

# Install dependencies (no devdeps)
RUN uv sync --frozen --no-dev --no-install-project

# Copy Source Code
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY alembic/ ./alembic/
COPY alembic.ini .

# Set PYTHONPATH
ENV PYTHONPATH=/app/src

# Security: Create non-root user
RUN groupadd -r appgroup && useradd -r -g appgroup -s /bin/bash appuser \
    && chown -R appuser:appgroup /app

# Switch to non-root user
USER appuser

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Expose port
EXPOSE 8000

# Default command
CMD ["python", "-m", "uvicorn", "orion.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
