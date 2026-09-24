# =============================================================================
# AIOutbound + LeadAI
# =============================================================================
# This file was missing from the repository. `docker-compose.yml` uses
# `build: .` and .github/workflows/deploy.yml builds an image, but `dockerfile`
# was listed in .gitignore, so neither could actually run from a fresh clone.
# Remove that line from .gitignore when you commit this.
#
# Two stages. The builder compiles the wheels that need a C toolchain
# (mysqlclient, numba/llvmlite, soxr, onnxruntime); the runtime image gets the
# built wheels and none of the compilers, which takes roughly 900MB off the
# final size and removes gcc from anything reachable by a request.
# =============================================================================

# ----------------------------------------------------------------------------
# Stage 1 — build wheels
# ----------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS builder

# 3.11 rather than 3.12: numba/llvmlite in requirements.txt pin against 3.11,
# and 3.10 is what the (Windows) prompts.pyd was built for. 3.11 is the newest
# version every pinned dependency here resolves cleanly on.

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        pkg-config \
        python3-dev \
        default-libmysqlclient-dev \
        libffi-dev \
        libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /wheels
COPY requirements.txt ./

# Build every dependency into a wheelhouse. Doing this as its own layer means a
# code change does not re-compile mysqlclient — that alone is several minutes
# back on every deploy.
RUN pip wheel --wheel-dir /wheels/dist -r requirements.txt


# ----------------------------------------------------------------------------
# Stage 2 — runtime
# ----------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=Asia/Kolkata \
    PORT=6789

# Runtime libraries only — no compilers.
#   libmysqlclient21 : mysqlclient at runtime
#   ffmpeg, libsndfile1 : pydub / librosa / soundfile decode call audio
#   curl : the healthcheck below
RUN apt-get update && apt-get install -y --no-install-recommends \
        libmariadb3 \
        ffmpeg \
        libsndfile1 \
        curl \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels/dist /wheels/dist
COPY requirements.txt ./
RUN pip install --no-index --find-links=/wheels/dist -r requirements.txt \
    && rm -rf /wheels

# Run as a non-root user. The app writes only to ./scripts (mounted) and to the
# LeadAI local storage fallback, both of which are chowned below.
RUN useradd --create-home --uid 10001 appuser
WORKDIR /app

COPY --chown=appuser:appuser . /app

RUN mkdir -p /app/storage_local /app/scripts \
    && chown -R appuser:appuser /app/storage_local /app/scripts

USER appuser

EXPOSE 6789

# The health endpoint is in PUBLIC_PATHS, so this needs no credentials.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/api/leadai/health" || exit 1

# Single worker on purpose. `active_calls`, `transcript_connections` and the
# websocket manager are process-local dicts in outbound/app.py — a second
# uvicorn worker would field the /media-stream webhook for a call it has never
# heard of and the audio leg would silently fail. Scale by running more
# CONTAINERS behind a sticky-session load balancer, or move that state into
# Redis first. See CLEANUP_AND_FIXES.md.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1 --timeout-keep-alive 75"]
