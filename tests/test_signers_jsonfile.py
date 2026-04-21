"""JsonFileSigner + get_signer factory — no mocking required."""
import base64
import json
from pathlib import Path

import pytest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from dwc_sidecar.signers import JsonFileSigner, Signer, get_signer



def _write_keys_file(path: Path, keys: dict[str, bytes]) -> None:
    path.write_text(json.dumps({
        kid: base64.b64encode(priv.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )).decode()
        for kid, priv in keys.items()
    }))


def test_jsonfile_signer_roundtrip(tmp_path, ed25519_keypair, verify_signature):
    priv, pub_raw, _ = ed25519_keypair
    kf = tmp_path / "keys.priv.json"
    _write_keys_file(kf, {"dwc-test-01": priv})

    signer = JsonFileSigner("dwc-test-01", kf)

    assert signer.kid == "dwc-test-01"
    assert signer.public_key_bytes() == pub_raw
    assert isinstance(signer, Signer)

    sig = signer.sign(b"canonical bytes")
    assert len(sig) == 64  # Ed25519 signatures are always 64 bytes
    verify_signature(pub_raw, b"canonical bytes", sig)


def test_jsonfile_signer_unknown_kid_raises(tmp_path):
    kf = tmp_path / "keys.priv.json"
    priv = Ed25519PrivateKey.generate()
    _write_keys_file(kf, {"dwc-dit-01": priv})

    with pytest.raises(KeyError, match="dwc-missing"):
        JsonFileSigner("dwc-missing", kf)


def test_jsonfile_signer_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        JsonFileSigner("dwc-dit-01", tmp_path / "nonexistent.json")


def test_get_signer_default_reads_cwd_keys(tmp_path, monkeypatch, ed25519_keypair, verify_signature):
    """Unset DWC_SIGNERS → default LocalSigner reading ./keys.priv.json."""
    priv, pub_raw, _ = ed25519_keypair
    _write_keys_file(tmp_path / "keys.priv.json", {"dwc-dit-01": priv})

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DWC_SIGNERS", raising=False)

    signer = get_signer("dwc-dit-01")
    assert signer.public_key_bytes() == pub_raw
    verify_signature(pub_raw, b"hi", signer.sign(b"hi"))


def test_get_signer_dispatches_via_dwc_signers(tmp_path, monkeypatch, ed25519_keypair, verify_signature):
    priv, pub_raw, _ = ed25519_keypair
    keys_path = tmp_path / "signing-keys.json"
    _write_keys_file(keys_path, {"dwc-post-01": priv})

    signers_cfg = tmp_path / "signers.json"
    signers_cfg.write_text(json.dumps({
        "dwc-post-01": {"type": "file", "path": str(keys_path)},
    }))

    monkeypatch.setenv("DWC_SIGNERS", str(signers_cfg))
    signer = get_signer("dwc-post-01")
    assert signer.kid == "dwc-post-01"
    verify_signature(pub_raw, b"msg", signer.sign(b"msg"))


def test_get_signer_unknown_type_raises(tmp_path, monkeypatch):
    cfg = tmp_path / "signers.json"
    cfg.write_text(json.dumps({"dwc-x": {"type": "aws-kms"}}))
    monkeypatch.setenv("DWC_SIGNERS", str(cfg))

    with pytest.raises(ValueError, match="unknown type 'aws-kms'"):
        get_signer("dwc-x")


def test_get_signer_unknown_kid_raises(tmp_path, monkeypatch):
    cfg = tmp_path / "signers.json"
    cfg.write_text(json.dumps({"dwc-x": {"type": "local"}}))
    monkeypatch.setenv("DWC_SIGNERS", str(cfg))

    with pytest.raises(KeyError, match="not in DWC_SIGNERS config"):
        get_signer("dwc-other")


def test_get_signer_missing_config_file_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("DWC_SIGNERS", str(tmp_path / "missing.json"))
    with pytest.raises(FileNotFoundError):
        get_signer("dwc-x")


def test_ed25519_pem_to_raw_helper(ed25519_keypair):
    """Shared helper used by GCP-KMS and Vault backends."""
    from dwc_sidecar.signers._util import ed25519_pem_to_raw

    _, pub_raw, pub_pem = ed25519_keypair
    assert ed25519_pem_to_raw(pub_pem) == pub_raw
    assert ed25519_pem_to_raw(pub_pem.decode()) == pub_raw


def test_ed25519_pem_to_raw_rejects_non_ed25519():
    from cryptography.hazmat.primitives.asymmetric import rsa
    from dwc_sidecar.signers._util import ed25519_pem_to_raw

    rsa_pub = rsa.generate_private_key(65537, 2048).public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    with pytest.raises(ValueError, match="not an Ed25519"):
        ed25519_pem_to_raw(rsa_pub)
