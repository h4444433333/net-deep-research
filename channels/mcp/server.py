#!/usr/bin/env python3
"""Source-checkout entry for the MCP adapter.

The real implementation lives in the package (net_deep_research/mcp_server.py)
so that `pip install "net-deep-research[mcp]"` ships it too. This file only
exists so source users can still run `python channels/mcp/server.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the package importable when running straight from a source checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from net_deep_research.mcp_server import main  # noqa: E402

if __name__ == "__main__":
    main()
