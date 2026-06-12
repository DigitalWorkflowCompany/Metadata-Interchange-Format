"""m-of-n threshold locks (Phase 2): schema + Stage 5 verification + CLI round-trip.

The example-clip stub now carries a real 2-of-2 lock (dwc-color-01 + dwc-post-01)
with both backing lock events, so most cases are constructed by mutating it. Any
edit to the lock body (policy, by, …) invalidates every co-signature by design,
so helper `_resign` re-creates the sigs whenever the body legitimately changes.
"""
import base64
import json
import sys
from pathlib import Path

import pytest

from dwc_sidecar.append import append_event
from dwc_sidecar.canonical import canonical_bytes
from dwc_sidecar.signers.jsonfile import JsonFileSigner
from dwc_sidecar.validate import _asset_payloads, validate_as_json

REPO_ROOT = Path(__file__).resolve().parent.parent
CLIP      = REPO_ROOT / "example-clip.omc.json"
PRIV      = REPO_ROOT / "keys.priv.json"
CLIP_UUID = "0190a5e2-7c3f-7b4a-a1b2-3c4d5e6f7890"
AMF       = "urn:uuid:af8a0002-0000-0000-0000-000000000002"


@pytest.fixture
def repo_cwd(monkeypatch):
    monkeypatch.chdir(REPO_ROOT)


def _resign(lock: dict, kids) -> None:
    """Set lock['sigs'] to fresh co-signatures over JCS(lock minus sig/sigs)."""
    lock.pop("sig", None)
    lock["sigs"] = []
    body = {k: v for k, v in lock.items() if k != "sigs"}
    lock["sigs"] = [
        {"alg": "ed25519", "kid": kid,
         "value": base64.b64encode(JsonFileSigner(kid, PRIV).sign(canonical_bytes(body))).decode()}
        for kid in kids
    ]


def _ocf(doc):
    for a in doc["Asset"]:
        if any(i.get("identifierValue") == CLIP_UUID for i in a.get("identifier", [])):
            return a
    raise AssertionError("OCF asset not found")


def _lock_and_events(doc):
    asset = _ocf(doc)
    p = _asset_payloads(asset)
    return asset, p["dwc.sidecar.locks"]["value"][0], p["dwc.sidecar.events"]["value"]


def _write(tmp_path, doc) -> Path:
    dst = tmp_path / "clip.omc.json"
    dst.write_text(json.dumps(doc, indent=2))
    return dst


def _stage5(dst, **kw):
    report = validate_as_json(dst, base_dir=REPO_ROOT, **kw)
    return next(s for s in report["stages"] if s["stage"] == "5")


def _add_lock_event(asset, kid, actor):
    append_event(asset, {
        "actor": {"id": actor, "role": "Locker"},
        "tool":  {"name": "pytest", "version": "0"},
        "action": "lock", "target": AMF,
    }, JsonFileSigner(kid, PRIV))


# --- the stub itself is the canonical 2-of-2 happy path -----------------------

def test_stub_two_of_two_passes(repo_cwd):
    assert _stage5(CLIP)["status"] == "pass"


def test_two_of_three_two_valid_cosigs(tmp_path, repo_cwd):
    doc = json.loads(CLIP.read_text())
    asset, lock, _ = _lock_and_events(doc)
    _add_lock_event(asset, "dwc-dit-01", "urn:email:dit@the-dwc.com")
    lock["policy"] = {"m": 2, "of": ["dwc-color-01", "dwc-post-01", "dwc-dit-01"]}
    _resign(lock, ["dwc-color-01", "dwc-post-01"])
    assert _stage5(_write(tmp_path, doc))["status"] == "pass"


def test_two_of_three_one_sig_fails(tmp_path, repo_cwd):
    doc = json.loads(CLIP.read_text())
    _, lock, _ = _lock_and_events(doc)
    lock["policy"] = {"m": 2, "of": ["dwc-color-01", "dwc-post-01", "dwc-dit-01"]}
    _resign(lock, ["dwc-color-01"])
    s5 = _stage5(_write(tmp_path, doc))
    assert s5["status"] == "fail"
    assert any("threshold not met (1 of 2)" in ln for ln in s5["lines"])


def test_duplicate_kid_counts_once(tmp_path, repo_cwd):
    doc = json.loads(CLIP.read_text())
    _, lock, _ = _lock_and_events(doc)
    _resign(lock, ["dwc-color-01", "dwc-color-01"])  # same kid twice
    s5 = _stage5(_write(tmp_path, doc))
    assert s5["status"] == "fail"
    assert any("threshold not met (1 of 2)" in ln for ln in s5["lines"])


def test_cosig_not_in_policy_of_fails(tmp_path, repo_cwd):
    doc = json.loads(CLIP.read_text())
    asset, lock, _ = _lock_and_events(doc)
    _add_lock_event(asset, "dwc-dit-01", "urn:email:dit@the-dwc.com")
    # dit signs but is not listed in policy.of
    lock["policy"] = {"m": 2, "of": ["dwc-color-01", "dwc-post-01"]}
    _resign(lock, ["dwc-color-01", "dwc-dit-01"])
    s5 = _stage5(_write(tmp_path, doc))
    assert s5["status"] == "fail"
    assert any("is not in policy.of" in ln for ln in s5["lines"])


def test_cosig_without_matching_event_fails(tmp_path, repo_cwd):
    doc = json.loads(CLIP.read_text())
    _, lock, _ = _lock_and_events(doc)
    # dit is in policy.of and signs, but there is no dit lock event in the asset.
    lock["policy"] = {"m": 2, "of": ["dwc-color-01", "dwc-post-01", "dwc-dit-01"]}
    _resign(lock, ["dwc-color-01", "dwc-dit-01"])
    s5 = _stage5(_write(tmp_path, doc))
    assert s5["status"] == "fail"
    assert any("no matching signed lock event" in ln for ln in s5["lines"])


def test_edit_policy_m_invalidates_sigs(tmp_path, repo_cwd):
    doc = json.loads(CLIP.read_text())
    _, lock, _ = _lock_and_events(doc)
    # Tamper m 2 -> 1 *after* signing: every co-signature now covers stale bytes.
    lock["policy"]["m"] = 1
    s5 = _stage5(_write(tmp_path, doc))
    assert s5["status"] == "fail"
    assert any("signature" in ln.lower() for ln in s5["lines"])


def test_keyring_policy_floor_blocks_one_of_one(tmp_path, repo_cwd):
    doc = json.loads(CLIP.read_text())
    _, lock, _ = _lock_and_events(doc)
    lock["policy"] = {"m": 1, "of": ["dwc-color-01"]}
    _resign(lock, ["dwc-color-01"])
    dst = _write(tmp_path, doc)

    keyring = json.loads((REPO_ROOT / "keyring.json").read_text())
    keyring["policies"] = {"lock": {"artifact": {"m": 2, "of": ["dwc-color-01", "dwc-post-01"]}}}
    kr = tmp_path / "keyring.json"
    kr.write_text(json.dumps(keyring))

    s5 = _stage5(dst, keyring_path=kr)
    assert s5["status"] == "fail"
    assert any("policy-downgrade defense" in ln for ln in s5["lines"])


def test_legacy_single_sig_still_passes(tmp_path, repo_cwd):
    doc = json.loads(CLIP.read_text())
    _, lock, _ = _lock_and_events(doc)
    # Replace the threshold lock with a legacy single-sig lock backed by seq3 (post).
    lock.pop("policy", None)
    lock.pop("sigs", None)
    body = {"target": AMF, "scope": "artifact",
            "by": "urn:email:post@the-dwc.com", "at": "2026-04-20T18:00:00Z",
            "reason": lock.get("reason", "")}
    lock.clear()
    lock.update(body)
    lock["sig"] = {"alg": "ed25519", "kid": "dwc-post-01",
                   "value": base64.b64encode(
                       JsonFileSigner("dwc-post-01", PRIV).sign(canonical_bytes(lock))).decode()}
    assert _stage5(_write(tmp_path, doc))["status"] == "pass"


def test_revoked_cosigner_fails(tmp_path, repo_cwd):
    doc = json.loads(CLIP.read_text())
    dst = _write(tmp_path, doc)  # unmodified 2-of-2
    keyring = json.loads((REPO_ROOT / "keyring.json").read_text())
    keyring["keys"]["dwc-post-01"]["revokedAt"] = "2026-01-02T00:00:00Z"  # before the lock ts
    keyring["keys"]["dwc-post-01"]["revocationReason"] = "test"
    kr = tmp_path / "keyring.json"
    kr.write_text(json.dumps(keyring))
    s5 = _stage5(dst, keyring_path=kr)
    assert s5["status"] == "fail"


def test_cli_lock_and_cosign_round_trip(tmp_path, repo_cwd, monkeypatch):
    """dwc lock + dwc lock cosign on a freshly bootstrapped sidecar → 10/10."""
    from dwc_sidecar import bootstrap, lock

    clip = tmp_path / "A050_C001.mov"
    clip.write_bytes(b"fake clip bytes for hashing\n")
    sidecar = tmp_path / "out.omc.json"

    monkeypatch.setattr(sys, "argv", [
        "dwc bootstrap", "--clip", str(clip), "--base-dir", str(tmp_path),
        "--actor", "dit@the-dwc.com", "--signing-kid", "dwc-dit-01",
        "--out", str(sidecar)])
    assert bootstrap.main() == 0

    clip_uuid = next(i["identifierValue"]
                     for a in json.loads(sidecar.read_text())["Asset"]
                     for i in a["identifier"] if i["identifierScope"] == "dwc:clip-uuid")
    target = f"urn:uuid:{clip_uuid}"

    monkeypatch.setattr(sys, "argv", [
        "dwc lock", str(sidecar), "--target", target, "--scope", "entity",
        "--policy", "2", "--of", "dwc-dit-01,dwc-post-01",
        "--signing-kid", "dwc-dit-01", "--reason", "two-party freeze"])
    assert lock.main() == 0

    monkeypatch.setattr(sys, "argv", [
        "dwc lock", "cosign", str(sidecar), "--target", target,
        "--signing-kid", "dwc-post-01"])
    assert lock.main() == 0

    report = validate_as_json(sidecar, base_dir=tmp_path)
    assert report["errors"] == 0, report["summary"]
    assert next(s for s in report["stages"] if s["stage"] == "5")["status"] == "pass"
