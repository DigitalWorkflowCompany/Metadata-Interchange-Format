"""Smoke tests for the web-validator bundle.

Covers build.py outputs, the Stage 6 missing_is_skip branch added for the
in-browser context, and a near-end-to-end Python simulation of the browser
driver: extract-zip → remap → validate_as_json. The Pyodide round-trip
itself is covered by the CI deploy job; this suite locks the Python side
so a stack change can't silently break the frontend.
"""
import importlib.util
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from dwc_sidecar import web_remap
from dwc_sidecar.validate import validate_as_json


REPO = Path(__file__).resolve().parent.parent
BUILD_SCRIPT = REPO / "tools" / "web-validator" / "build.py"


# ── build.py: produces dist/ with expected shape ────────────────────────


_BUILD_MISSING = importlib.util.find_spec("build") is None


@pytest.mark.skipif(_BUILD_MISSING,
                    reason="PEP 517 'build' package not installed — "
                           "`pip install build` to run this")
def test_build_py_produces_expected_dist(tmp_path, monkeypatch):
    """Invoke build.py from a fresh working copy; confirm the dist/
    directory contains the four static files + a trimmed wheel."""
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    r = subprocess.run(
        [sys.executable, str(BUILD_SCRIPT)],
        capture_output=True, text=True, cwd=REPO, timeout=180,
    )
    assert r.returncode == 0, r.stderr

    dist = REPO / "tools" / "web-validator" / "dist"
    names = {p.name for p in dist.iterdir()}
    assert {"index.html", "app.js", "app.css", "manifest.json"}.issubset(names)
    wheels = list(dist.glob("dwc_sidecar-*-py3-none-any.whl"))
    assert len(wheels) == 1
    # Trimmed wheel must stay under 5 MB — budget for first-page-load.
    assert wheels[0].stat().st_size < 5 * 1024 * 1024, (
        f"wheel too large: {wheels[0].stat().st_size} bytes — "
        "check pyproject.toml exclude list"
    )

    manifest = json.loads((dist / "manifest.json").read_text())
    assert manifest["wheel"] == wheels[0].name
    assert "pyodide_version" in manifest


# ── Stage 6 missing_is_skip (added for the web validator) ───────────────


def test_stage_6_missing_is_skip_does_not_fail(tmp_path, monkeypatch):
    """When the user drops a sidecar without its artifacts, Stage 6 must
    SKIP rather than FAIL so the cryptographic chain (Stages 1/2/3/4/7)
    still reports cleanly."""
    # Build a minimal-ish sidecar pointing at a non-existent file
    monkeypatch.chdir(REPO)
    src_doc = json.loads((REPO / "example-clip.omc.json").read_text())
    # Remove AMF/FDL/MHL artifacts entirely so we're left with just a
    # missing clip — simpler than mocking a whole zip.
    for asset in src_doc["Asset"]:
        for cd in asset["assetFC"]["functionalProperties"]["customData"]:
            if cd["domain"] == "dwc.sidecar.artifacts":
                cd["value"] = [
                    {"kind": "clip", "path": "definitely/missing/file.ari",
                     "hash": {"alg": "sha256", "value": "0" * 64}}
                ]

    out = tmp_path / "broken.omc.json"
    out.write_text(json.dumps(src_doc))

    # Without the flag, Stage 6 FAILs:
    report_default = validate_as_json(out, base_dir=tmp_path)
    stage_6_default = next(s for s in report_default["stages"] if s["stage"] == "6")
    assert stage_6_default["status"] == "fail"

    # With the flag, Stage 6 SKIPs and top-level errors count drops:
    report_skip = validate_as_json(out, base_dir=tmp_path, missing_is_skip=True)
    stage_6_skip = next(s for s in report_skip["stages"] if s["stage"] == "6")
    assert stage_6_skip["status"] == "pass"
    assert report_skip["errors"] < report_default["errors"]
    # And the SKIP line surfaces what was missing
    assert any("SKIP" in ln for ln in stage_6_skip["lines"])


# ── End-to-end Python simulation of the browser driver ─────────────────


def test_browser_flow_simulation(tmp_path):
    """Build a zip the way a user would, unpack it, remap, validate.
    Mirrors the JS DRIVER_SCRIPT flow without Pyodide."""
    # Create a stand-in "bundle": sidecar + keyring + one reachable file
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    # Use the real example-clip + keyring from the repo
    shutil.copy(REPO / "example-clip.omc.json", bundle_dir / "example-clip.omc.json")
    shutil.copy(REPO / "keyring.json",          bundle_dir / "keyring.json")
    # Flatten required artifacts into the bundle root so the remap has
    # something to match against.
    from_repo = {
        "A001_C042_0420AB.ari":  "Camera/A001/A001_C042_0420AB.ari",
        "A001_C042_0420AB.mhl":  "Camera/A001/A001_C042_0420AB.mhl",
        "A001_C042_0420AB.amf":  "amf/A001_C042_0420AB.amf",
        "A001_C042_0420AB.fdl":  "fdl/A001_C042_0420AB.fdl",
        "A001_C042_0420AB.mov":  "proxy/A001_C042_0420AB.mov",
    }
    for target_name, repo_rel in from_repo.items():
        src = REPO / repo_rel
        if src.exists():
            shutil.copy(src, bundle_dir / target_name)

    # Pack into a zip (as a user would drop)
    zip_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for p in bundle_dir.iterdir():
            zf.write(p, arcname=p.name)

    # Simulate /work/ extraction
    work = tmp_path / "work"; work.mkdir()
    shutil.copy(zip_path, work / "bundle.zip")
    with zipfile.ZipFile(work / "bundle.zip") as zf:
        zf.extractall(work)

    # Run the remap driver (mirrors DRIVER_SCRIPT)
    sidecars = list(work.rglob("*.omc.json"))
    assert sidecars, "bundle lost the sidecar somehow"
    sidecar_path = sidecars[0]

    index = web_remap.build_basename_index(work)
    doc = json.loads(sidecar_path.read_text())
    web_remap.remap_artifact_paths(doc, index)
    sidecar_path.write_text(json.dumps(doc))

    keyring_path = next(iter(work.rglob("keyring.json")), None)

    report = validate_as_json(
        sidecar_path,
        base_dir        = work,
        keyring_path    = keyring_path,
        missing_is_skip = True,
    )

    # Signatures + chain integrity + schema must all pass — these are the
    # stages a cryptographic audit actually cares about.
    by_stage = {s["stage"]: s for s in report["stages"]}
    assert by_stage["1"]["status"] == "pass"  # OMC schema
    assert by_stage["2"]["status"] == "pass"  # DWC schemas
    assert by_stage["3"]["status"] == "pass"  # chain integrity
    assert by_stage["4"]["status"] == "pass", by_stage["4"]["lines"]  # signatures
    assert by_stage["5"]["status"] == "pass"  # lock crosscheck
    assert by_stage["7"]["status"] == "pass"  # OMC x-controlledValues
