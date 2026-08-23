#!/usr/bin/env python3
"""net-deep-research MCP adapter (thin wrapper, zero code duplication).

Exposes the core library as MCP tools so any MCP-capable client
(Claude Desktop, Qoder, Coze MCP gateway, ...) can use it.

Install:
    pip install "net-deep-research[mcp]"

Run (stdio transport), either via the console entry point:
    net-deep-research-mcp
or from a source checkout:
    python -m net_deep_research.mcp_server

MCP client config example (after pip install):
    {"mcpServers": {"net-deep-research":
        {"command": "net-deep-research-mcp", "args": []}}}

Configuration (.env / environment) follows the core package:
LLM_API_KEY is required; backend defaults to https://www.shoggoth.vip
and degrades silently when unreachable (same rules as the CLI).
"""

from __future__ import annotations

import json

from net_deep_research import __version__, research
from net_deep_research.cli import backend_check_urls

# MCP SDK 1.x bundles FastMCP at mcp.server.fastmcp; SDK 2.x removed it and
# the standalone `fastmcp` package is the successor. Support both.
try:
    from mcp.server.fastmcp import FastMCP  # mcp SDK 1.x
except ModuleNotFoundError:
    from fastmcp import FastMCP  # standalone fastmcp (mcp SDK 2.x era)

mcp = FastMCP(f"net-deep-research {__version__}")


@mcp.tool()
def deep_research(question: str, report: bool = False) -> str:
    """Run a multi-source deep research on the question.

    Multi-round search, URL safety screening, source reputation scoring
    and conflict-aware synthesis. Returns JSON with keys: session_id,
    normalization, sources, answer, feedback, passport, report_path.
    """
    result = research(question, report=report)
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
def check_source(url: str) -> str:
    """Screen a single URL for safety (SSL, Safe Browsing, heuristics).

    Returns JSON {"url": ..., "safe": bool}. When the reputation backend
    is unreachable the verdict comes from local inline guards.
    """
    verdicts = backend_check_urls([url])
    safe = bool(verdicts.get(url, True)) if verdicts else True
    return json.dumps({"url": url, "safe": safe})


def main() -> None:
    """Console entry point: net-deep-research-mcp (stdio transport)."""
    mcp.run()


if __name__ == "__main__":
    main()
