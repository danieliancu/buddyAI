# ola server + admin/customer web app. Build context: repository root.
#   docker compose -f deploy/docker-compose.yml build server

# --- web app (React) -----------------------------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# --- server ----------------------------------------------------------------------------------
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    BUDDYAI_DATA_DIR=/data \
    BUDDYAI_WEB_DIST=/app/web/dist \
    BUDDYAI_HOST=0.0.0.0 \
    BUDDYAI_PORT=8765
WORKDIR /app/server
COPY server/requirements.txt ./
RUN pip install -r requirements.txt
COPY server/ ./
COPY --from=web /src/web/dist /app/web/dist
RUN useradd --system --uid 10001 --home /app buddyai \
    && mkdir -p /data && chown buddyai /data \
    && rm -rf tests tools/make_samples.ps1 .env data
# Which commit this image was built from, for /healthz and the System page (server/app/version.py). Coolify passes
# SOURCE_COMMIT only if "Available during build" is chosen; by default the running container gets it instead.
# Kept after the slow steps so that a new commit rebuilds only this layer.
ARG SOURCE_COMMIT=unknown
ENV BUDDYAI_BUILD_COMMIT=$SOURCE_COMMIT
USER buddyai
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/readyz', timeout=4).status == 200 else 1)"
CMD ["python", "-m", "app.main"]
