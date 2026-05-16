# ══════════════════════════════════════════════════════════
# PULSΞ Backend — Python FastAPI
# ══════════════════════════════════════════════════════════
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Build-time system deps. `gcc` covers any wheel-less builds; `curl` is for
# the healthcheck (no Python `requests` round-trip needed).
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m textblob.download_corpora || true

# App code
COPY python/ .
COPY frontend/ /frontend/
COPY images/ /images/

# Non-root user
RUN useradd -m -u 1001 pulse && chown -R pulse:pulse /app /frontend /images
USER pulse

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD curl --fail --silent http://localhost:8000/health > /dev/null || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
