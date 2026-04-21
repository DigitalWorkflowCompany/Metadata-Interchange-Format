"""VaultTransitSigner — Ed25519 signing via HashiCorp Vault's Transit engine.

Vault Transit exposes Ed25519 as a first-class key type. Keys live inside
Vault; our process sends canonical bytes over HTTPS and receives the
signature back.

No optional dependency required — uses stdlib urllib. For TLS verification
issues (self-signed certs, corporate trust chains), install the system's
trust bundle or set REQUESTS_CA_BUNDLE-equivalent via SSL env vars.

signers.json entry:

  {
    "type":        "vault-transit",
    "url":         "https://vault.example.com:8200",
    "key_name":    "dwc-post-01",
    "mount_point": "transit",       // optional, defaults to "transit"
    "token_env":   "VAULT_TOKEN"    // optional, defaults to VAULT_TOKEN
  }

Create the key externally:

  vault write -f transit/keys/dwc-post-01 type=ed25519
"""
import base64
import json
import os
import urllib.error
import urllib.request
from typing import Any, Mapping

from .base import Signer
from ._util import ed25519_pem_to_raw


class VaultTransitSigner(Signer):

    def __init__(
        self,
        kid: str,
        url: str,
        key_name: str,
        mount_point: str = "transit",
        token_env: str = "VAULT_TOKEN",
        token: str | None = None,
    ):
        self._kid       = kid
        self._url       = url.rstrip("/")
        self._key_name  = key_name
        self._mount     = mount_point

        resolved_token = token if token is not None else os.environ.get(token_env)
        if not resolved_token:
            raise RuntimeError(
                f"Vault token missing: set env var {token_env!r} "
                f"or pass `token` in signers.json"
            )
        self._headers = {
            "X-Vault-Token": resolved_token,
            "Content-Type":  "application/json",
        }

        data = self._get(f"/v1/{self._mount}/keys/{self._key_name}")
        keys: Mapping[str, Any] = data["keys"]
        latest_version = max(int(v) for v in keys)
        pub_pem = keys[str(latest_version)]["public_key"]
        self._pub_bytes = ed25519_pem_to_raw(pub_pem)

    def sign(self, message: bytes) -> bytes:
        payload = {"input": base64.b64encode(message).decode("ascii")}
        data    = self._post(f"/v1/{self._mount}/sign/{self._key_name}", payload)
        sig_str = data["signature"]
        # Vault prefixes signatures with "vault:v<N>:<b64>"
        _, _, b64 = sig_str.split(":", 2)
        return base64.b64decode(b64)

    def public_key_bytes(self) -> bytes:
        return self._pub_bytes

    @property
    def kid(self) -> str:
        return self._kid

    def _get(self, path: str) -> Mapping[str, Any]:
        req = urllib.request.Request(f"{self._url}{path}", headers=self._headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())["data"]

    def _post(self, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        req = urllib.request.Request(
            f"{self._url}{path}",
            data=json.dumps(payload).encode(),
            headers=self._headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())["data"]
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            raise RuntimeError(f"Vault {e.code} on {path}: {body}") from e
