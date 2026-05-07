# ── Stage: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim

# Install Node.js 20 LTS + system deps needed by Playwright/Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer cache friendly)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright's Chromium binary + all OS-level deps in one step
RUN playwright install chromium --with-deps

# Copy application code
COPY . .

# Pre-create the viz output directory (Railway volume mounts here)
RUN mkdir -p viz_outputs

# Tell the orchestrator where fixed_main_v6.py lives (same dir as app)
ENV FIXED_MAIN_PATH=/app/fixed_main_v6.py
# In production: no live dev servers (they bind localhost ports inaccessible from outside)
ENV AUTO_START_DEV_SERVER=false
# Always run npm run build after generation so vizzes are statically servable
ENV BUILD_STATIC=true

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
