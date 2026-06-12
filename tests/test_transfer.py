"""Two-party transfer counter-signing (Phase 3).

Stage 3.5 (d) structural binding is exercised by mutating the reel's C043
transfer/accept pair; the offer→accept CLI round-trip (incl. the verify-then-sign
gate) is exercised on a freshly bootstrapped sidecar."""
import json
import sys
from pathlib import Path

import pytest

from dwc_sidecar.validate import _iter_assets, _asset_payloads, validate_as_json

REPO_ROOT = Path(__file__).resolve().parent.parent
REEL      = REPO_ROOT / "example-reel.omc.json"
C043_UUID = "0190a5e2-7c3f-7b4a-a1b2-3c4d5e6f7891"


@pytest.fixture
def repo_cwd(monkeypatch):
    monkeypatch.chdir(REPO_ROOT)


def _c043_events(doc):
    for a in _iter_assets(doc):
        if any(i.get("identifierValue") == C043_UUID for i in a.get("identifier", [])):
            return _asset_payloads(a)["dwc.sidecar.events"]["value"]
    raise AssertionError("C043 asset not found")


def _write(tmp_path, doc):
    p = tmp_path / "reel.omc.json"
    p.write_text(json.dumps(doc, indent=2))
    return p


def _stage35(dst, **kw):
    report = validate_as_json(dst, base_dir=REPO_ROOT, **kw)
    return next(s for s in report["stages"] if s["stage"] == "3.5")


# --- the stub reel is the canonical happy path -------------------------------

def test_reel_transfer_pair_validates(repo_cwd):
    report = validate_as_json(REEL)
    assert report["errors"] == 0
    events = _c043_events(json.loads(REEL.read_text()))
    assert any(e["action"] == "accept" for e in events)
    assert any(e["action"] == "transfer" for e in events)


def test_wrong_receiver_actor_fails(tmp_path, repo_cwd):
    doc = json.loads(REEL.read_text())
    ac = next(e for e in _c043_events(doc) if e["action"] == "accept")
    ac["actor"]["id"] = "urn:email:colorist@the-dwc.com"  # not the offer's receiver
    s = _stage35(_write(tmp_path, doc))
    assert s["status"] == "fail"
    assert any("is not the offer's receiver" in ln for ln in s["lines"])


def test_wrong_of_hash_fails(tmp_path, repo_cwd):
    doc = json.loads(REEL.read_text())
    ac = next(e for e in _c043_events(doc) if e["action"] == "accept")
    ac["transfer"]["of"] = "sha256:" + "1" * 64
    s = _stage35(_write(tmp_path, doc))
    assert s["status"] == "fail"
    assert any("does not reference a transfer offer" in ln for ln in s["lines"])


def test_same_kid_self_accept_fails(tmp_path, repo_cwd):
    doc = json.loads(REEL.read_text())
    ac = next(e for e in _c043_events(doc) if e["action"] == "accept")
    ac["sig"]["kid"] = "dwc-dit-01"  # same kid as the offer
    s = _stage35(_write(tmp_path, doc))
    assert s["status"] == "fail"
    assert any("self-acceptance" in ln for ln in s["lines"])


def test_accept_with_no_offer_fails(tmp_path, repo_cwd):
    doc = json.loads(REEL.read_text())
    events = _c043_events(doc)
    # Drop the transfer offer; the dangling accept must FAIL structurally.
    events[:] = [e for e in events if e["action"] != "transfer"]
    s = _stage35(_write(tmp_path, doc))
    assert s["status"] == "fail"
    assert any("does not reference a transfer offer" in ln for ln in s["lines"])


# --- offer / accept CLI round-trip + verify-then-sign gate -------------------

def _bootstrap(tmp_path, monkeypatch):
    from dwc_sidecar import bootstrap
    clip = tmp_path / "B010_C003.mov"
    clip.write_bytes(b"venice clip bytes\n")
    sidecar = tmp_path / "out.omc.json"
    monkeypatch.setattr(sys, "argv", [
        "dwc bootstrap", "--clip", str(clip), "--base-dir", str(tmp_path),
        "--actor", "dit@the-dwc.com", "--signing-kid", "dwc-dit-01",
        "--out", str(sidecar)])
    assert bootstrap.main() == 0
    return clip, sidecar


def test_offer_then_accept_round_trip(tmp_path, repo_cwd, monkeypatch):
    from dwc_sidecar import transfer
    _clip, sidecar = _bootstrap(tmp_path, monkeypatch)

    monkeypatch.setattr(sys, "argv", [
        "dwc transfer", "offer", str(sidecar),
        "--to", "post@the-dwc.com", "--signing-kid", "dwc-dit-01"])
    assert transfer.main() == 0

    # A pending offer is a WARN, not an error — validation still passes (exit 0).
    rep = validate_as_json(sidecar, base_dir=tmp_path)
    assert rep["errors"] == 0
    s35 = next(s for s in rep["stages"] if s["stage"] == "3.5")
    assert s35["status"] == "warn"
    assert any("transfer in flight" in ln for ln in s35["lines"])

    monkeypatch.setattr(sys, "argv", [
        "dwc transfer", "accept", str(sidecar),
        "--signing-kid", "dwc-post-01", "--base-dir", str(tmp_path)])
    assert transfer.main() == 0

    rep2 = validate_as_json(sidecar, base_dir=tmp_path)
    assert rep2["errors"] == 0
    assert next(s for s in rep2["stages"] if s["stage"] == "3.5")["status"] == "pass"


def test_accept_refuses_on_artifact_tamper(tmp_path, repo_cwd, monkeypatch):
    from dwc_sidecar import transfer
    clip, sidecar = _bootstrap(tmp_path, monkeypatch)

    monkeypatch.setattr(sys, "argv", [
        "dwc transfer", "offer", str(sidecar),
        "--to", "post@the-dwc.com", "--signing-kid", "dwc-dit-01"])
    assert transfer.main() == 0

    clip.write_bytes(b"TAMPERED at the receiving site\n")  # Stage 6 will mismatch

    monkeypatch.setattr(sys, "argv", [
        "dwc transfer", "accept", str(sidecar),
        "--signing-kid", "dwc-post-01", "--base-dir", str(tmp_path)])
    assert transfer.main() == 1
    # No accept event was appended.
    events = next(e["value"] for a in _iter_assets(json.loads(sidecar.read_text()))
                  for e in _asset_payloads(a).values()
                  if e.get("domain") == "dwc.sidecar.events")
    assert not any(e["action"] == "accept" for e in events)


def test_accept_refuses_wrong_actor_key(tmp_path, repo_cwd, monkeypatch):
    from dwc_sidecar import transfer
    _clip, sidecar = _bootstrap(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", [
        "dwc transfer", "offer", str(sidecar),
        "--to", "post@the-dwc.com", "--signing-kid", "dwc-dit-01"])
    assert transfer.main() == 0
    # dwc-color-01 is bound to colorist@, not the offer's receiver post@ — refuse.
    monkeypatch.setattr(sys, "argv", [
        "dwc transfer", "accept", str(sidecar),
        "--signing-kid", "dwc-color-01", "--base-dir", str(tmp_path)])
    assert transfer.main() == 1
