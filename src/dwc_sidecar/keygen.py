"""`dwc keygen` — generate a new Ed25519 signing key and print a keyring.json
entry ready to paste.

Backends:

  local / file  — generate in-process, write the private key to a JSON file
                  matching the JsonFileSigner format
  pkcs11        — generate the keypair directly inside a PKCS#11 token;
                  the private key never leaves the hardware. Requires
                  pip install dwc-sidecar[hsm] and a vendor PKCS#11 module.

The public key, validity window, and kid are printed in the exact shape
`keyring.json` expects under `.keys.<kid>`. Copy and paste into keyring.json
(new deployments) or into the signed-event replay flow (rotations).
"""
import argparse
import base64
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _iso_days(offset_days: int = 0) -> str:
    dt = datetime.now(timezone.utc) + timedelta(days=offset_days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _emit_keyring_entry(
    kid: str,
    pub_raw: bytes,
    valid_from: str,
    valid_until: str,
) -> None:
    entry = {
        kid: {
            "publicKey":        _b64(pub_raw),
            "validFrom":        valid_from,
            "validUntil":       valid_until,
            "revokedAt":        None,
            "revocationReason": None,
        }
    }
    print("\n--- paste into keyring.json under .keys ---")
    print(json.dumps(entry, indent=2))
    print("--- end ---\n")


def _keygen_local(kid: str, path: Path) -> bytes:
    path = Path(path)
    bundle: dict[str, str] = {}
    if path.exists():
        bundle = json.loads(path.read_text())
        if kid in bundle:
            raise SystemExit(
                f"ERROR: kid {kid!r} already exists in {path}. "
                f"Rotate via a new kid (dwc-dit-02, etc.) rather than overwriting."
            )
    priv = Ed25519PrivateKey.generate()
    bundle[kid] = _b64(priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    path.write_text(json.dumps(bundle, indent=2))
    print(f"Wrote private key → {path}")
    return priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _keygen_pkcs11(kid: str, module: str, slot: int | None,
                   token_label: str | None, label: str | None,
                   pin_env: str, pin: str | None) -> bytes:
    try:
        import pkcs11  # type: ignore[import-not-found]
        from pkcs11 import KeyType, Mechanism, Attribute  # type: ignore[import-not-found]
    except ImportError as e:
        raise SystemExit(
            "pkcs11 backend requires:  pip install dwc-sidecar[hsm]"
        ) from e

    import os
    resolved_pin = pin if pin is not None else os.environ.get(pin_env)
    if resolved_pin is None:
        raise SystemExit(f"PIN not provided: set env var {pin_env!r} or pass --pin")

    lib = pkcs11.lib(str(Path(module).expanduser()))
    if token_label is not None:
        token = lib.get_token(token_label=token_label)
    else:
        slots = lib.get_slots(token_present=True)
        token = slots[0].get_token() if slot is None else slots[slot].get_token()

    key_label = label or kid
    with token.open(rw=True, user_pin=resolved_pin) as session:
        pub, _priv = session.generate_keypair(
            KeyType.EC_EDWARDS, 256,
            mechanism=Mechanism.EC_EDWARDS_KEY_PAIR_GEN,
            label=key_label,
            store=True,
        )
        raw = bytes(pub[Attribute.EC_POINT])
        if len(raw) == 34 and raw[0] == 0x04 and raw[1] == 0x20:
            raw = raw[2:]
        print(f"Generated keypair in PKCS#11 token; private key never left hardware.")
        print(f"  module: {module}")
        print(f"  label:  {key_label}")
        return raw


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate a new Ed25519 signing key and emit a keyring.json entry."
    )
    ap.add_argument("--kid", required=True,
                     help="Key identifier (e.g. dwc-dit-02). Must match what signed events will declare.")
    ap.add_argument("--backend", choices=["local", "file", "pkcs11"], default="local",
                     help="Where the private key should live (default: local)")
    ap.add_argument("--valid-from", default=_iso_days(0),
                     help="ISO-8601 UTC timestamp; defaults to now")
    ap.add_argument("--valid-until", default=_iso_days(365),
                     help="ISO-8601 UTC timestamp; defaults to now + 365 days")

    # local / file
    ap.add_argument("--path", type=Path,
                     help="Private-key file path. Default: keys.priv.json for local, "
                          "required for file backend.")

    # pkcs11
    ap.add_argument("--module",
                     help="PKCS#11 vendor library path (.so/.dylib/.dll)")
    ap.add_argument("--slot", type=int, default=None,
                     help="PKCS#11 slot index (alternative to --token-label)")
    ap.add_argument("--token-label",
                     help="PKCS#11 token label (alternative to --slot)")
    ap.add_argument("--label",
                     help="Key object label inside the token (default: same as --kid)")
    ap.add_argument("--pin-env", default="DWC_PKCS11_PIN",
                     help="Env var holding the user PIN (default: DWC_PKCS11_PIN)")
    ap.add_argument("--pin", help="User PIN (insecure on command line; prefer --pin-env)")

    args = ap.parse_args()

    if args.backend in ("local", "file"):
        path = args.path or (Path("keys.priv.json") if args.backend == "local" else None)
        if path is None:
            print("ERROR: --path is required with --backend=file", file=sys.stderr)
            return 2
        pub_raw = _keygen_local(args.kid, path)
    else:
        if not args.module:
            print("ERROR: --module is required with --backend=pkcs11", file=sys.stderr)
            return 2
        pub_raw = _keygen_pkcs11(
            args.kid, args.module, args.slot, args.token_label,
            args.label, args.pin_env, args.pin,
        )

    _emit_keyring_entry(args.kid, pub_raw, args.valid_from, args.valid_until)
    return 0


if __name__ == "__main__":
    sys.exit(main())
