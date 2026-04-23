#!/usr/bin/env python3
"""Single source of truth for the menu-bar app's test fixtures (plan §3.8a).

The Swift decoder tests read canonical fixture JSON files from
``macos-statusbar/Tests/DwcStatusTests/Fixtures/``. Those files are
generated from this Python script so shapes stay in lockstep with
what ``dwc doctor --json`` and ``.watch-state.json`` actually emit.

CI runs with ``--check`` which writes to a tempdir and diffs against the
committed fixtures; any drift fails the workflow.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO        = Path(__file__).resolve().parent.parent.parent
FIXTURE_DIR = REPO / "macos-statusbar" / "Tests" / "DwcStatusTests" / "Fixtures"


# ── DoctorReport fixtures ───────────────────────────────────────────────
# Shape is ``{status, checks: [{status,title,detail,remedy}]}`` —
# see ``dwc_sidecar.doctor.format_json``.

def _check(status: str, title: str, detail: str = "", remedy: str = "") -> dict:
    return {"status": status, "title": title, "detail": detail, "remedy": remedy}


DOCTOR_ALL_PASS = {
    "status": "pass",
    "checks": [
        _check("pass", "Python version",         "3.12.3 ≥ 3.11"),
        _check("pass", "Required packages",      "5 packages importable"),
        _check("pass", "Hash algorithms",        "1 alg(s) referenced and available"),
        _check("pass", "keyring.json",           "3 kid(s)"),
        _check("pass", "Keyring validity windows","all 3 referenced kid(s) within window"),
        _check("pass", "Signer config (DWC_SIGNERS)", "unset — will use JsonFileSigner against keys.priv.json"),
        _check("pass", "Plaintext private keys", "using keys.priv.json default (dev mode)"),
        _check("pass", ".watch-state.json",      "not present (no watcher running here)"),
        _check("pass", "Sidecar parse",          "2 sidecar(s) parsed with a dwc.sidecar.* block"),
        _check("pass", "Key expiry window",      "no kids expire within 30d"),
    ],
}

DOCTOR_MIXED = {
    "status": "warn",
    "checks": [
        _check("pass", "Python version",         "3.12.3 ≥ 3.11"),
        _check("pass", "Required packages",      "5 packages importable"),
        _check("warn", "Key expiry window",      "expiring within 30d: dwc-dit-01 (14d remaining)",
               remedy="Plan a rotation — keys are immutable once published"),
        _check("pass", "keyring.json",           "2 kid(s)"),
    ],
}

DOCTOR_FAILING = {
    "status": "fail",
    "checks": [
        _check("pass", "Python version",         "3.12.3 ≥ 3.11"),
        _check("fail", "keyring.json",           "/production/keyring.json not found",
               remedy="Run `dwc init` or copy keyring.json from your deployment"),
        _check("warn", "Hosted schema drift",    "could not verify — network unavailable",
               remedy="If behind a proxy, check egress to ns.the-dwc.com"),
    ],
}


# ── WatchState fixtures ─────────────────────────────────────────────────
# Shape is what ``src/dwc_sidecar/watch.py::_save_state`` emits.

WATCHSTATE_FULL = {
    "processed_mhl_sha256": [
        "9fdeaa4e1cb2c95b8db02c5e8e0a4f8b1e3a2c9e0b5d4a6f2e1c8d7b9f3a0e6b",
        "a02bef85fe9131f7adc418cb6b38e6cf2395e4f1af14eba37cd1e0dcc8e2f3a0",
    ],
    "emitted": [
        {"clipName": "A001_C040_0420XY",
         "omcPath":  "/Volumes/Mag_A001/sidecars/A001_C040_0420XY.omc.json",
         "signedAt": "2026-04-23T09:58:00Z",
         "status":   "signed"},
        {"clipName": "A001_C041_0420ZA",
         "omcPath":  "/Volumes/Mag_A001/sidecars/A001_C041_0420ZA.omc.json",
         "signedAt": "2026-04-23T09:59:11Z",
         "status":   "signed"},
        {"clipName": "A001_C042_0420AB",
         "omcPath":  "/Volumes/Mag_A001/sidecars/A001_C042_0420AB.omc.json",
         "signedAt": "2026-04-23T10:00:23Z",
         "status":   "signed"},
    ],
    "savedAt": "2026-04-23T10:00:24Z",
}

WATCHSTATE_LEGACY = {
    # Pre-§1.8 watcher: no `emitted` field at all
    "processed_mhl_sha256": ["deadbeefcafe0123456789abcdef0123456789abcdef0123"],
    "savedAt": "2026-02-10T22:15:00Z",
}

WATCHSTATE_MIXED_STATUS = {
    "processed_mhl_sha256": ["11" * 32],
    "emitted": [
        {"clipName": "A001_C042_0420AB",
         "omcPath":  "/x/A001_C042_0420AB.omc.json",
         "signedAt": "2026-04-23T10:00:00Z",
         "status":   "signed"},
        {"clipName": "A001_C043_0420CD",
         "omcPath":  "/x/A001_C043_0420CD.omc.json",
         "signedAt": "2026-04-23T10:01:00Z",
         "status":   "quarantined"},
        {"clipName": "A001_C044_0420EF",
         "omcPath":  "/x/A001_C044_0420EF.omc.json",
         "signedAt": "2026-04-23T10:02:00Z",
         "status":   "signed"},
        {"clipName": "A001_C045_0420GH",
         "omcPath":  "/x/A001_C045_0420GH.omc.json",
         "signedAt": "2026-04-23T10:03:00Z",
         "status":   "quarantined"},
    ],
    "savedAt": "2026-04-23T10:03:01Z",
}


FIXTURES: dict[str, dict] = {
    "doctor_all_pass":          DOCTOR_ALL_PASS,
    "doctor_mixed":             DOCTOR_MIXED,
    "doctor_failing":           DOCTOR_FAILING,
    "watchstate_full":          WATCHSTATE_FULL,
    "watchstate_legacy":        WATCHSTATE_LEGACY,
    "watchstate_mixed_status":  WATCHSTATE_MIXED_STATUS,
}


def _serialize(obj: dict) -> str:
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def write_fixtures(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name, obj in FIXTURES.items():
        (dest / f"{name}.json").write_text(_serialize(obj))


def check_fixtures() -> int:
    """Compare the committed Swift fixtures against what this script would
    produce; exit 0 if identical, 1 on drift."""
    with tempfile.TemporaryDirectory() as td:
        staging = Path(td)
        write_fixtures(staging)
        diffs: list[str] = []
        for name in FIXTURES:
            want  = (staging     / f"{name}.json").read_text()
            have  = (FIXTURE_DIR / f"{name}.json").read_text() \
                    if (FIXTURE_DIR / f"{name}.json").exists() else ""
            if want != have:
                diffs.append(name)
        if diffs:
            print("Fixture drift in:", ", ".join(diffs), file=sys.stderr)
            print("Run: python3 tools/macos-statusbar/sync_fixtures.py",
                  file=sys.stderr)
            return 1
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate/verify the menu-bar app's JSON test fixtures.")
    ap.add_argument("--check", action="store_true",
                    help="Compare committed fixtures against generator output; "
                         "exit 1 on drift. Used by CI.")
    args = ap.parse_args()
    if args.check:
        return check_fixtures()
    write_fixtures(FIXTURE_DIR)
    print(f"Wrote {len(FIXTURES)} fixtures → {FIXTURE_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
