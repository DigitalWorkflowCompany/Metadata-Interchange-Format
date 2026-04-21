"""GCPKMSSigner — google.cloud.kms mocked at the module level.

The mock KMS client holds a real Ed25519 private key and returns real
signatures, so the final `Ed25519PublicKey.verify()` passes only if the
Signer wires the bytes through correctly (no mangling in sign/pub-key
parsing)."""
import sys
import types
from unittest.mock import MagicMock

import pytest



@pytest.fixture
def mock_gcp_kms(monkeypatch, ed25519_keypair):
    """Install a fake google.cloud.kms in sys.modules.

    Yields the mock client so tests can assert call arguments."""
    priv, _, pub_pem = ed25519_keypair
    mock_client = MagicMock()
    mock_client.get_public_key.return_value = MagicMock(pem=pub_pem.decode())

    def fake_sign(request):
        # sign the real message with the real priv; returns an object with .signature
        return MagicMock(signature=priv.sign(request["data"]))
    mock_client.asymmetric_sign.side_effect = fake_sign

    fake_kms = types.ModuleType("google.cloud.kms")
    fake_kms.KeyManagementServiceClient = MagicMock(return_value=mock_client)  # type: ignore[attr-defined]

    fake_cloud = types.ModuleType("google.cloud")
    fake_cloud.kms = fake_kms  # type: ignore[attr-defined]
    fake_google = types.ModuleType("google")
    fake_google.cloud = fake_cloud  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.cloud", fake_cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.kms", fake_kms)

    # Force a fresh import so the lazy `from google.cloud import kms` picks up the fake.
    monkeypatch.delitem(sys.modules, "dwc_sidecar.signers.gcp_kms", raising=False)

    return mock_client


def test_gcp_kms_signer_roundtrip(mock_gcp_kms, ed25519_keypair, verify_signature):
    _, pub_raw, _ = ed25519_keypair
    from dwc_sidecar.signers.gcp_kms import GCPKMSSigner

    key_version = "projects/p/locations/l/keyRings/r/cryptoKeys/k/cryptoKeyVersions/1"
    signer = GCPKMSSigner("dwc-color-01", key_version)

    assert signer.kid == "dwc-color-01"
    assert signer.public_key_bytes() == pub_raw
    mock_gcp_kms.get_public_key.assert_called_once_with(request={"name": key_version})

    sig = signer.sign(b"canonical event bytes")
    verify_signature(pub_raw, b"canonical event bytes", sig)

    mock_gcp_kms.asymmetric_sign.assert_called_once_with(request={
        "name": key_version,
        "data": b"canonical event bytes",
    })


def test_gcp_kms_signer_missing_sdk_raises(monkeypatch):
    """If google-cloud-kms isn't installed, constructor should raise a helpful ImportError."""
    # Block the import chain — any of these being absent should trip the helpful message.
    monkeypatch.setitem(sys.modules, "google.cloud.kms", None)
    monkeypatch.delitem(sys.modules, "dwc_sidecar.signers.gcp_kms", raising=False)

    from dwc_sidecar.signers.gcp_kms import GCPKMSSigner
    with pytest.raises(ImportError, match="dwc-sidecar\\[gcp\\]"):
        GCPKMSSigner("x", "projects/x/locations/x/keyRings/x/cryptoKeys/x/cryptoKeyVersions/1")
