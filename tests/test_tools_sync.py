"""Guard: root tools/ (skill bundle) and net_deep_research/tools/ (PyPI package)
must stay byte-identical. The skill channel runs the root copies; the package
ships its own copies. If you edit one side, sync the other.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_TOOLS = ROOT / "tools"
PKG_TOOLS = ROOT / "net_deep_research" / "tools"
SCRIPTS = ["md_to_pdf.py", "score_stability.py"]


def test_skill_and_package_tool_copies_exist():
    for name in SCRIPTS:
        assert (SKILL_TOOLS / name).is_file(), f"skill-side tools/{name} missing"
        assert (PKG_TOOLS / name).is_file(), f"package-side net_deep_research/tools/{name} missing"


def test_skill_and_package_tool_copies_identical():
    for name in SCRIPTS:
        skill_bytes = (SKILL_TOOLS / name).read_bytes()
        pkg_bytes = (PKG_TOOLS / name).read_bytes()
        assert skill_bytes == pkg_bytes, (
            f"{name} diverged between tools/ and net_deep_research/tools/; "
            "edit one side then copy to the other"
        )
