"""AzureManagedHSMSigner — azure.identity + azure.keyvault.keys mocked.

Both the CryptographyClient.sign call and the KeyClient.get_key call are
mocked to return real Ed25519 signatures/pubkeys, so the final verify
catches any wiring errors."""
import base64
import sys
import types
from unittest.mock import MagicMock

import pytest



@pytest.fixture
def mock_azure(monkeypatch, ed25519_keypair):
    priv, pub_raw, _ = ed25519_keypair

    # --- crypto client ---
    sign_calls: list[tuple] = []
    mock_crypto = MagicMock()
    def fake_sign(alg, message):
        sign_calls.append((alg, message))
        return MagicMock(signature=priv.sign(message))
    mock_crypto.sign.side_effect = fake_sign

    # --- key client ---
    mock_jwk = MagicMock()
    mock_jwk.x = pub_raw  # already-raw bytes path
    mock_key = MagicMock()
    mock_key.key = mock_jwk
    mock_key_client = MagicMock()
    mock_key_client.get_key.return_value = mock_key

    # --- fake SDK modules ---
    fake_identity = types.ModuleType("azure.identity")
    fake_identity.DefaultAzureCredential = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]

    fake_keys = types.ModuleType("azure.keyvault.keys")
    fake_keys.KeyClient = MagicMock(return_value=mock_key_client)  # type: ignore[attr-defined]

    fake_crypto_mod = types.ModuleType("azure.keyvault.keys.crypto")
    fake_crypto_mod.CryptographyClient = MagicMock(return_value=mock_crypto)  # type: ignore[attr-defined]

    class _SigAlg:
        eddsa = "eddsa-sentinel"
    fake_crypto_mod.SignatureAlgorithm = _SigAlg  # type: ignore[attr-defined]

    fake_keyvault = types.ModuleType("azure.keyvault")
    fake_keyvault.keys = fake_keys  # type: ignore[attr-defined]

    fake_azure = types.ModuleType("azure")
    fake_azure.identity = fake_identity  # type: ignore[attr-defined]
    fake_azure.keyvault = fake_keyvault  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "azure", fake_azure)
    monkeypatch.setitem(sys.modules, "azure.identity", fake_identity)
    monkeypatch.setitem(sys.modules, "azure.keyvault", fake_keyvault)
    monkeypatch.setitem(sys.modules, "azure.keyvault.keys", fake_keys)
    monkeypatch.setitem(sys.modules, "azure.keyvault.keys.crypto", fake_crypto_mod)
    monkeypatch.delitem(sys.modules, "dwc_sidecar.signers.azure_mhsm", raising=False)

    return {
        "sign_calls":      sign_calls,
        "key_client":      mock_key_client,
        "crypto_mod":      fake_crypto_mod,
    }


def test_azure_mhsm_signer_roundtrip(mock_azure, ed25519_keypair, verify_signature):
    _, pub_raw, _ = ed25519_keypair
    from dwc_sidecar.signers.azure_mhsm import AzureManagedHSMSigner

    key_id = "https://myhsm.managedhsm.azure.net/keys/color-01/abc123"
    signer = AzureManagedHSMSigner("dwc-color-01", key_id)

    assert signer.kid == "dwc-color-01"
    assert signer.public_key_bytes() == pub_raw
    mock_azure["key_client"].get_key.assert_called_once_with("color-01", version="abc123")

    sig = signer.sign(b"event body")
    verify_signature(pub_raw, b"event body", sig)

    alg, msg = mock_azure["sign_calls"][0]
    assert alg == mock_azure["crypto_mod"].SignatureAlgorithm.eddsa
    assert msg == b"event body"


def test_azure_mhsm_signer_jwk_x_as_base64url_string(monkeypatch, ed25519_keypair):
    """JWK .x sometimes comes back as a base64url-encoded string; constructor decodes it."""
    priv, pub_raw, _ = ed25519_keypair
    pub_raw_b64url = base64.urlsafe_b64encode(pub_raw).rstrip(b"=").decode()

    mock_crypto = MagicMock()
    mock_crypto.sign.return_value = MagicMock(signature=priv.sign(b"x"))

    mock_jwk = MagicMock()
    mock_jwk.x = pub_raw_b64url  # string path
    mock_key_client = MagicMock()
    mock_key_client.get_key.return_value = MagicMock(key=mock_jwk)

    fake_identity = types.ModuleType("azure.identity")
    fake_identity.DefaultAzureCredential = MagicMock()  # type: ignore[attr-defined]
    fake_keys = types.ModuleType("azure.keyvault.keys")
    fake_keys.KeyClient = MagicMock(return_value=mock_key_client)  # type: ignore[attr-defined]
    fake_crypto_mod = types.ModuleType("azure.keyvault.keys.crypto")
    fake_crypto_mod.CryptographyClient = MagicMock(return_value=mock_crypto)  # type: ignore[attr-defined]
    class _SA: eddsa = "eddsa"
    fake_crypto_mod.SignatureAlgorithm = _SA  # type: ignore[attr-defined]

    for name, mod in [
        ("azure", types.ModuleType("azure")),
        ("azure.identity",             fake_identity),
        ("azure.keyvault",             types.ModuleType("azure.keyvault")),
        ("azure.keyvault.keys",        fake_keys),
        ("azure.keyvault.keys.crypto", fake_crypto_mod),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.delitem(sys.modules, "dwc_sidecar.signers.azure_mhsm", raising=False)

    from dwc_sidecar.signers.azure_mhsm import AzureManagedHSMSigner
    s = AzureManagedHSMSigner(
        "x", "https://h.managedhsm.azure.net/keys/k/v",
    )
    assert s.public_key_bytes() == pub_raw


def test_azure_mhsm_signer_missing_sdk_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "azure.identity", None)
    monkeypatch.delitem(sys.modules, "dwc_sidecar.signers.azure_mhsm", raising=False)

    from dwc_sidecar.signers.azure_mhsm import AzureManagedHSMSigner
    with pytest.raises(ImportError, match="dwc-sidecar\\[azure\\]"):
        AzureManagedHSMSigner("x", "https://h.managedhsm.azure.net/keys/k/v")
