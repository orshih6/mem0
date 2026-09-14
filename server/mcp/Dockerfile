# MCP wrapper for the self-hosted mem0 REST API (orshih6 fork). See server/mcp/README.md.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY server.py .

RUN useradd --system --uid 10001 --create-home --home-dir /home/mcp --shell /usr/sbin/nologin mcp
USER 10001
EXPOSE 8080

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips=*"]
