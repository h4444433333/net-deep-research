#!/usr/bin/env python3
"""net-deep-research MCP adapter — Streamable HTTP transport.

Serves the same two tools as the stdio adapter (deep_research, check_source)
over HTTP so the server can be registered on MCP directories (Smithery, ...)
or wired into platform MCP gateways (Coze, ...).

Install:
    pip install "net-deep-research[mcp]"

Run (defaults: 127.0.0.1:8086, endpoint path "/mcp" — fixed by fastmcp 3.x):
    net-deep-research-mcp-http
or from a source checkout:
    python -m net_deep_research.mcp_http

Configuration via environment variables:
    NDR_MCP_HTTP_HOST  bind address (default 127.0.0.1; use 0.0.0.0 behind
                       a reverse proxy)
    NDR_MCP_HTTP_PORT  listen port  (default 8086)
    NDR_MCP_HTTP_PATH  endpoint path override (fastmcp 2.x / mcp SDK 1.x only;
                       fastmcp 3.x always serves /mcp)

Put this behind TLS with a reverse proxy (Caddy/nginx) and register the
public HTTPS URL, e.g. https://www.shoggoth.vip/mcp/

Note: sessions are in-memory, so run a single worker behind the proxy.
LLM_API_KEY and backend settings follow the core package (.env / env vars).
"""

from __future__ import annotations

import os

from net_deep_research.mcp_server import mcp


def main() -> None:
    """Console entry point: net-deep-research-mcp-http (Streamable HTTP)."""
    host = os.environ.get("NDR_MCP_HTTP_HOST", "127.0.0.1")
    port = int(os.environ.get("NDR_MCP_HTTP_PORT", "8086"))
    path = os.environ.get("NDR_MCP_HTTP_PATH", "/mcp")

    try:
        # fastmcp 3.x forwards host/port/path as transport kwargs
        mcp.run(transport="streamable-http", host=host, port=port, path=path)
    except TypeError:
        # fastmcp 2.x / mcp SDK 1.x keep them on the settings object
        settings = getattr(mcp, "settings", None)
        if settings is not None:
            settings.host = host
            settings.port = port
            try:
                settings.path = path
            except ValueError:
                pass  # path not configurable on this SDK version
        try:
            mcp.run(transport="streamable-http")
        except (ValueError, KeyError):
            mcp.run(transport="http")


if __name__ == "__main__":
    main()
