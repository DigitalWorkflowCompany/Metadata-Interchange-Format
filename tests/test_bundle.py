"""Sealed-bundle create/verify (Phase 4).

The validator already does the heavy lifting; these tests pin the bundle's
trust UX and IO safety: external trust must be explicit, a tampered byte or
edited sidecar must FAIL, the bundled keyring is never silently trusted, zip
slip is refused, --lite needs --allow-missing-clips, and CWD files are never
picked up.
"""
import json
import zipfile
from pathlib import Path

import pytest

from dwc_sidecar.bundle import BundleTrustError, create_bundle, verify_bundle

REPO_ROOT = Path(__file__).resolve().parent.parent
CLIP      = REPO_ROOT / "example-clip.omc.json"


@pytest.fixture
def repo_cwd(monkeypatch):
    monkeypatch.chdir(REPO_ROOT)


def _make(tmp_path, *, lite=False) -> tuple[Path, dict]:
    out = tmp_path / "clip.dwcbundle.zip"
    manifest = create_bundle(CLIP, REPO_ROOT, out, lite=lite)
    return out, manifest


def _rewrite_entry(src: Path, dst: Path, name: str, new_bytes: bytes) -> None:
    """Copy `src` zip to `dst`, replacing one entry's bytes."""
    with zipfile.ZipFile(src) as zin:
        items = [(i.filename, zin.read(i.filename)) for i in zin.infolist()]
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for fname, data in items:
            zout.writestr(fname, new_bytes if fname == name else data)


# --- happy paths --------------------------------------------------------------

def test_roundtrip_trust_bundled_ok(tmp_path, repo_cwd):
    b, _ = _make(tmp_path)
    rep = verify_bundle(b, trust_bundled=True)
    assert rep["errors"] == 0
    assert rep["exit_code"] == 0
    assert all(s["status"] != "fail" for s in rep["validation"]["stages"])


def test_fingerprint_pin_ok(tmp_path, repo_cwd):
    b, manifest = _make(tmp_path)
    rep = verify_bundle(b, keyring_fingerprint="sha256:" + manifest["keyringSha256"])
    assert rep["exit_code"] == 0
    assert rep["trust_mode"] == "fingerprint-pinned"


# --- trust must be explicit ---------------------------------------------------

def test_bundled_untrusted_is_nonzero_with_banner(tmp_path, repo_cwd):
    b, _ = _make(tmp_path)
    rep = verify_bundle(b)  # no trust flags
    assert rep["errors"] == 0          # signatures verified...
    assert rep["exit_code"] != 0       # ...but trust is unconfirmed
    assert "TRUST IS SELF-CONTAINED" in (rep["banner"] or "")


def test_fingerprint_mismatch_raises_before_stages(tmp_path, repo_cwd):
    b, _ = _make(tmp_path)
    with pytest.raises(BundleTrustError, match="fingerprint mismatch"):
        verify_bundle(b, keyring_fingerprint="sha256:" + "0" * 64)


def test_missing_keyring_in_bundle_fails(tmp_path, repo_cwd):
    b, _ = _make(tmp_path)
    stripped = tmp_path / "nokeyring.zip"
    with zipfile.ZipFile(b) as zin:
        with zipfile.ZipFile(stripped, "w") as zout:
            for i in zin.infolist():
                if i.filename != "keyring.json":
                    zout.writestr(i.filename, zin.read(i.filename))
    with pytest.raises(BundleTrustError, match="no keyring"):
        verify_bundle(stripped, trust_bundled=True)


# --- integrity ----------------------------------------------------------------

def test_tampered_artifact_byte_fails(tmp_path, repo_cwd):
    b, _ = _make(tmp_path)
    tampered = tmp_path / "tampered.zip"
    _rewrite_entry(b, tampered, "amf/A001_C042_0420AB.amf", b"not the original amf bytes\n")
    rep = verify_bundle(tampered, trust_bundled=True)
    assert rep["errors"] > 0
    assert any(s["stage"] == "6" and s["status"] == "fail" for s in rep["validation"]["stages"])


def test_edited_sidecar_fails_signature(tmp_path, repo_cwd):
    b, manifest = _make(tmp_path)
    doc = json.loads(CLIP.read_text())
    # Mutate an event body under the signature (Stage 4 must catch it).
    for a in doc["Asset"]:
        for cd in a["assetFC"]["functionalProperties"]["customData"]:
            if cd["domain"] == "dwc.sidecar.events":
                cd["value"][0]["actor"]["role"] = "Impostor"
                break
        break
    edited = tmp_path / "edited.zip"
    _rewrite_entry(b, edited, manifest["sidecar"], json.dumps(doc, indent=2).encode())
    rep = verify_bundle(edited, trust_bundled=True)
    assert rep["errors"] > 0


# --- IO safety ----------------------------------------------------------------

def test_zip_slip_entry_refused(tmp_path, repo_cwd):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("example-clip.omc.json", "{}")
        z.writestr("../../evil.txt", "pwned")
    with pytest.raises(ValueError, match="escapes extraction root"):
        verify_bundle(evil, trust_bundled=True)


def test_escaping_artifact_path_refused_at_bundle(tmp_path, repo_cwd):
    doc = json.loads(CLIP.read_text())
    doc["Asset"][0]["assetFC"]["functionalProperties"]["customData"][0]["value"][0]["path"] = \
        "../../../etc/passwd"
    bad = tmp_path / "bad.omc.json"
    bad.write_text(json.dumps(doc))
    with pytest.raises(SystemExit, match="escapes base-dir"):
        create_bundle(bad, REPO_ROOT, tmp_path / "x.zip")


# --- lite ---------------------------------------------------------------------

def test_lite_needs_allow_missing(tmp_path, repo_cwd):
    b, manifest = _make(tmp_path, lite=True)
    assert manifest["omitted"]  # the integrity-role MHL was omitted

    fails = verify_bundle(b, trust_bundled=True)
    assert fails["errors"] > 0  # missing file without opt-in → FAIL

    ok = verify_bundle(b, trust_bundled=True, allow_missing_clips=True)
    assert ok["errors"] == 0


# --- CWD contamination --------------------------------------------------------

def test_cwd_keyring_is_ignored(tmp_path, repo_cwd, monkeypatch):
    b, _ = _make(tmp_path)  # built while CWD = repo root (real keyring)
    # Now move to a dir holding a DIFFERENT keyring.json; verify must ignore it.
    contaminated = tmp_path / "elsewhere"
    contaminated.mkdir()
    (contaminated / "keyring.json").write_text(json.dumps(
        {"alg": "ed25519", "keys": {"dwc-dit-01": {"publicKey": "AAAA", "validFrom": None,
                                                    "validUntil": None}}}))
    monkeypatch.chdir(contaminated)
    rep = verify_bundle(b, trust_bundled=True)
    assert rep["errors"] == 0  # used the bundle's keyring, not the CWD one
