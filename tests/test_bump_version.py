"""Tests for scripts/bump_version.py version-point sync."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

spec = importlib.util.spec_from_file_location("bump_version", ROOT / "scripts" / "bump_version.py")
bump = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bump)


def test_all_five_version_points_found():
    versions = bump.read_versions()
    assert len(versions) == 5
    assert all(v is not None for v in versions.values()), versions


def test_version_points_currently_consistent():
    version = bump.check_consistency()
    assert bump.SEMVER.match(version), version


def test_semver_validation():
    assert bump.SEMVER.match("1.1.2")
    assert bump.SEMVER.match("10.20.30")
    assert not bump.SEMVER.match("1.1")
    assert not bump.SEMVER.match("1.1.1.1")
    assert not bump.SEMVER.match("v1.1.1")
    assert not bump.SEMVER.match("1.1.1a")


def test_apply_bump_dry_run_does_not_touch_files():
    before = {rel: (bump.ROOT / rel).read_text(encoding="utf-8") for rel, _, _ in bump.VERSION_POINTS}
    bump.apply_bump("9.9.9", dry_run=True)
    after = {rel: (bump.ROOT / rel).read_text(encoding="utf-8") for rel, _, _ in bump.VERSION_POINTS}
    assert before == after
