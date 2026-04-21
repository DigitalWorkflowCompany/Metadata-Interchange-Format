"""JsonFileSigner — Ed25519 private keys stored in a JSON file as base64.

Backs two named backends in signers.json:

  {"type": "local"}                  → JsonFileSigner(kid, "keys.priv.json")
  {"type": "file", "path": "..."}    → JsonFileSigner(kid, explicit path)

The "local" alias preserves the dev-era keys.priv.json workflow; "file"
is for secret-mount deployments (Docker secrets, Kubernetes secrets,
or any external mount not tracked by git).
"""
import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .base import Signer


class JsonFileSigner(Signer):

    def __init__(self, kid: str, path: Path):
        self._kid = kid
        bundle = json.loads(Path(path).read_text())
        if kid not in bundle:
            raise KeyError(
                f"kid {kid!r} not found in {path} "
                f"(available: {sorted(bundle)})"
            )
        self._priv = Ed25519PrivateKey.from_private_bytes(
            base64.b64decode(bundle[kid])
        )

    def sign(self, message: bytes) -> bytes:
        return self._priv.sign(message)

    def public_key_bytes(self) -> bytes:
        return self._priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    @property
    def kid(self) -> str:
        return self._kid
