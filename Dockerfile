# ── Stage 1: frontend build ──────────────────────────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build   # → /app/backend/static (vite.config.ts outDir: ../backend/static)

# ── Stage 2: python deps (separate so deps cache survives code changes) ───────
FROM python:3.12-slim AS python-deps
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# ── Stage 3: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Install only what Playwright needs to fetch chromium deps. Node is gone:
# the vanilla viz pipeline emits one self-contained HTML file and does not
# run `npm install` or `npm build`.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR /app

# Copy Python packages from the deps stage
COPY --from=python-deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=python-deps /usr/local/bin /usr/local/bin

# Install Playwright's Chromium binary + all OS-level deps in one step
RUN playwright install chromium --with-deps \
    && rm -rf /var/lib/apt/lists/*

# Copy application source (backend package + subprocess contract stub)
COPY backend/ ./backend/
COPY fixed_main_v6.py ./

# Copy the built SPA into the location the / route expects
COPY --from=frontend-builder /app/backend/static ./backend/static/

# Pre-create the viz output directory (Railway volume mounts here)
RUN mkdir -p /app/viz_outputs

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIZ_OUTPUT_DIR=/app/viz_outputs \
    FIXED_MAIN_PATH=/app/fixed_main_v6.py

RUN useradd --create-home --uid 1001 app && chown -R app:app /app
USER app

EXPOSE 8001
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8001}"]
