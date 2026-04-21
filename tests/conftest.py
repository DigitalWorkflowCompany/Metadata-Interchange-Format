"""Shared fixtures for signer tests.

Every signer backend test uses the same pattern: replace the real backend's
signing call with a fake that drives a real Ed25519 private key. The test
then verifies the resulting signature cryptographically — so if the wiring
is wrong (bytes get mangled, the wrong message is signed, the signature
format is misparsed), the `.verify()` call fails loudly.
"""
import pytest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


@pytest.fixture
def ed25519_keypair():
    """Return (priv, pub_raw_32_bytes, pub_pem_bytes)."""
    priv    = Ed25519PrivateKey.generate()
    pub     = priv.public_key()
    pub_raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    pub_pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv, pub_raw, pub_pem


@pytest.fixture
def verify_signature():
    """Returns a callable — raises if wiring is wrong."""
    def _verify(pub_raw: bytes, message: bytes, signature: bytes) -> None:
        Ed25519PublicKey.from_public_bytes(pub_raw).verify(signature, message)
    return _verify
