#!/usr/bin/env python3
"""Regenerate demo keys, rewrite event hash-chain + signatures in example-clip.omc.json,
and publish the matching keyring.json.

Run after editing events in the example, or to bootstrap the demo."""
import base64, json
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from .canonical import canonical_bytes, event_hash, dump_pubkey_b64

# Demo files resolved against the caller's CWD (repo root in practice).
EXAMPLE  = Path("example-clip.omc.json")
KEYRING  = Path("keyring.json")
PRIVKEYS = Path("keys.priv.json")   # demo only; gitignore in real use

KIDS = ["dwc-dit-01", "dwc-color-01", "dwc-post-01"]

# Key validity windows. Real deployments would rotate; these cover 2026 calendar year
# and illustrate that Stage 4 checks event ts against validFrom / validUntil.
KEY_WINDOWS = {
    "dwc-dit-01":   {"validFrom": "2026-01-01T00:00:00Z", "validUntil": "2027-01-01T00:00:00Z"},
    "dwc-color-01": {"validFrom": "2026-01-01T00:00:00Z", "validUntil": "2027-01-01T00:00:00Z"},
    "dwc-post-01":  {"validFrom": "2026-01-01T00:00:00Z", "validUntil": "2027-01-01T00:00:00Z"},
}


def load_or_make_keys():
    if PRIVKEYS.exists():
        raw = json.loads(PRIVKEYS.read_text())
        return {
            kid: Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw[kid]))
            for kid in KIDS
        }
    keys = {kid: Ed25519PrivateKey.generate() for kid in KIDS}
    PRIVKEYS.write_text(json.dumps({
        kid: base64.b64encode(k.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )).decode() for kid, k in keys.items()
    }, indent=2))
    print(f"Generated demo keys → {PRIVKEYS.name}")
    return keys


def write_keyring(keys):
    KEYRING.write_text(json.dumps({
        "alg": "ed25519",
        "keys": {
            kid: {
                "publicKey":        dump_pubkey_b64(k.public_key()),
                "validFrom":        KEY_WINDOWS[kid]["validFrom"],
                "validUntil":       KEY_WINDOWS[kid]["validUntil"],
                "revokedAt":        None,
                "revocationReason": None,
            }
            for kid, k in keys.items()
        },
    }, indent=2))
    print(f"Wrote public keyring → {KEYRING.name}")


def re_sign_events(doc, keys):
    cd = doc["Asset"][0]["assetFC"]["functionalProperties"]["customData"]
    events = next(e["value"] for e in cd if e["domain"] == "dwc.sidecar.events")

    prev = None
    for ev in events:
        # link to previous
        ev["prevHash"] = prev
        # drop old hash/sig, recompute
        ev.pop("hash", None)
        ev.pop("sig",  None)
        kid = KIDS[0] if ev["seq"] == 1 else (
            "dwc-color-01" if ev["action"] == "attach" else "dwc-post-01"
        )
        # stamp new hash over canonical body
        h = event_hash(ev)
        # sign canonical body
        priv = keys[kid]
        sig_b64 = base64.b64encode(priv.sign(canonical_bytes(ev))).decode()
        ev["hash"] = h
        ev["sig"]  = {"alg": "ed25519", "kid": kid, "value": sig_b64}
        prev = h
        print(f"  seq={ev['seq']:<2} action={ev['action']:<10} kid={kid}")


def main():
    keys = load_or_make_keys()
    write_keyring(keys)
    doc = json.loads(EXAMPLE.read_text())
    print("\nRe-signing events:")
    re_sign_events(doc, keys)
    EXAMPLE.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"\nUpdated → {EXAMPLE.name}")


if __name__ == "__main__":
    main()
