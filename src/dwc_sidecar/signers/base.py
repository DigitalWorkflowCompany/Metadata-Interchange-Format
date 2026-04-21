"""Abstract Signer: something that can sign canonical event bytes with an
Ed25519 key, without the key material necessarily leaving its backend.

A Signer has three observable operations:
- `sign(message)` — returns the raw 64-byte Ed25519 signature
- `public_key_bytes()` — returns the raw 32-byte Ed25519 public key
- `kid` — the stable key identifier matching `keyring.json`
"""
from abc import ABC, abstractmethod


class Signer(ABC):

    @abstractmethod
    def sign(self, message: bytes) -> bytes:
        """Return raw 64-byte Ed25519 signature over `message`."""

    @abstractmethod
    def public_key_bytes(self) -> bytes:
        """Return the raw 32-byte Ed25519 public key."""

    @property
    @abstractmethod
    def kid(self) -> str:
        """Stable key identifier (matches an entry in `keyring.json`)."""
