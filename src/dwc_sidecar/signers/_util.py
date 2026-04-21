"""Shared helpers for signer backends."""
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def ed25519_pem_to_raw(pem: str | bytes) -> bytes:
    """Convert a PEM-encoded Ed25519 public key (as returned by GCP KMS or
    Vault Transit) to its 32 raw bytes (as needed for keyring.json)."""
    if isinstance(pem, str):
        pem = pem.encode()
    pub = serialization.load_pem_public_key(pem)
    if not isinstance(pub, Ed25519PublicKey):
        raise ValueError("PEM is not an Ed25519 public key")
    return pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
