#!/usr/bin/env python3
"""Build the static web-validator bundle for Cloudflare Pages deploy.

Produces ``tools/web-validator/dist/`` containing:

    index.html, app.js, app.css              — static front-end
    manifest.json                            — {wheel: "<name>", pyodide_version}
    dwc_sidecar-<version>-py3-none-any.whl   — loaded in-browser by micropip

Run from the repo root:

    python3 tools/web-validator/build.py

Mirrors the ``tools/publish-schemas/`` pattern — pure-stdlib orchestration,
no deployment. Deployment lives in ``.github/workflows/web-validator.yml``.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


PYODIDE_VERSION = "0.27.3"

HERE    = Path(__file__).resolve().parent
REPO    = HERE.parent.parent
DIST    = HERE / "dist"
STATIC  = ("index.html", "app.js", "app.css")


def build_wheel(out_dir: Path) -> Path:
    """Invoke ``python -m build --wheel``. Requires the ``build`` package;
    installed via ``pip install build`` or ``pip install -e .[dev]``
    if we add it to dev extras."""
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel",
         "--outdir", str(out_dir), str(REPO)],
        check=True,
    )
    wheels = sorted(out_dir.glob("dwc_sidecar-*-py3-none-any.whl"))
    if not wheels:
        raise SystemExit("wheel build produced no dwc_sidecar-*.whl")
    # If multiple versions accumulated, keep only the newest.
    newest = wheels[-1]
    for w in wheels[:-1]:
        w.unlink()
    return newest


def copy_static(src_dir: Path, out_dir: Path) -> None:
    for name in STATIC:
        shutil.copy2(src_dir / name, out_dir / name)


def write_manifest(out_dir: Path, wheel_name: str) -> None:
    manifest = {
        "wheel":            wheel_name,
        "pyodide_version":  PYODIDE_VERSION,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def main() -> int:
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)

    wheel = build_wheel(DIST)
    copy_static(HERE, DIST)
    write_manifest(DIST, wheel.name)

    # Human-readable summary on stdout
    artifacts = sorted(DIST.iterdir())
    print(f"dist/ built at {DIST}:")
    for p in artifacts:
        size_kb = p.stat().st_size / 1024
        print(f"  {p.name:<50s} {size_kb:>10,.1f} KB")
    print(f"\nDeploy: push this dist/ to Cloudflare Pages project dwc-validator.")
    print(f"Or run locally:  python3 -m http.server --directory {DIST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
