#!/usr/bin/env python3
"""Regenerate demo keys and re-sign every example sidecar, then publish the
matching keyring.json.

Covers every example file and every Asset inside each (the reel nests two
clips), not just example-clip's first Asset — the documented key-rotation
procedure broke twice because the reel's signatures went stale.

Per asset it rewrites, in v0.2 form:
  - event artifact commitments (create commits to every artifact, attach to
    its target artifact) — the signed body covers the integrity claims;
  - the hash chain + Ed25519 signatures (each event keeps its existing kid);
  - the lock records' real signatures (JCS canonical bytes minus 'sig');
  - the signed chain-head anchor (dwc.sidecar.head);
  - namespace/schema URLs on all dwc.sidecar.* customData entries.

Run after editing events in an example, or to bootstrap the demo. Uses the
local JSON-file signer backend regardless of DWC_SIGNERS — demo keys only ever
live in keys.priv.json, never in an HSM."""
import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .canonical import (
    canonical_bytes, event_hash, dump_pubkey_b64,
    make_head, SIDECAR_NS,
)
from .signers.jsonfile import JsonFileSigner

# Demo files resolved against the caller's CWD (repo root in practice).
EXAMPLES = [Path("example-clip.omc.json"), Path("example-reel.omc.json")]
KEYRING  = Path("keyring.json")
PRIVKEYS = Path("keys.priv.json")   # demo only; gitignore in real use

KIDS = ["dwc-dit-01", "dwc-color-01", "dwc-post-01", "dwc-transcode-01"]

# Key validity windows. Real deployments would rotate; these cover 2026 calendar year
# and illustrate that Stage 4 checks event ts against validFrom / validUntil.
KEY_WINDOWS = {
    "dwc-dit-01":       {"validFrom": "2026-01-01T00:00:00Z", "validUntil": "2027-01-01T00:00:00Z"},
    "dwc-color-01":     {"validFrom": "2026-01-01T00:00:00Z", "validUntil": "2027-01-01T00:00:00Z"},
    "dwc-post-01":      {"validFrom": "2026-01-01T00:00:00Z", "validUntil": "2027-01-01T00:00:00Z"},
    "dwc-transcode-01": {"validFrom": "2026-01-01T00:00:00Z", "validUntil": "2027-01-01T00:00:00Z"},
}

# kid → actor binding (Phase 1.2). Stage 4 FAILs any event signed by a key whose
# keyring entry declares an actor but whose actor.id differs. The proxy build is
# its own party (the transcoder), so it gets its own key rather than borrowing
# the colorist's — otherwise dwc-color-01 would be bound to two actors.
KEY_ACTORS = {
    "dwc-dit-01":       "urn:email:dit@the-dwc.com",
    "dwc-color-01":     "urn:email:colorist@the-dwc.com",
    "dwc-post-01":      "urn:email:post@the-dwc.com",
    "dwc-transcode-01": "urn:email:transcoder@the-dwc.com",
}


def _write_privkeys(keys: dict[str, Ed25519PrivateKey]) -> None:
    payload = json.dumps({
        kid: base64.b64encode(k.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )).decode() for kid, k in keys.items()
    }, indent=2)
    # 0o600: private keys must not be group/world-readable (shared NAS hosts).
    fd = os.open(PRIVKEYS, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(payload)
    os.chmod(PRIVKEYS, 0o600)


def load_or_make_keys() -> dict[str, Ed25519PrivateKey]:
    raw = json.loads(PRIVKEYS.read_text()) if PRIVKEYS.exists() else {}
    keys: dict[str, Ed25519PrivateKey] = {}
    generated = []
    for kid in KIDS:
        if kid in raw:
            keys[kid] = Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw[kid]))
        else:
            keys[kid] = Ed25519PrivateKey.generate()
            generated.append(kid)
    # Persist if we minted any new kid (e.g. a demo key added in a later release)
    # so the private material and the keyring stay in lockstep.
    if generated or not PRIVKEYS.exists():
        _write_privkeys(keys)
        print(f"Generated demo keys for {generated or KIDS} → {PRIVKEYS.name}")
    return keys


def write_keyring(keys: dict[str, Ed25519PrivateKey]) -> None:
    KEYRING.write_text(json.dumps({
        "alg": "ed25519",
        "keys": {
            kid: {
                "publicKey":        dump_pubkey_b64(k.public_key()),
                "validFrom":        KEY_WINDOWS[kid]["validFrom"],
                "validUntil":       KEY_WINDOWS[kid]["validUntil"],
                "revokedAt":        None,
                "revocationReason": None,
                "actor":            KEY_ACTORS[kid],
            }
            for kid, k in keys.items()
        },
    }, indent=2))
    print(f"Wrote public keyring → {KEYRING.name}")


def _iter_assets(node):
    """Yield every dict with entityType == 'Asset' anywhere in the doc."""
    if isinstance(node, dict):
        if node.get("entityType") == "Asset":
            yield node
        for v in node.values():
            yield from _iter_assets(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_assets(v)


def _fallback_kid(ev: dict) -> str:
    return KIDS[0] if ev.get("seq") == 1 else (
        "dwc-color-01" if ev.get("action") == "attach" else "dwc-post-01"
    )


def _sig_block(signer: JsonFileSigner, record: dict) -> dict:
    return {"alg": "ed25519", "kid": signer.kid,
            "value": base64.b64encode(signer.sign(canonical_bytes(record))).decode()}


def re_sign_asset(asset: dict, signers: dict[str, JsonFileSigner]) -> bool:
    """Re-sign one Asset's payloads in place. Returns True if it had events."""
    fp = (asset.get("assetFC") or {}).get("functionalProperties") or {}
    cd = fp.get("customData")
    if not cd:
        return False
    by_domain = {e.get("domain"): e for e in cd if isinstance(e, dict)}
    ev_entry = by_domain.get("dwc.sidecar.events")
    if ev_entry is None:
        return False

    # Bump namespace/schema URLs to the current format version.
    for e in cd:
        if isinstance(e, dict) and str(e.get("domain", "")).startswith("dwc.sidecar."):
            name = e["domain"].split(".")[-1]
            e["namespace"] = SIDECAR_NS
            e["schema"]    = f"{SIDECAR_NS}/{name}.schema.json"

    artifacts = (by_domain.get("dwc.sidecar.artifacts") or {}).get("value") or []
    by_id     = {a["id"]: a for a in artifacts if isinstance(a, dict) and a.get("id")}
    events    = ev_entry.get("value") or []

    prev = None
    last_kid = KIDS[0]
    last_transfer_hash = None
    for ev in events:
        kid = (ev.get("sig") or {}).get("kid") or _fallback_kid(ev)
        ev["prevHash"] = prev
        ev.pop("hash", None)
        ev.pop("sig",  None)
        # v0.2 commitments: create (and a transfer hand-off) commit every artifact
        # hash; attach commits its target's. A transfer re-commits the bytes being
        # handed over so the receiver's `accept` attests *these* hashes (Phase 3).
        if ev.get("action") in ("create", "transfer") and artifacts:
            ev["artifacts"] = [{"id": a["id"], "hash": dict(a["hash"])} for a in by_id.values()]
        elif ev.get("action") == "attach" and ev.get("target") in by_id:
            a = by_id[ev["target"]]
            ev["artifacts"] = [{"id": a["id"], "hash": dict(a["hash"])}]
        # An `accept` commits by hash to the offer it counter-signs; backfill that
        # reference so the example stays valid after the offer is re-hashed.
        if ev.get("action") == "accept" and isinstance(ev.get("transfer"), dict):
            if last_transfer_hash is not None:
                ev["transfer"]["of"] = last_transfer_hash
        h = event_hash(ev)
        ev["hash"] = h
        ev["sig"]  = _sig_block(signers[kid], ev)
        if ev.get("action") == "transfer":
            last_transfer_hash = h
        prev, last_kid = h, kid
        print(f"    seq={ev['seq']:<2} action={ev['action']:<10} kid={kid}")

    # Locks: real signatures over the lock record (not the "BASE64..." stub).
    # Threshold locks (policy + sigs) co-sign the *same* JCS bytes — canonical_bytes
    # strips both 'sig' and 'sigs', so every co-signature covers the policy itself.
    locks = (by_domain.get("dwc.sidecar.locks") or {}).get("value") or []
    for lk in locks:
        policy = lk.get("policy")
        if isinstance(policy, dict) and policy.get("of"):
            lk.pop("sig", None)
            lk.pop("sigs", None)
            lk["sigs"] = [
                {"alg": "ed25519", "kid": kid,
                 "value": base64.b64encode(signers[kid].sign(canonical_bytes(lk))).decode()}
                for kid in policy["of"] if kid in signers
            ]
            print(f"    lock target={lk.get('target')} policy={policy.get('m')}-of-"
                  f"{len(policy.get('of', []))} kids={[s['kid'] for s in lk['sigs']]}")
        else:
            kid = (lk.get("sig") or {}).get("kid") or "dwc-post-01"
            lk.pop("sig", None)
            lk["sig"] = _sig_block(signers[kid], lk)
            print(f"    lock target={lk.get('target')} kid={kid}")

    # Chain-head anchor, signed by whoever appended last.
    if events:
        head = make_head(events[-1], signers[last_kid])
        head_entry = by_domain.get("dwc.sidecar.head")
        if head_entry is None:
            cd.append({"domain": "dwc.sidecar.head",
                       "namespace": SIDECAR_NS,
                       "schema":    f"{SIDECAR_NS}/head.schema.json",
                       "value": head})
        else:
            head_entry["value"] = head
        print(f"    head seq={head['seq']} kid={signers[last_kid].kid}")
    return True


def main() -> None:
    keys = load_or_make_keys()
    write_keyring(keys)
    signers = {kid: JsonFileSigner(kid, PRIVKEYS) for kid in KIDS}
    for example in EXAMPLES:
        if not example.exists():
            print(f"skip (not found): {example.name}")
            continue
        doc = json.loads(example.read_text())
        print(f"\nRe-signing {example.name}:")
        n = 0
        for asset in _iter_assets(doc):
            if re_sign_asset(asset, signers):
                n += 1
        example.write_text(json.dumps(doc, indent=2) + "\n")
        print(f"  {n} asset(s) re-signed → {example.name}")


if __name__ == "__main__":
    main()
