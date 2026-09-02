# Multi-stage build for a single deployable service: the FastAPI backend
# serves the built React app itself (see main.py's SPA fallback), so one
# image/one Render service is all this needs — no separate static host.

# ---- Stage 1: build the frontend ----
# Pinned by digest (not just the floating "20-slim" tag) so the base image
# can't silently change between builds — verified current as of this pin:
# docker.io/library/node:20-slim
FROM node:20-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0 AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: Python runtime ----
# Pinned by digest — see the note on the frontend stage above. Verified
# current as of this pin: docker.io/library/python:3.12-slim
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
WORKDIR /app

# psycopg[binary] and fastembed's onnxruntime need no extra system libs beyond
# these; tree-sitter-python ships prebuilt wheels.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# The built frontend, in the exact path main.py's SPA fallback looks for
# (frontend/dist relative to the repo root, i.e. two levels above main.py).
COPY --from=frontend /app/frontend/dist ./frontend/dist

# Run as a non-root user. This app clones and parses arbitrary
# user-supplied git repositories — any code-execution bug in that path
# would otherwise run as root, with a writable root filesystem.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
    && mkdir -p /app/repos /app/data \
    && chown -R app:app /app
USER app

# Render sets $PORT at runtime; default only matters for local `docker run`.
ENV PORT=8000
# main.py's SPA fallback can't find this on its own once installed via pip
# (see config.py's frontend_dist comment) — point it here explicitly.
ENV FRONTEND_DIST=/app/frontend/dist
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/health', timeout=3)"
CMD ["sh", "-c", "uvicorn archaeologist.main:app --host 0.0.0.0 --port ${PORT}"]
