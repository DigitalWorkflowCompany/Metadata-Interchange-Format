"""Parity and contract tests for validate_as_json().

Locks in the shape the web validator (§4) and `dwc doctor` (§2) consume,
and proves the JSON and CLI entry points call the same stage functions —
if they drift, the menu-bar app would show different pass/fail counts than
the CLI the DIT is reading.
"""
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from dwc_sidecar.validate import (
    _print_results,
    load,
    main,
    validate_as_json,
)

REPO_ROOT    = Path(__file__).resolve().parent.parent
EXAMPLE_CLIP = REPO_ROOT / "example-clip.omc.json"
EXAMPLE_REEL = REPO_ROOT / "example-reel.omc.json"


@pytest.fixture(autouse=True)
def _chdir_repo_root(monkeypatch):
    # validate_signatures() resolves keyring.json via CWD-relative Path("keyring.json").
    # The stub keyring lives at the repo root; pin CWD there so tests are location-safe.
    monkeypatch.chdir(REPO_ROOT)


def test_returns_expected_shape():
    report = validate_as_json(EXAMPLE_CLIP)
    assert set(report) == {"target", "base_dir", "stages", "errors", "summary"}
    assert isinstance(report["stages"], list)
    assert len(report["stages"]) == 9

    stage_nums = [s["stage"] for s in report["stages"]]
    assert stage_nums == ["1", "2", "3", "4", "5", "6", "7", "8", "9"]

    for s in report["stages"]:
        assert set(s) == {"stage", "title", "status", "errors", "warnings", "lines"}
        assert s["status"] in {"pass", "warn", "fail"}
        assert isinstance(s["lines"], list)
        assert isinstance(s["errors"], int)


def test_stub_clip_passes_all_stages():
    report = validate_as_json(EXAMPLE_CLIP)
    assert report["errors"] == 0
    assert report["summary"] == "OK"
    for s in report["stages"]:
        assert s["status"] != "fail", f"stage {s['stage']} failed: {s['lines']}"


def test_stub_reel_passes_all_stages():
    report = validate_as_json(EXAMPLE_REEL)
    assert report["errors"] == 0
    assert report["summary"] == "OK"


def test_check_hosted_inserts_stage_2_5_slot():
    # Stage 2.5 hits the network; we assert only on slot placement here.
    # The slot's status depends on network reachability, which is tested in CI.
    report = validate_as_json(EXAMPLE_CLIP, check_hosted=True)
    stage_nums = [s["stage"] for s in report["stages"]]
    assert stage_nums == ["1", "2", "2.5", "3", "4", "5", "6", "7", "8", "9"]


def test_parity_with_cli_main(capsys):
    """CLI and JSON path call the same stage functions — their per-stage
    text must be identical. Header and SUMMARY are CLI-only."""
    exit_code = main(["dwc validate", str(EXAMPLE_CLIP)])
    cli_stdout = capsys.readouterr().out
    assert exit_code == 0

    report = validate_as_json(EXAMPLE_CLIP)
    buf = io.StringIO()
    with redirect_stdout(buf):
        _print_results(report["stages"])
    json_stdout = buf.getvalue()

    # CLI output = "→ <target>\n  base-dir: <base>\n\n" + json_stdout + "SUMMARY: OK\n"
    assert json_stdout in cli_stdout
    assert cli_stdout.endswith("SUMMARY: OK\n")


def test_base_dir_override_without_chdir(tmp_path):
    """Web validator contract: the sidecar file lives at an arbitrary path,
    its artifact references are resolved against an explicit base_dir."""
    doc = load(EXAMPLE_CLIP)
    relocated = tmp_path / "relocated.omc.json"
    relocated.write_text(json.dumps(doc))

    # Without base_dir override → artifacts resolve to tmp_path, where they don't exist
    report_missing = validate_as_json(relocated)
    stage_6 = next(s for s in report_missing["stages"] if s["stage"] == "6")
    assert stage_6["status"] == "fail"

    # With explicit base_dir → artifacts resolve against the repo root and pass
    report_ok = validate_as_json(relocated, base_dir=REPO_ROOT)
    stage_6_ok = next(s for s in report_ok["stages"] if s["stage"] == "6")
    assert stage_6_ok["status"] == "pass"


def test_does_not_chdir():
    """validate_as_json must not touch process CWD — Pyodide's single shared
    CWD would race between concurrent drop-zone validations (§4.4a)."""
    cwd_before = Path.cwd()
    validate_as_json(EXAMPLE_CLIP)
    assert Path.cwd() == cwd_before


def test_report_is_json_serializable():
    """The web validator ships the dict across the JS/Python boundary via
    json.dumps. Anything non-serializable (Path, datetime, set) breaks that."""
    report = validate_as_json(EXAMPLE_CLIP)
    json.dumps(report)


def test_errors_propagate_to_top_level(tmp_path):
    """Break chain continuity → Stage 3 FAIL → top-level errors > 0."""
    doc = load(EXAMPLE_CLIP)
    # Bump seq on the first event so the chain is non-contiguous.
    for asset in doc["Asset"]:
        for cd in asset["assetFC"]["functionalProperties"]["customData"]:
            if cd["domain"] == "dwc.sidecar.events" and cd["value"]:
                cd["value"][0]["seq"] = 99
                break

    broken = tmp_path / "broken.omc.json"
    broken.write_text(json.dumps(doc))
    report = validate_as_json(broken, base_dir=REPO_ROOT)

    assert report["errors"] > 0
    assert report["summary"].startswith("FAIL")
    stage_3 = next(s for s in report["stages"] if s["stage"] == "3")
    assert stage_3["status"] == "fail"
    assert stage_3["errors"] > 0


def test_stage_line_format_preserved():
    """The CLI contract includes line-level text (subprocess callers in
    watch.py / mhl_walker.py / batch.py grab stdout.splitlines()[-2:] for
    error display). Lock a sample line to catch accidental wording drift."""
    report = validate_as_json(EXAMPLE_CLIP)
    stage_1 = next(s for s in report["stages"] if s["stage"] == "1")
    assert stage_1["lines"] == ["Stage 1 — OMC v2.8: OK"]
