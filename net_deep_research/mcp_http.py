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
    NDR_MCP_ALLOWED_HOSTS  comma-separated Host header allowlist for the mcp
                       SDK DNS-rebinding guard (default: the shoggoth.vip
                       domains; widen when served under other hostnames)

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
    allowed_hosts = [
        h.strip()
        for h in os.environ.get(
            "NDR_MCP_ALLOWED_HOSTS", "www.shoggoth.vip,shoggoth.vip"
        ).split(",")
    ]

    try:
        # fastmcp 3.x forwards host/port/path as transport kwargs
        mcp.run(
            transport="streamable-http",
            host=host,
            port=port,
            path=path,
            allowed_hosts=allowed_hosts,
        )
        return
    except TypeError:
        pass

    # mcp SDK FastMCP: host/port/path + DNS-rebinding allowlist live on settings.
    # The SDK defaults to protection=ON with loopback-only hosts, which rejects
    # every request arriving through a reverse proxy -> retarget the allowlist.
    settings = getattr(mcp, "settings", None)
    if settings is not None:
        settings.host = host
        settings.port = port
        for attr in ("streamable_http_path", "mount_path"):
            if hasattr(settings, attr):
                setattr(settings, attr, path)
                break
        ts = getattr(settings, "transport_security", None)
        if ts is not None:
            ts.allowed_hosts = list(allowed_hosts)
            # allowed_origins stays at the SDK default (loopback): tool-to-tool
            # calls carry no Origin header, TLS termination sits at the gateway
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
