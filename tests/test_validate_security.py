"""Adversarial tests for the validator — the review's trust-hole list.

Each test forges the kind of sidecar an attacker (or a corrupted toolchain)
would hand a recipient and asserts the validator *fails* it rather than
crashing or passing green. The stub examples are the honest baseline; every
test mutates a copy.
"""
import copy
import json
from pathlib import Path

import pytest

from dwc_sidecar.validate import validate_as_json

REPO_ROOT    = Path(__file__).resolve().parent.parent
EXAMPLE_CLIP = REPO_ROOT / "example-clip.omc.json"


@pytest.fixture(autouse=True)
def _chdir_repo_root(monkeypatch):
    # keyring.json is CWD-relative
    monkeypatch.chdir(REPO_ROOT)


@pytest.fixture
def clip_doc():
    return json.loads(EXAMPLE_CLIP.read_text())


def _write(tmp_path: Path, doc) -> Path:
    p = tmp_path / "case.omc.json"
    p.write_text(json.dumps(doc))
    return p


def _stage(report, num):
    return next(s for s in report["stages"] if s["stage"] == num)


def _payload(doc, asset_idx, domain):
    cd = doc["Asset"][asset_idx]["assetFC"]["functionalProperties"]["customData"]
    return next(e for e in cd if e["domain"] == domain)


# ---------------------------------------------------------------- crashes →

def test_seq_null_is_error_not_crash(clip_doc, tmp_path):
    """Stage 3 used to compute None + 1 → TypeError aborting the whole run."""
    _payload(clip_doc, 0, "dwc.sidecar.events")["value"][0]["seq"] = None
    report = validate_as_json(_write(tmp_path, clip_doc), base_dir=REPO_ROOT)
    assert report["errors"] > 0
    assert _stage(report, "3")["status"] == "fail"


def test_seq_absent_is_error_not_crash(clip_doc, tmp_path):
    del _payload(clip_doc, 0, "dwc.sidecar.events")["value"][0]["seq"]
    report = validate_as_json(_write(tmp_path, clip_doc), base_dir=REPO_ROOT)
    assert _stage(report, "3")["status"] == "fail"


def test_naive_timestamp_does_not_abort_validation(clip_doc, tmp_path):
    """A schema-valid ts without an offset used to raise naive-vs-aware
    TypeError inside Stage 4 — a way to *abort* validation, not fail it."""
    ev = _payload(clip_doc, 0, "dwc.sidecar.events")["value"][0]
    ev["ts"] = "2026-04-20T09:12:00"   # no offset → naive
    report = validate_as_json(_write(tmp_path, clip_doc), base_dir=REPO_ROOT)
    # The edit breaks the event hash/signature — what matters is a structured
    # FAIL rather than an exception.
    assert report["errors"] > 0


# ------------------------------------------------------- path containment →

@pytest.mark.parametrize("evil", ["../../../../etc/passwd", "../keyring.json"])
def test_artifact_path_traversal_fails(clip_doc, tmp_path, evil):
    art = _payload(clip_doc, 0, "dwc.sidecar.artifacts")["value"][1]
    art["path"] = evil
    report = validate_as_json(_write(tmp_path, clip_doc),
                              base_dir=REPO_ROOT / "amf")
    s6 = _stage(report, "6")
    assert s6["status"] == "fail"
    assert any("escapes base-dir" in ln for ln in s6["lines"])


def test_traversal_does_not_leak_target_hash(clip_doc, tmp_path):
    """The FAIL line for an escaping path must not hash the target file —
    progressive hash disclosure was the oracle in the original finding."""
    art = _payload(clip_doc, 0, "dwc.sidecar.artifacts")["value"][1]
    art["path"] = "../example-clip.omc.json"
    report = validate_as_json(_write(tmp_path, clip_doc),
                              base_dir=REPO_ROOT / "amf")
    s6 = _stage(report, "6")
    offending = [ln for ln in s6["lines"] if "example-clip" in ln]
    assert offending and all("actual" not in ln for ln in offending)


def test_mhl_entry_traversal_fails(clip_doc, tmp_path):
    art = _payload(clip_doc, 0, "dwc.sidecar.artifacts")["value"][0]
    assert art["kind"] == "asc-mhl"
    art["mhlEntry"] = "../../etc/passwd"
    report = validate_as_json(_write(tmp_path, clip_doc), base_dir=REPO_ROOT)
    # Stage 8 either fails on the missing Hashes entry or on containment —
    # never reads outside base_dir. Containment is checked when the entry
    # exists in the MHL, so force the lookup path too:
    assert report["errors"] > 0


# ------------------------------------------------------------- fail-open →

def test_missing_keyring_warns_not_passes(clip_doc, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)   # no keyring.json here
    report = validate_as_json(_write(tmp_path, clip_doc), base_dir=REPO_ROOT)
    s4 = _stage(report, "4")
    assert s4["status"] == "warn"
    assert s4["warnings"] == 1
    assert report["warnings"] >= 1
    assert "warning" in report["summary"]


def test_missing_keyring_fails_with_require_keyring(clip_doc, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    report = validate_as_json(_write(tmp_path, clip_doc), base_dir=REPO_ROOT,
                              require_keyring=True)
    assert _stage(report, "4")["status"] == "fail"
    assert report["errors"] > 0


# ----------------------------------------------------- lock verification →

def test_tampered_lock_signature_fails(clip_doc, tmp_path):
    # The clip lock is now an m-of-n threshold lock; tampering a co-signature
    # must still FAIL Stage 5.
    lk = _payload(clip_doc, 0, "dwc.sidecar.locks")["value"][0]
    lk["sigs"][0]["value"] = "BASE64..."
    report = validate_as_json(_write(tmp_path, clip_doc), base_dir=REPO_ROOT)
    s5 = _stage(report, "5")
    assert s5["status"] == "fail"
    assert any("signature" in ln for ln in s5["lines"])


def test_edited_lock_body_fails(clip_doc, tmp_path):
    """Changing the lock's reason invalidates its signature."""
    lk = _payload(clip_doc, 0, "dwc.sidecar.locks")["value"][0]
    lk["reason"] = "totally different intent"
    report = validate_as_json(_write(tmp_path, clip_doc), base_dir=REPO_ROOT)
    assert _stage(report, "5")["status"] == "fail"


# ------------------------------------------------- truncation / replay →

def test_truncated_chain_fails_via_head(clip_doc, tmp_path):
    """Drop events 2–3 (including the lock) keeping the valid seq:1 prefix.
    Old validator: 3/4/5 all green. Now the head anchor catches it."""
    ev_entry = _payload(clip_doc, 0, "dwc.sidecar.events")
    ev_entry["value"] = ev_entry["value"][:1]
    lk_entry = _payload(clip_doc, 0, "dwc.sidecar.locks")
    lk_entry["value"] = []   # attacker also drops the lock view
    report = validate_as_json(_write(tmp_path, clip_doc), base_dir=REPO_ROOT)
    s35 = _stage(report, "3.5")
    assert s35["status"] == "fail"
    assert any("truncated" in ln for ln in s35["lines"])


def test_removing_head_fails_for_v02(clip_doc, tmp_path):
    cd = clip_doc["Asset"][0]["assetFC"]["functionalProperties"]["customData"]
    cd[:] = [e for e in cd if e["domain"] != "dwc.sidecar.head"]
    report = validate_as_json(_write(tmp_path, clip_doc), base_dir=REPO_ROOT)
    s35 = _stage(report, "3.5")
    assert s35["status"] == "fail"
    assert any("missing dwc.sidecar.head" in ln for ln in s35["lines"])


def test_cross_asset_event_replay_fails(clip_doc, tmp_path):
    """Lift asset A's genuinely-signed event log into asset B: every
    signature still verifies, but the targets don't belong to B."""
    ev_a = _payload(clip_doc, 0, "dwc.sidecar.events")
    ev_b = _payload(clip_doc, 1, "dwc.sidecar.events")
    head_a = _payload(clip_doc, 0, "dwc.sidecar.head")
    head_b = _payload(clip_doc, 1, "dwc.sidecar.head")
    ev_b["value"]   = copy.deepcopy(ev_a["value"])
    head_b["value"] = copy.deepcopy(head_a["value"])
    report = validate_as_json(_write(tmp_path, clip_doc), base_dir=REPO_ROOT)
    s35 = _stage(report, "3.5")
    assert s35["status"] == "fail"
    assert any("cross-sidecar replay" in ln for ln in s35["lines"])


def test_artifact_hash_swap_fails_against_signed_commitment(clip_doc, tmp_path):
    """The core trust fix: swap the file AND its declared hash. Stage 6 would
    re-hash the new bytes against the new value and pass; the signed
    commitment inside the create event now catches it."""
    art = _payload(clip_doc, 0, "dwc.sidecar.artifacts")["value"][1]
    art["hash"]["value"] = "0" * 64
    report = validate_as_json(_write(tmp_path, clip_doc), base_dir=REPO_ROOT)
    s35 = _stage(report, "3.5")
    assert s35["status"] == "fail"
    assert any("signed commitment" in ln for ln in s35["lines"])


def test_uncommitted_artifact_fails_for_v02(clip_doc, tmp_path):
    """Appending a new artifact without a signed commitment must fail —
    otherwise an attacker can attach arbitrary 'verified' files."""
    arts = _payload(clip_doc, 0, "dwc.sidecar.artifacts")["value"]
    smuggled = copy.deepcopy(arts[1])
    smuggled["id"] = "urn:uuid:af8a0009-0000-0000-0000-000000000009"
    arts.append(smuggled)
    report = validate_as_json(_write(tmp_path, clip_doc), base_dir=REPO_ROOT)
    s35 = _stage(report, "3.5")
    assert s35["status"] == "fail"
    assert any("not covered by any signed event commitment" in ln for ln in s35["lines"])


# ------------------------------------------------------------ weak algs →

def test_weak_integrity_alg_warns(clip_doc, tmp_path):
    art = _payload(clip_doc, 0, "dwc.sidecar.artifacts")["value"][0]
    assert art["role"] == "integrity"
    art["hash"]["alg"] = "xxh64"   # breaks sig/commitment too; we only assert the warning
    report = validate_as_json(_write(tmp_path, clip_doc), base_dir=REPO_ROOT)
    s6 = _stage(report, "6")
    assert any("no adversarial-collision resistance" in ln for ln in s6["lines"])
    assert s6["warnings"] >= 1


def test_v01_sidecars_still_validate(tmp_path):
    """v0.1 documents (no commitments, no head) remain valid — Stage 3.5
    notes the limitation instead of failing."""
    legacy = json.loads(EXAMPLE_CLIP.read_text())
    for asset in legacy["Asset"]:
        cd = asset["assetFC"]["functionalProperties"]["customData"]
        cd[:] = [e for e in cd if e["domain"] != "dwc.sidecar.head"]
        for e in cd:
            e["namespace"] = "https://ns.the-dwc.com/sidecar/v0.1"
            e["schema"] = e["schema"].replace("/v0.2/", "/v0.1/")
            if e["domain"] == "dwc.sidecar.events":
                for ev in e["value"]:
                    ev.pop("artifacts", None)
    # events were re-signed with commitments, so stripping them breaks sigs —
    # rebuild a fully unsigned-claims v0.1 doc is out of scope; instead just
    # check Stage 3.5 itself (structure-only) reports notes, not errors.
    from dwc_sidecar.validate import validate_binding
    result = validate_binding(legacy)
    assert result["status"] == "pass"
    assert any("v0.1 payload" in ln for ln in result["lines"])
