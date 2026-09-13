# syntax=docker/dockerfile:1
#
# Kairos Code in one image: the API, the Web UI and the CLI.
# NOTE: not verified by the maintainer's machine (no Docker available at the
# time of writing) — please open an issue if a step needs adjusting.

# ---------- stage 1: build the frontend ----------
FROM node:24-alpine AS web
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build


# ---------- stage 2: runtime ----------
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    KAIROS_HOST=0.0.0.0 \
    KAIROS_PORT=8900 \
    KAIROS_DATA_DIR=/data

WORKDIR /app

# git is not optional: checkpoints, rollback and the demo all shell out to it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE NOTICE ./
# hatch_build.py is the custom wheel hook declared in pyproject.toml: without it
# `pip install .` cannot even start building. The UI arrives as web/dist below,
# so the hook finds it and packs it into the installed package.
COPY hatch_build.py ./
COPY kairos/ ./kairos/
COPY api/ ./api/
COPY scripts/ ./scripts/
# The UI the API serves. `api/app.py` resolves it as <parent of api/>/web/dist.
COPY --from=web /app/web/dist ./web/dist

RUN pip install .

# Settings, the SQLite database, the cost ledger and project workspaces live here.
VOLUME ["/data"]
EXPOSE 8900

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8900/api/health', timeout=4).status == 200 else 1)"

# `serve` is the long-lived HTTP/WebSocket server (the CLI verbs demo/gate/exec
# use the same image: `docker run --rm kairos-code demo`).
CMD ["python", "-m", "kairos", "serve", "--host", "0.0.0.0", "--port", "8900"]
