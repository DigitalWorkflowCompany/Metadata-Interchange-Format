"""Signer backends for DWC sidecar events.

Usage:

    from dwc_sidecar.signers import get_signer
    signer = get_signer("dwc-dit-01")
    sig = signer.sign(canonical_bytes)

Backend selection flows from the `DWC_SIGNERS` env var, which names a
JSON config mapping kid → backend. If unset, every kid resolves to a
`JsonFileSigner` reading `./keys.priv.json` — the dev-time default.

signers.json shape:

    {
      "dwc-dit-01":   { "type": "pkcs11",
                        "module": "/usr/local/lib/libykcs11.dylib",
                        "slot":   0,
                        "label":  "dwc-dit-01" },
      "dwc-color-01": { "type": "gcp-kms",
                        "key_version": "projects/p/locations/eu-west2/keyRings/r/cryptoKeys/k/cryptoKeyVersions/1" },
      "dwc-post-01":  { "type": "vault-transit",
                        "url": "https://vault.example.com:8200",
                        "key_name": "dwc-post-01" },
      "dwc-audit-01": { "type": "azure-mhsm",
                        "key_id": "https://myhsm.managedhsm.azure.net/keys/audit-01/abc" },
      "dwc-dev-01":   { "type": "keychain", "service": "dwc-sidecar" },
      "dwc-demo-01":  { "type": "local" },
      "dwc-staging":  { "type": "file", "path": "/run/secrets/staging-key" }
    }

AWS KMS is intentionally absent: it does not support Ed25519 as of 2026.
For an AWS HSM deployment, use AWS CloudHSM via the pkcs11 backend with
the AWS CloudHSM Client's PKCS#11 library as `module`.
"""
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .base import Signer
from .jsonfile import JsonFileSigner

__all__ = ["Signer", "JsonFileSigner", "get_signer"]


DEFAULT_LOCAL_KEYS = Path("keys.priv.json")


def _config() -> Mapping[str, Mapping[str, Any]] | None:
    env = os.environ.get("DWC_SIGNERS")
    if not env:
        return None
    cfg_path = Path(env)
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"DWC_SIGNERS points at {cfg_path} which does not exist"
        )
    data = json.loads(cfg_path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{cfg_path}: expected a JSON object mapping kid → config")
    return data


def _build(kid: str, cfg: Mapping[str, Any]) -> Signer:
    t = cfg.get("type")
    params = {k: v for k, v in cfg.items() if k != "type"}
    if t == "local":
        return JsonFileSigner(kid, Path(cfg.get("path") or DEFAULT_LOCAL_KEYS))
    if t == "file":
        if "path" not in cfg:
            raise ValueError(f"signer {kid!r} type='file' requires 'path'")
        return JsonFileSigner(kid, Path(cfg["path"]))
    if t == "pkcs11":
        from .pkcs11 import PKCS11Signer
        return PKCS11Signer(kid, **params)
    if t == "gcp-kms":
        from .gcp_kms import GCPKMSSigner
        return GCPKMSSigner(kid, **params)
    if t == "vault-transit":
        from .vault import VaultTransitSigner
        return VaultTransitSigner(kid, **params)
    if t == "azure-mhsm":
        from .azure_mhsm import AzureManagedHSMSigner
        return AzureManagedHSMSigner(kid, **params)
    if t == "keychain":
        from .keychain import KeychainSigner
        return KeychainSigner(kid, **params)
    raise ValueError(
        f"signer {kid!r}: unknown type {t!r} "
        f"(expected: local, file, pkcs11, gcp-kms, vault-transit, azure-mhsm, keychain)"
    )


def get_signer(kid: str) -> Signer:
    """Return a Signer for `kid`, selecting the backend via DWC_SIGNERS config
    (or falling back to the local keys.priv.json default)."""
    cfg = _config()
    if cfg is None:
        return JsonFileSigner(kid, DEFAULT_LOCAL_KEYS)
    if kid not in cfg:
        raise KeyError(
            f"kid {kid!r} not in DWC_SIGNERS config "
            f"(configured kids: {sorted(cfg)})"
        )
    return _build(kid, cfg[kid])
