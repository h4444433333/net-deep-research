#!/usr/bin/env python3
"""Unified release pipeline for net-deep-research.

Subcommands:
    release.py build                 clean old artifacts -> build -> twine check -> fresh-venv install verify
    release.py upload --repo testpypi|pypi
                                     upload the CURRENT version's artifacts (explicit filenames, no globs)
    release.py skill-bundle          stage the ClawHub skill bundle into dist/skill/:
                                     zip archive + extracted (uncompressed) copy of the current version
    release.py clawhub               skill-bundle + publish via `npx clawhub@latest publish`
    release.py all                   build + upload testpypi + upload pypi + clawhub

Credentials: read from environment variables only (never stored on disk):
    PYPI_TOKEN       pypi.org API token
    TESTPYPI_TOKEN   test.pypi.org API token

Usage example:
    python scripts/release.py build
    TESTPYPI_TOKEN=pypi-xxx python scripts/release.py upload --repo testpypi
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import venv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
SKILL_DIST = DIST / "skill"
PKG_NAME = "net_deep_research"

# ClawHub skill bundle 的内容清单（相对仓库根），__pycache__/.DS_Store 一律排除。
SKILL_BUNDLE_FILES = ["SKILL.md", "_meta.json", "skill-card.md"]
SKILL_BUNDLE_DIRS = ["references", "tools"]
_SKILL_EXCLUDE_PARTS = {"__pycache__", ".DS_Store"}

REPOS = {
    "pypi": {"env": "PYPI_TOKEN", "flag": []},
    "testpypi": {"env": "TESTPYPI_TOKEN", "flag": ["--repository", "testpypi"]},
}


def current_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version = "([^"]+)"', text, re.M)
    if not m:
        die("version not found in pyproject.toml")
    return m.group(1)


def die(msg: str) -> None:
    print(f"[release] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd: list[str], **kw) -> None:
    print(f"[release] $ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=ROOT, **kw)
    if result.returncode != 0:
        die(f"command failed: {' '.join(cmd)}")


def artifact_paths(version: str) -> tuple[Path, Path]:
    wheel = DIST / f"{PKG_NAME}-{version}-py3-none-any.whl"
    sdist = DIST / f"{PKG_NAME}-{version}.tar.gz"
    return wheel, sdist


def cmd_build() -> str:
    version = current_version()
    wheel, sdist = artifact_paths(version)

    # Remove stale artifacts of the CURRENT version only (other versions may be
    # restored by a local sync process; uploads always use explicit filenames).
    for p in (wheel, sdist):
        if p.exists():
            p.unlink()

    # A leftover setuptools build/ dir shadows the `build` package for
    # `python -m build`; it is regeneratable, so drop it first.
    stale_build_dir = ROOT / "build"
    if stale_build_dir.exists():
        shutil.rmtree(stale_build_dir)

    run([sys.executable, "-m", "build"])
    for p in (wheel, sdist):
        if not p.exists():
            die(f"expected artifact missing after build: {p.name}")
    run([sys.executable, "-m", "twine", "check", str(wheel), str(sdist)])

    # Fresh-venv install verification (end-to-end smoke).
    venv_dir = ROOT / ".release_verify_venv"
    if venv_dir.exists():
        shutil.rmtree(venv_dir)
    venv.create(venv_dir, with_pip=True)
    pip = venv_dir / "bin" / "pip"
    py = venv_dir / "bin" / "python"
    run([str(pip), "install", "-q", str(wheel)])
    run([
        str(py), "-c",
        "import net_deep_research as n; assert n.__version__ == '%s', n.__version__; "
        "from net_deep_research import research; from net_deep_research.cli import "
        "_llm_cross_validate_urls, backend_online; print('[release] install verify OK, version', n.__version__)" % version,
    ])
    run([str(venv_dir / "bin" / "net-deep-research"), "--help"], stdout=subprocess.DEVNULL)
    shutil.rmtree(venv_dir)
    print(f"[release] build OK: {wheel.name} + {sdist.name}")
    return version


def cmd_upload(repo: str) -> None:
    import os

    cfg = REPOS[repo]
    token = os.environ.get(cfg["env"])
    if not token:
        die(f"environment variable {cfg['env']} is not set")
    version = current_version()
    wheel, sdist = artifact_paths(version)
    for p in (wheel, sdist):
        if not p.exists():
            die(f"artifact missing: {p.name} (run `release.py build` first)")
    run(
        [sys.executable, "-m", "twine", "upload", *cfg["flag"], str(wheel), str(sdist)],
        env={**__import__("os").environ, "TWINE_USERNAME": "__token__", "TWINE_PASSWORD": token},
    )
    print(f"[release] uploaded {version} to {repo}")


def cmd_skill_bundle() -> str:
    """把 ClawHub skill bundle 落到 dist/skill/：压缩包 + 解压副本。

    产物命名与 PyPI 口径一致，带当前版本号：
        dist/skill/net-deep-research-skill-<version>.zip
        dist/skill/net-deep-research-skill-<version>/   （解压后的目录）
    打包前校验 _meta.json 版本与 pyproject 一致，防止五处版本号漂移。
    """
    version = current_version()
    meta = json.loads((ROOT / "_meta.json").read_text(encoding="utf-8"))
    if meta.get("version") != version:
        die(f"_meta.json version {meta.get('version')!r} != pyproject version {version!r}")

    missing = [
        name
        for name in SKILL_BUNDLE_FILES + SKILL_BUNDLE_DIRS
        if not (ROOT / name).exists()
    ]
    if missing:
        die(f"skill bundle content missing: {', '.join(missing)}")

    # 只清理当前版本的旧产物，避免残留过期压缩包。
    SKILL_DIST.mkdir(parents=True, exist_ok=True)
    for stale in (
        list(SKILL_DIST.glob(f"{PKG_NAME.replace('_', '-')}-skill-{version}.*"))
        + list(SKILL_DIST.glob(f"{PKG_NAME.replace('_', '-')}-skill-{version}"))
    ):
        if stale.is_dir():
            shutil.rmtree(stale)
        else:
            stale.unlink()

    zip_path = SKILL_DIST / f"{PKG_NAME.replace('_', '-')}-skill-{version}.zip"
    extracted_dir = SKILL_DIST / f"{PKG_NAME.replace('_', '-')}-skill-{version}"

    def iter_bundle_files():
        for name in SKILL_BUNDLE_FILES:
            yield ROOT / name, name
        for dir_name in SKILL_BUNDLE_DIRS:
            for path in sorted((ROOT / dir_name).rglob("*")):
                if not path.is_file():
                    continue
                if any(part in _SKILL_EXCLUDE_PARTS for part in path.parts):
                    continue
                yield path, str(path.relative_to(ROOT))

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for src, arcname in iter_bundle_files():
            zf.write(src, arcname)

    if extracted_dir.exists():
        shutil.rmtree(extracted_dir)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extracted_dir)

    file_count = sum(1 for _ in iter_bundle_files())
    print(f"[release] skill bundle OK ({file_count} files): {zip_path.name} + {extracted_dir.name}/")
    return version


def cmd_clawhub() -> None:
    cmd_skill_bundle()
    run(["npx", "-y", "clawhub@latest", "publish"])


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    cmd = args[0]
    if cmd == "build":
        cmd_build()
    elif cmd == "upload":
        repo = args[args.index("--repo") + 1] if "--repo" in args else "pypi"
        if repo not in REPOS:
            die(f"unknown repo: {repo}")
        cmd_upload(repo)
    elif cmd == "skill-bundle":
        cmd_skill_bundle()
    elif cmd == "clawhub":
        cmd_clawhub()
    elif cmd == "all":
        version = cmd_build()
        cmd_upload("testpypi")
        print(f"[release] verify {version} from TestPyPI before continuing...")
        cmd_upload("pypi")
        cmd_clawhub()
        print(f"[release] released {version} to testpypi + pypi + clawhub")
    else:
        print(__doc__, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
