# numguard — public x402-metered verification API (and MCP-over-HTTP).
FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY numguard ./numguard
RUN pip install --no-cache-dir . uvicorn "cryptography>=41"

EXPOSE 8080
# The x402-paid REST API. Set NUMGUARD_PAYTO + NUMGUARD_FACILITATOR_URL to charge; unset = free dev mode.
# To serve the MCP server over HTTP instead:  uvicorn numguard.mcp_server:app --host 0.0.0.0 --port 8080
CMD ["uvicorn", "numguard.rest_api:app", "--host", "0.0.0.0", "--port", "8080"]
