"""KeychainSigner — Ed25519 private key stored in the macOS Keychain.

Lightweight variant: the raw 32-byte Ed25519 private key is stored as a
generic-password item in the login Keychain and retrieved on-demand via
the `security` CLI. Signing happens in-process.

Compared to LocalSigner / FileSigner, this:
+ keeps the key off the filesystem (no committed keys.priv.json)
+ gates access via macOS login / biometrics when the keychain is locked
+ scopes per-user on shared hosts
- holds key bytes in process memory briefly (not true Secure Enclave)

For true hardware-bound signing on Mac (key never leaves the Secure
Enclave) you need a Swift helper binary — out of scope for this backend.

No optional dependency — stdlib only.

signers.json entry:

  {
    "type":    "keychain",
    "service": "dwc-sidecar"   // optional, defaults to "dwc-sidecar"
  }

Manage the keychain item externally or via `dwc keygen --backend keychain`:

  # add a new key manually
  security add-generic-password -s dwc-sidecar -a dwc-dit-01 -w <b64priv>

  # remove
  security delete-generic-password -s dwc-sidecar -a dwc-dit-01
"""
import base64
import subprocess
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .base import Signer


class KeychainSigner(Signer):

    def __init__(self, kid: str, service: str = "dwc-sidecar"):
        if sys.platform != "darwin":
            raise RuntimeError("KeychainSigner only works on macOS (requires the 'security' CLI)")
        self._kid     = kid
        self._service = service

        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-s", service, "-a", kid, "-w"],
                capture_output=True, text=True, check=True, timeout=30,
            )
        except FileNotFoundError as e:
            raise RuntimeError("macOS 'security' CLI not found on PATH") from e
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"keychain: no item for kid {kid!r} in service {service!r}. "
                f"Add with  dwc keygen --kid {kid} --backend keychain  "
                f"or  security add-generic-password -s {service} -a {kid} -w <b64priv>"
            ) from e

        priv_bytes = base64.b64decode(result.stdout.strip())
        self._priv = Ed25519PrivateKey.from_private_bytes(priv_bytes)

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
