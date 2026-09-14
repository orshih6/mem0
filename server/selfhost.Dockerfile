# Production image for the self-hosted REST server (orshih6 fork).
#
# Differs from server/Dockerfile (upstream) in three ways:
#   - Build context is the REPO ROOT, and the mem0 SDK is installed from this same
#     commit instead of `mem0ai>=0.1.48` from PyPI, so server and SDK can never drift.
#   - No `--reload`; runs as a non-root user, with MEM0_DIR under /app/history.
#   - Adds psycopg[binary]: upstream requirements need a system libpq that slim lacks.
#   - Runs `alembic upgrade head` before starting, so the app tables (users, api_keys,
#     request_logs, settings, ...) exist without a separate migrate step.
#
# Build:  docker build -f server/selfhost.Dockerfile -t mem0-server .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Server dependencies, minus the PyPI SDK (installed from source below).
COPY server/requirements.txt /tmp/requirements.txt
# requirements.txt asks for plain `psycopg`, which needs a system libpq that python:slim
# does not have ("no pq wrapper available"). The binary extra bundles libpq.
RUN grep -viE '^mem0ai' /tmp/requirements.txt > /tmp/requirements.nosdk.txt \
 && pip install -r /tmp/requirements.nosdk.txt "psycopg[binary,pool]>=3.2.8"

# mem0 SDK from this commit.
COPY pyproject.toml README.md LICENSE /src/
COPY mem0 /src/mem0
RUN pip install /src && rm -rf /src

COPY server /app

RUN useradd --system --uid 10001 --home-dir /home/mem0 --create-home --shell /usr/sbin/nologin mem0 \
 && mkdir -p /app/history \
 && chown -R 10001:10001 /app/history

# The SDK creates $MEM0_DIR (default ~/.mem0) at import time. Keep it next to the
# history DB so one writable volume at /app/history covers all runtime state.
ENV HOME=/home/mem0 \
    MEM0_DIR=/app/history/.mem0

USER 10001
EXPOSE 8000

# --proxy-headers so the per-IP rate limiter sees the client address from the
# reverse proxy rather than the proxy's own pod IP.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=*"]
