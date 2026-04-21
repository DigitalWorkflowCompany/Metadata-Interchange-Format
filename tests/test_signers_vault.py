"""VaultTransitSigner — urllib.request.urlopen mocked.

Simulates Vault's HTTP API: reads /v1/<mount>/keys/<name> returning a PEM
pubkey, writes to /v1/<mount>/sign/<name> returning a real Ed25519
signature with the "vault:v1:" prefix Vault uses."""
import base64
import io
import json
from unittest.mock import MagicMock, patch

import pytest



class _FakeResponse:
    """urlopen context-manager-style response."""
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def read(self): return self._body


@pytest.fixture
def mock_vault_http(monkeypatch, ed25519_keypair):
    priv, _, pub_pem = ed25519_keypair
    calls: list[tuple] = []

    def fake_urlopen(req, timeout=None):
        calls.append((req.get_method(), req.full_url, req.data))
        url = req.full_url
        if "/keys/" in url and req.get_method() == "GET":
            body = json.dumps({"data": {"keys": {
                "1": {"public_key": pub_pem.decode()},
            }}}).encode()
            return _FakeResponse(body)
        if "/sign/" in url and req.get_method() == "POST":
            payload = json.loads(req.data)
            message = base64.b64decode(payload["input"])
            sig = priv.sign(message)
            body = json.dumps({"data": {
                "signature": f"vault:v1:{base64.b64encode(sig).decode()}",
            }}).encode()
            return _FakeResponse(body)
        raise AssertionError(f"unexpected Vault request: {req.get_method()} {url}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setenv("VAULT_TOKEN", "test-token")
    return calls


def test_vault_signer_roundtrip(mock_vault_http, ed25519_keypair, verify_signature):
    _, pub_raw, _ = ed25519_keypair
    from dwc_sidecar.signers.vault import VaultTransitSigner

    signer = VaultTransitSigner(
        kid="dwc-post-01",
        url="https://vault.example.com:8200",
        key_name="dwc-post-01",
    )

    assert signer.kid == "dwc-post-01"
    assert signer.public_key_bytes() == pub_raw

    sig = signer.sign(b"event bytes")
    verify_signature(pub_raw, b"event bytes", sig)

    methods_urls = [(m, u) for m, u, _ in mock_vault_http]
    assert ("GET",  "https://vault.example.com:8200/v1/transit/keys/dwc-post-01") in methods_urls
    assert ("POST", "https://vault.example.com:8200/v1/transit/sign/dwc-post-01") in methods_urls


def test_vault_signer_picks_latest_key_version(monkeypatch, ed25519_keypair):
    """Multiple key versions → public_key_bytes returns the highest-numbered one."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv_v1 = Ed25519PrivateKey.generate()
    priv_v3 = Ed25519PrivateKey.generate()
    pub_v3_raw = priv_v3.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    def pem(p):
        return p.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

    def fake_urlopen(req, timeout=None):
        body = json.dumps({"data": {"keys": {
            "1": {"public_key": pem(priv_v1)},
            "3": {"public_key": pem(priv_v3)},
        }}}).encode()
        return _FakeResponse(body)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setenv("VAULT_TOKEN", "t")

    from dwc_sidecar.signers.vault import VaultTransitSigner
    signer = VaultTransitSigner("dwc-x", "https://v:8200", "k")
    assert signer.public_key_bytes() == pub_v3_raw


def test_vault_signer_missing_token_raises(monkeypatch):
    monkeypatch.delenv("VAULT_TOKEN", raising=False)
    from dwc_sidecar.signers.vault import VaultTransitSigner
    with pytest.raises(RuntimeError, match="Vault token missing"):
        VaultTransitSigner("x", "https://v:8200", "k")


def test_vault_signer_custom_token_env(monkeypatch, mock_vault_http):
    """Test the token_env override path."""
    monkeypatch.delenv("VAULT_TOKEN", raising=False)
    monkeypatch.setenv("MY_CUSTOM_VAULT_TOKEN", "secret")

    from dwc_sidecar.signers.vault import VaultTransitSigner
    signer = VaultTransitSigner("x", "https://v:8200", "k", token_env="MY_CUSTOM_VAULT_TOKEN")
    # If construction didn't raise, the env var worked.
    assert signer.kid == "x"
