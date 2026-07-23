"""asgi — one ASGI app that serves BOTH rails from a single deployment:

- the x402-paid REST API at the root (`/verify_backtest`, `/pricing`, `/health`, …)
- the MCP server over streamable-HTTP at `/mcp` (so agents + registries like Smithery can reach it by URL)

Run:  uvicorn numguard.asgi:app --host 0.0.0.0 --port $PORT

The MCP app owns a session-manager lifespan; we reuse it so the combined app starts it correctly.
"""
from __future__ import annotations
from starlette.applications import Starlette

from .mcp_server import mcp
from . import rest_api

_mcp_app = mcp.streamable_http_app()          # a Starlette app serving /mcp, with its own lifespan

app = Starlette(
    routes=rest_api.routes + list(_mcp_app.routes),
    lifespan=lambda a: _mcp_app.router.lifespan_context(a),
)
