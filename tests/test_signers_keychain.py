"""KeychainSigner — subprocess.run(security) mocked.

Simulates the macOS `security find-generic-password -w` output: base64 of a
real Ed25519 private key's raw bytes. Happy path verifies a round-trip;
error paths verify helpful messages."""
import base64
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from cryptography.hazmat.primitives import serialization



@pytest.fixture
def _force_darwin(monkeypatch):
    """KeychainSigner short-circuits on non-darwin. Force it on for the tests."""
    monkeypatch.setattr(sys, "platform", "darwin")


def test_keychain_signer_roundtrip(monkeypatch, ed25519_keypair, verify_signature, _force_darwin):
    priv, pub_raw, _ = ed25519_keypair
    priv_raw = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    priv_b64 = base64.b64encode(priv_raw).decode()

    calls: list[list[str]] = []
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=priv_b64 + "\n", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    monkeypatch.delitem(sys.modules, "dwc_sidecar.signers.keychain", raising=False)
    from dwc_sidecar.signers.keychain import KeychainSigner

    s = KeychainSigner("dwc-dit-01", service="dwc-test-svc")
    assert s.kid == "dwc-dit-01"
    assert s.public_key_bytes() == pub_raw
    verify_signature(pub_raw, b"msg", s.sign(b"msg"))

    assert calls[0] == [
        "security", "find-generic-password",
        "-s", "dwc-test-svc", "-a", "dwc-dit-01", "-w",
    ]


def test_keychain_signer_missing_item_raises(monkeypatch, _force_darwin):
    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(44, cmd, output="", stderr="not found")
    monkeypatch.setattr(subprocess, "run", fake_run)

    monkeypatch.delitem(sys.modules, "dwc_sidecar.signers.keychain", raising=False)
    from dwc_sidecar.signers.keychain import KeychainSigner

    with pytest.raises(RuntimeError, match="no item for kid 'dwc-missing'"):
        KeychainSigner("dwc-missing")


def test_keychain_signer_security_cli_not_found_raises(monkeypatch, _force_darwin):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("no security")
    monkeypatch.setattr(subprocess, "run", fake_run)

    monkeypatch.delitem(sys.modules, "dwc_sidecar.signers.keychain", raising=False)
    from dwc_sidecar.signers.keychain import KeychainSigner

    with pytest.raises(RuntimeError, match="security.*CLI not found"):
        KeychainSigner("x")


def test_keychain_signer_non_darwin_raises(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delitem(sys.modules, "dwc_sidecar.signers.keychain", raising=False)
    from dwc_sidecar.signers.keychain import KeychainSigner

    with pytest.raises(RuntimeError, match="only works on macOS"):
        KeychainSigner("x")
