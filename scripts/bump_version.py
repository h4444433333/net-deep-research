#!/usr/bin/env python3
"""Single-source version bump for net-deep-research.

Syncs the version string across all 5 known version points and verifies
consistency by reading them back. Version numbers on PyPI are immutable,
so the script refuses to bump to a version already published.

Usage:
    python scripts/bump_version.py 1.1.2            # apply bump
    python scripts/bump_version.py 1.1.2 --dry-run  # show planned edits only
    python scripts/bump_version.py --check          # verify current consistency

Exit codes: 0 ok, 1 validation failure, 2 usage error.
"""

from __future__ import annotations

import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (relative path, regex with one capture group for the version, template)
VERSION_POINTS: list[tuple[str, re.Pattern[str], str]] = [
    ("pyproject.toml", re.compile(r'^version = "([^"]+)"', re.M), 'version = "{v}"'),
    ("net_deep_research/__init__.py", re.compile(r'^__version__ = "([^"]+)"', re.M), '__version__ = "{v}"'),
    ("_meta.json", re.compile(r'"version": "([^"]+)"'), '"version": "{v}"'),
    ("SKILL.md", re.compile(r"Bundle version: `([^`]+)`"), "Bundle version: `{v}`"),
    ("skill-card.md", re.compile(r"(## Skill Version\(s\): <br>\n)(\S+)(.*\n)"), r"\g<1>{v}\g<3>"),
]

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def read_versions() -> dict[str, str | None]:
    """Return {path: version found or None} for every version point."""
    found: dict[str, str | None] = {}
    for rel, pattern, _ in VERSION_POINTS:
        text = (ROOT / rel).read_text(encoding="utf-8")
        m = pattern.search(text)
        if m is None:
            found[rel] = None
        else:
            # version lives in group 2 when the template keeps surrounding text
            found[rel] = m.group(2) if m.lastindex and m.lastindex >= 2 else m.group(1)
    return found


def check_consistency() -> str:
    """Exit 1 if version points disagree; return the agreed version."""
    versions = read_versions()
    missing = [p for p, v in versions.items() if v is None]
    if missing:
        print(f"[bump] ERROR: version not found in: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    distinct = set(versions.values())
    if len(distinct) != 1:
        print("[bump] ERROR: version points disagree:", file=sys.stderr)
        for path, v in versions.items():
            print(f"  {path}: {v}", file=sys.stderr)
        sys.exit(1)
    return versions["pyproject.toml"]  # type: ignore[return-value]


def git_worktree_clean() -> bool:
    out = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return out.stdout.strip() == ""


def published_on_pypi(version: str) -> bool:
    """True if the version already exists on PyPI (best effort; network errors -> False)."""
    url = f"https://pypi.org/pypi/net-deep-research/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False


def apply_bump(new_version: str, dry_run: bool) -> None:
    safe_v = new_version.replace("\\", "\\\\")
    for rel, pattern, template in VERSION_POINTS:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        if not pattern.search(text):
            print(f"[bump] ERROR: pattern not found in {rel}", file=sys.stderr)
            sys.exit(1)
        replacement = template.format(v=safe_v)
        new_text = pattern.sub(lambda m: m.expand(replacement), text, count=1)
        if dry_run:
            print(f"[bump] would edit {rel}")
        else:
            path.write_text(new_text, encoding="utf-8")
            print(f"[bump] updated {rel}")


def main() -> None:
    args = [a for a in sys.argv[1:] if a]
    dry_run = "--dry-run" in args
    check_only = "--check" in args
    positional = [a for a in args if not a.startswith("--")]

    if check_only:
        version = check_consistency()
        print(f"[bump] all version points consistent at {version}")
        return
    if len(positional) != 1:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    new_version = positional[0]
    if not SEMVER.match(new_version):
        print(f"[bump] ERROR: {new_version} is not x.y.z semver", file=sys.stderr)
        sys.exit(2)

    current = check_consistency()
    if new_version == current:
        print(f"[bump] already at {current}; nothing to do")
        return
    if not dry_run and not git_worktree_clean():
        print("[bump] ERROR: git worktree is dirty; commit or stash first", file=sys.stderr)
        sys.exit(1)
    if published_on_pypi(new_version):
        print(f"[bump] ERROR: {new_version} already exists on PyPI; versions are immutable", file=sys.stderr)
        sys.exit(1)

    print(f"[bump] {current} -> {new_version}" + (" (dry-run)" if dry_run else ""))
    apply_bump(new_version, dry_run)
    if not dry_run:
        agreed = check_consistency()
        print(f"[bump] verified: all version points now at {agreed}")


if __name__ == "__main__":
    main()
