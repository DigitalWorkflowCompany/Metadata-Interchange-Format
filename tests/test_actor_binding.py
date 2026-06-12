"""Stage 4 kid↔actor binding (Phase 1.2).

When the keyring binds a key to an actor URN, any event signed by that key but
claiming a different actor.id is a FAIL — even though the signature itself
verifies. Without this, a single trusted key can impersonate any actor, and
'distinct parties' (threshold locks, transfer) means nothing."""
import shutil
from pathlib import Path

import pytest

from dwc_sidecar.append import append_event, load_sidecar_asset, save_sidecar
from dwc_sidecar.signers.jsonfile import JsonFileSigner
from dwc_sidecar.validate import validate_as_json

REPO_ROOT = Path(__file__).resolve().parent.parent
CLIP      = REPO_ROOT / "example-clip.omc.json"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    return tmp_path


def _stage4(report):
    return next(s for s in report["stages"] if s["stage"] == "4")


def test_matching_actor_passes(workspace):
    dst = workspace / "clip.omc.json"
    shutil.copyfile(CLIP, dst)
    doc, asset = load_sidecar_asset(dst, clip_uuid="0190a5e2-7c3f-7b4a-a1b2-3c4d5e6f7890")
    append_event(asset, {
        "actor": {"id": "urn:email:dit@the-dwc.com", "role": "DIT"},
        "tool":  {"name": "pytest", "version": "0"},
        "action": "verify",
        "target": "urn:uuid:0190a5e2-7c3f-7b4a-a1b2-3c4d5e6f7890",
    }, JsonFileSigner("dwc-dit-01", REPO_ROOT / "keys.priv.json"))
    save_sidecar(dst, doc)
    assert _stage4(validate_as_json(dst, base_dir=REPO_ROOT))["status"] == "pass"


def test_mismatched_actor_fails(workspace):
    dst = workspace / "clip.omc.json"
    shutil.copyfile(CLIP, dst)
    doc, asset = load_sidecar_asset(dst, clip_uuid="0190a5e2-7c3f-7b4a-a1b2-3c4d5e6f7890")
    # dwc-dit-01 is bound to dit@; signing an event as colorist@ must FAIL Stage 4
    # even though the signature is cryptographically valid.
    append_event(asset, {
        "actor": {"id": "urn:email:colorist@the-dwc.com", "role": "DIT"},
        "tool":  {"name": "pytest", "version": "0"},
        "action": "verify",
        "target": "urn:uuid:0190a5e2-7c3f-7b4a-a1b2-3c4d5e6f7890",
    }, JsonFileSigner("dwc-dit-01", REPO_ROOT / "keys.priv.json"))
    save_sidecar(dst, doc)
    stage4 = _stage4(validate_as_json(dst, base_dir=REPO_ROOT))
    assert stage4["status"] == "fail"
    assert any("does not match the actor bound" in ln for ln in stage4["lines"])
