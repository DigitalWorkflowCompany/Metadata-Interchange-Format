"""Unit tests for the two ingestion paths (mhl_walker, batch) — previously
untested end-to-end. Each builds a tiny production tree, emits a sidecar with
a real Ed25519 signer, and pushes the result through the full validator.
"""
import base64
import json
from pathlib import Path

import pytest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from dwc_sidecar import batch, mhl_walker
from dwc_sidecar.canonical import file_digest
from dwc_sidecar.signers.jsonfile import JsonFileSigner
from dwc_sidecar.validate import validate_as_json

KID = "dwc-test-01"


@pytest.fixture
def production(tmp_path):
    """Minimal tree: one roll, one clip, one MHL v2 declaring its sha256."""
    root = tmp_path / "prod"
    roll = root / "1_OCF" / "A001"
    roll.mkdir(parents=True)
    clip = roll / "A001_C001.mov"
    clip.write_bytes(b"not really prores, but bytes are bytes" * 64)
    sha = file_digest(clip, "sha256")
    (roll / "A001.mhl").write_text(
        f"Version: 2.0.0\nHashes:\n  - File: A001_C001.mov\n    sha256: {sha}\n"
    )

    priv = Ed25519PrivateKey.generate()
    keys_file = tmp_path / "keys.priv.json"
    keys_file.write_text(json.dumps({KID: base64.b64encode(priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )).decode()}))
    keyring = tmp_path / "keyring.json"
    keyring.write_text(json.dumps({"alg": "ed25519", "keys": {
        KID: base64.b64encode(priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )).decode()
    }}))
    return {"root": root, "roll": roll, "clip": clip, "clip_sha": sha,
            "signer": JsonFileSigner(KID, keys_file), "keyring": keyring}


def _validate(out: Path, prod) -> dict:
    return validate_as_json(out, base_dir=prod["root"],
                            keyring_path=prod["keyring"],
                            require_keyring=True)


def test_mhl_walker_sidecar_validates(production, tmp_path):
    doc = mhl_walker.build_sidecar_from_mhl_entry(
        production["roll"] / "A001.mhl", "A001_C001.mov", production["clip"],
        "sha256", production["clip_sha"],
        production["root"], None, None, None, production["signer"],
    )
    out = tmp_path / "A001_C001.omc.json"
    out.write_text(json.dumps(doc, indent=2))
    report = _validate(out, production)
    assert report["errors"] == 0, report
    # v0.2 contract: signed commitments + head anchor present
    cd = doc["Asset"][0]["assetFC"]["functionalProperties"]["customData"]
    domains = [e["domain"] for e in cd]
    assert "dwc.sidecar.head" in domains
    ev = next(e for e in cd if e["domain"] == "dwc.sidecar.events")["value"][0]
    assert ev["artifacts"], "create event must commit to artifact hashes"


def test_mhl_walker_trust_mhl_fast_path(production, tmp_path):
    doc = mhl_walker.build_sidecar_from_mhl_entry(
        production["roll"] / "A001.mhl", "A001_C001.mov", production["clip"],
        "sha256", production["clip_sha"],
        production["root"], None, None, None, production["signer"],
    )
    out = tmp_path / "A001_C001.omc.json"
    out.write_text(json.dumps(doc, indent=2))
    report = validate_as_json(out, base_dir=production["root"],
                              keyring_path=production["keyring"],
                              trust_mhl=True)
    assert report["errors"] == 0
    s6 = next(s for s in report["stages"] if s["stage"] == "6")
    assert any("delegated to Stage 8" in ln for ln in s6["lines"])


def test_batch_sidecar_validates_with_default_sha256(production, tmp_path):
    doc = batch.build_sidecar(
        production["clip"], production["roll"], production["root"],
        None, None, None, "sha256", production["signer"],
    )
    out = tmp_path / "A001_C001.omc.json"
    out.write_text(json.dumps(doc, indent=2))
    report = _validate(out, production)
    assert report["errors"] == 0, report
    assert report["warnings"] == 0, "sha256 clip-integrity must not warn"
    arts = next(e for e in doc["Asset"][0]["assetFC"]["functionalProperties"]["customData"]
                if e["domain"] == "dwc.sidecar.artifacts")["value"]
    ci = next(a for a in arts if a["role"] == "clip-integrity")
    assert ci["hash"]["alg"] == "sha256"


def test_weak_alg_sidecar_warns_but_passes(production, tmp_path):
    """xxh64 stays usable (it's what MHL v1 trees offer) — but Stage 6 warns."""
    doc = batch.build_sidecar(
        production["clip"], production["roll"], production["root"],
        None, None, None, "xxh64", production["signer"],
    )
    out = tmp_path / "A001_C001.omc.json"
    out.write_text(json.dumps(doc, indent=2))
    report = _validate(out, production)
    assert report["errors"] == 0
    assert report["warnings"] >= 1


def test_rel_with_prefix_sibling_does_not_crash(tmp_path):
    """str(p).startswith(str(base)) was true for /x/Media vs base /x/M —
    then relative_to raised ValueError and killed the MHL entry."""
    base  = tmp_path / "M"
    inner = tmp_path / "Media"
    base.mkdir(); inner.mkdir()
    f = inner / "clip.mov"
    f.write_bytes(b"x")
    assert mhl_walker._rel(f, base) == str(f.resolve())          # outside → absolute
    g = base / "clip.mov"
    g.write_bytes(b"x")
    assert mhl_walker._rel(g, base) == "./clip.mov"              # inside → relative


def test_default_clip_hash_is_sha256():
    """The clip-integrity default backs the substitution claim — it must be a
    collision-resistant alg, not xxh64."""
    import inspect
    from dwc_sidecar import bootstrap
    assert 'default="sha256"' in inspect.getsource(batch.main)
    assert 'default="sha256"' in inspect.getsource(bootstrap.main)
