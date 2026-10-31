# AXIOM: the one image. A Node stage builds the dashboard (the only frontend); the Python runtime serves it.
#
# Versions are pinned here and nowhere else. To bump one, change the ARG and rebuild:
#   NODE_VERSION    must match .nvmrc and the CI Node version (CI reads the file)
#   PYTHON_VERSION  must match .python-version (CI reads the file)
#   UV_VERSION      the uv that installs from uv.lock
# Playwright's Chromium follows the playwright version pinned in uv.lock; nothing else downloads a browser.
# tests/test_infra_consistency.py fails when these drift from the version files.
ARG NODE_VERSION=22.23.2
ARG PYTHON_VERSION=3.12.15
ARG UV_VERSION=0.12.18

# ---- Stage 1: dashboard -> /build/jobpilot/static -------------------------------------------------------
FROM node:${NODE_VERSION}-bookworm-slim AS dashboard
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY frontend/ ./
# tsc -b (type check) then vite build; vite.config.ts writes to ../jobpilot/static.
RUN npm run build

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ---- Stage 2: runtime ------------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
COPY --from=uv /uv /uvx /bin/
# MALLOC_ARENA_MAX: glibc's default 8x-cores malloc arenas let the threaded app grow 2 GB -> 5.4 GB in 15 minutes
# under a 10-run load (2026-10-15); two arenas keep freed memory reusable.
# MALLOC_MMAP_THRESHOLD_ and MALLOC_TRIM_THRESHOLD_: naming either turns off glibc's self-tuning, which raises both after
# the first large free, up to 32 MB. After that a query's 460 KB packets came from the heaps, and small objects allocated
# between them pinned the freed space: 763 MB free of 897 MB in malloc's heaps, 1.3 GB resident (2026-10-30). At these
# values a large value is mapped on its own and unmapped when freed; jobpilot/memory.py returns the rest.
ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PLAYWRIGHT_BROWSERS_PATH=/opt/browsers \
    ANONYMIZED_TELEMETRY=false \
    BROWSER_USE_LOGGING_LEVEL=warning \
    DATA_DIR=/app/.data \
    APP_PORT=8080 \
    MALLOC_ARENA_MAX=2 \
    MALLOC_MMAP_THRESHOLD_=131072 \
    MALLOC_TRIM_THRESHOLD_=131072 \
    PATH=/app/.venv/bin:$PATH
WORKDIR /app

# Resume tailoring compiles LaTeX (jobpilot/features/resumes/compiler.py).
RUN apt-get update && apt-get install -y --no-install-recommends \
        texlive-latex-base texlive-latex-recommended texlive-fonts-recommended && \
    rm -rf /var/lib/apt/lists/*

# Dependencies first, so a code-only change never reinstalls them.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Chromium for headless runs without a host Chrome (the demo, `jobpilot verify`, friends with no worker Chrome).
# The code launches the full Chromium binary with --headless=new, so the separate headless shell is skipped.
RUN playwright install --with-deps --no-shell chromium && \
    rm -rf /var/lib/apt/lists/* && \
    chmod -R a+rX /opt/browsers && \
    useradd --uid 10001 --create-home pilot && \
    mkdir -p /app/.data && chown -R pilot:pilot /app/.data

# Application code, then the built dashboard, then install the project itself (editable, so /app/jobpilot is live).
COPY jobpilot/ ./jobpilot/
COPY LICENSE ./
COPY docs/third-party.md ./docs/third-party.md
COPY docs/licenses/ ./docs/licenses/
COPY --from=dashboard /build/jobpilot/static ./jobpilot/static
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Declared last so a new revision never invalidates the layers above.
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.title="AXIOM" \
      org.opencontainers.image.source="https://github.com/premalshah999/AXIOM" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.revision="${GIT_SHA}"
ENV AXIOM_REVISION=${GIT_SHA}

USER pilot
# The app listens on APP_PORT inside the container, and Compose publishes the same number, so BASE_URL (the address in
# the browser) is also correct from inside: the built-in demo form and `jobpilot verify` open it from the container.
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ['APP_PORT'] + '/healthz', timeout=3)"]
CMD ["sh", "-c", "exec jobpilot serve --host 0.0.0.0 --port \"$APP_PORT\""]
