# numguard — public x402-metered verification API (and MCP-over-HTTP).
FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY numguard ./numguard
RUN pip install --no-cache-dir . uvicorn "cryptography>=41" "web3>=6"

EXPOSE 8080
# Bind to the host's $PORT when set (Render/Fly/Heroku assign it), else 8080. Shell form so $PORT expands.
# The x402-paid REST API. Set NUMGUARD_PAYTO + NUMGUARD_FACILITATOR_URL to charge; unset = free dev mode.
# To serve the MCP server over HTTP instead: swap rest_api for  numguard.mcp_server:app
CMD ["sh", "-c", "uvicorn numguard.rest_api:app --host 0.0.0.0 --port ${PORT:-8080}"]
