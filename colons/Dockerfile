# ============================================================
# Colons - single-image Dockerfile
# Builds the web UI and serves everything from one process.
# ============================================================

# ---------- Stage 1: build the web UI ----------
FROM node:20-alpine AS web-builder
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---------- Stage 2: python runtime ----------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    COLONS_WEB_DIST=/app/web/dist \
    COLONS_DATA_DIR=/data \
    PLAYWRIGHT_BROWSERS_PATH=/opt/colons-browser

WORKDIR /app

# Minimal system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better layer caching)
COPY pyproject.toml README.md LICENSE ./
COPY backend ./backend
RUN pip install --upgrade pip && pip install ".[full]"
RUN playwright install --with-deps chromium

# Copy the built web UI
COPY --from=web-builder /web/dist /app/web/dist

# Data volume for the SQLite DB and any files
RUN useradd -m -u 1000 colons && mkdir -p /data /app/workspace && chown -R colons:colons /data /app
USER colons
VOLUME ["/data"]
WORKDIR /app/workspace

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "colons_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
