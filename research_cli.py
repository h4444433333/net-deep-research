#!/usr/bin/env python3
"""Thin entry point kept for backward compatibility.

Real implementation lives in net_deep_research/cli.py; installing the package
(`pip install net-deep-research`) provides the `net-deep-research` command
and the `from net_deep_research import research` API.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from net_deep_research.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
