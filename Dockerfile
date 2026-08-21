# Multi-stage build for a single deployable service: the FastAPI backend
# serves the built React app itself (see main.py's SPA fallback), so one
# image/one Render service is all this needs — no separate static host.

# ---- Stage 1: build the frontend ----
FROM node:20-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: Python runtime ----
FROM python:3.12-slim
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

# Render sets $PORT at runtime; default only matters for local `docker run`.
ENV PORT=8000
# main.py's SPA fallback can't find this on its own once installed via pip
# (see config.py's frontend_dist comment) — point it here explicitly.
ENV FRONTEND_DIST=/app/frontend/dist
CMD ["sh", "-c", "uvicorn archaeologist.main:app --host 0.0.0.0 --port ${PORT}"]
