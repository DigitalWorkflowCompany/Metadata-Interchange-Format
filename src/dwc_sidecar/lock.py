#!/usr/bin/env python3
"""`dwc lock` / `dwc lock cosign` — create and co-sign m-of-n threshold locks.

    dwc lock <sidecar> --target <urn> --scope artifact \\
             --policy 2 --of dwc-dit-01,dwc-post-01 \\
             --signing-kid dwc-dit-01 [--reason "…"] [--clip-uuid …]

        Appends a signed `lock` event, creates the lock record carrying the
        policy plus the initiator's first co-signature, and rewrites the head.

    dwc lock cosign <sidecar> --target <urn> --signing-kid dwc-post-01 [--clip-uuid …]

        Verifies the chain, appends this co-signer's `lock` event, adds their
        co-signature to the existing lock record, rewrites the head, and prints
        whether the threshold is now satisfied.

Both go through append.append_event (verify-before-append) and
signers.get_signer (so HSM/cloud backends work unchanged — co-signing is
exactly the DIT-laptop-plus-post-HSM scenario)."""
import argparse
import base64
import json
import sys
from pathlib import Path

from .append import append_event, load_sidecar_asset, save_sidecar
from .canonical import canonical_bytes
from .signers import get_signer
from .validate import _asset_payloads, KEYRING

SIDECAR_NS_NOTE = "dwc.sidecar.locks"


def _actor_for_kid(kid: str, explicit: str | None) -> str:
    """Resolve the actor URN for a signing kid: explicit flag wins, else the
    keyring's actor binding, else error (a lock event needs a real actor.id and
    Stage 4 will reject a guess that doesn't match the key's binding)."""
    if explicit:
        return explicit if explicit.startswith("urn:") else f"urn:email:{explicit}"
    if KEYRING.exists():
        try:
            entry = (json.loads(KEYRING.read_text()).get("keys") or {}).get(kid) or {}
        except Exception:
            entry = {}
        if isinstance(entry, dict) and entry.get("actor"):
            return entry["actor"]
    raise SystemExit(
        f"ERROR: no actor for kid {kid!r}. Pass --actor <urn:email:…>, or bind the "
        f"key in keyring.json (dwc keygen --actor …) so the lock event's actor.id "
        f"matches what Stage 4 expects.")


def _locks_entry(asset: dict) -> dict:
    payloads = _asset_payloads(asset)
    entry = payloads.get("dwc.sidecar.locks")
    if entry is None:
        raise SystemExit("ERROR: asset has no dwc.sidecar.locks payload to extend")
    entry.setdefault("value", [])
    return entry


def _threshold_status(lock: dict) -> tuple[int, int]:
    return len({s.get("kid") for s in lock.get("sigs") or []}), (lock.get("policy") or {}).get("m", 0)


def _cmd_lock(argv) -> int:
    ap = argparse.ArgumentParser(prog="dwc lock", description="Create an m-of-n threshold lock")
    ap.add_argument("sidecar", type=Path)
    ap.add_argument("--target", required=True, help="URN the lock applies to (artifact id or entity urn)")
    ap.add_argument("--scope", default="artifact", choices=["artifact", "entity", "field"])
    ap.add_argument("--field", help="JSON Pointer to the locked field (scope=field only)")
    ap.add_argument("--policy", type=int, required=True, metavar="M",
                     help="Number of distinct co-signers required (the 'm' of m-of-n)")
    ap.add_argument("--of", required=True,
                     help="Comma-separated eligible kids (the 'n' of m-of-n)")
    ap.add_argument("--signing-kid", required=True,
                     help="kid that signs the first lock event + first co-signature")
    ap.add_argument("--actor", help="Actor URN for the lock event (default: keyring binding)")
    ap.add_argument("--role", default="Locker", help="Actor role label for the event")
    ap.add_argument("--reason", help="Human reason recorded on the lock")
    ap.add_argument("--clip-uuid", help="Select an asset in a multi-asset (reel) sidecar")
    args = ap.parse_args(argv)

    of = [k.strip() for k in args.of.split(",") if k.strip()]
    if args.signing_kid not in of:
        raise SystemExit(f"ERROR: --signing-kid {args.signing_kid!r} must be one of --of {of}")
    if args.policy < 1 or args.policy > len(of):
        raise SystemExit(f"ERROR: --policy {args.policy} must be between 1 and len(--of)={len(of)}")

    doc, asset = load_sidecar_asset(args.sidecar, clip_uuid=args.clip_uuid)
    actor = _actor_for_kid(args.signing_kid, args.actor)
    signer = get_signer(args.signing_kid)

    ev = append_event(asset, {
        "actor": {"id": actor, "role": args.role},
        "tool":  {"name": "dwc lock", "version": "0.6.0"},
        "action": "lock",
        "target": args.target,
    }, signer)

    lock = {
        "target": args.target,
        "scope":  args.scope,
        "by":     actor,
        "at":     ev["ts"],
    }
    if args.scope == "field":
        if not args.field:
            raise SystemExit("ERROR: --field is required when --scope field")
        lock["field"] = args.field
    if args.reason:
        lock["reason"] = args.reason
    lock["policy"] = {"m": args.policy, "of": of}
    # First co-signature over JCS(lock minus sig/sigs); cosign adds the rest later.
    lock["sigs"] = [{"alg": "ed25519", "kid": args.signing_kid,
                     "value": base64.b64encode(signer.sign(canonical_bytes(lock))).decode()}]

    _locks_entry(asset)["value"].append(lock)
    save_sidecar(args.sidecar, doc)

    k, m = _threshold_status(lock)
    print(f"✓ created {args.policy}-of-{len(of)} lock on {args.target}")
    print(f"  signed lock event seq={ev['seq']} by {args.signing_kid}")
    print(f"  threshold {'satisfied' if k >= m else 'pending'} ({k}/{m})")
    return 0


def _cmd_cosign(argv) -> int:
    ap = argparse.ArgumentParser(prog="dwc lock cosign",
                                  description="Add a co-signature to an existing threshold lock")
    ap.add_argument("sidecar", type=Path)
    ap.add_argument("--target", required=True, help="URN of the lock to co-sign")
    ap.add_argument("--signing-kid", required=True)
    ap.add_argument("--actor", help="Actor URN for the lock event (default: keyring binding)")
    ap.add_argument("--role", default="Locker")
    ap.add_argument("--clip-uuid")
    args = ap.parse_args(argv)

    doc, asset = load_sidecar_asset(args.sidecar, clip_uuid=args.clip_uuid)
    locks = _locks_entry(asset)["value"]
    lock = next((lk for lk in locks
                 if isinstance(lk, dict) and lk.get("target") == args.target
                 and lk.get("policy") is not None), None)
    if lock is None:
        raise SystemExit(f"ERROR: no threshold lock with target {args.target!r} in this asset")

    of = (lock.get("policy") or {}).get("of") or []
    if args.signing_kid not in of:
        raise SystemExit(f"ERROR: --signing-kid {args.signing_kid!r} is not in this lock's "
                         f"policy.of {of}")
    if any(s.get("kid") == args.signing_kid for s in lock.get("sigs") or []):
        raise SystemExit(f"ERROR: {args.signing_kid!r} has already co-signed this lock")

    actor = _actor_for_kid(args.signing_kid, args.actor)
    signer = get_signer(args.signing_kid)  # verify-before-append happens inside append_event
    ev = append_event(asset, {
        "actor": {"id": actor, "role": args.role},
        "tool":  {"name": "dwc lock cosign", "version": "0.6.0"},
        "action": "lock",
        "target": args.target,
    }, signer)

    # The body (target/scope/by/at/policy/…) is unchanged, so existing co-sigs
    # stay valid; we only append this kid's signature over the same JCS bytes.
    lock.setdefault("sigs", []).append({
        "alg": "ed25519", "kid": args.signing_kid,
        "value": base64.b64encode(signer.sign(canonical_bytes(lock))).decode(),
    })
    save_sidecar(args.sidecar, doc)

    k, m = _threshold_status(lock)
    print(f"✓ co-signed lock on {args.target} as {args.signing_kid}")
    print(f"  signed lock event seq={ev['seq']}")
    print(f"  threshold {'satisfied' if k >= m else 'pending'} ({k}/{m})")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "cosign":
        return _cmd_cosign(argv[1:])
    return _cmd_lock(argv)


if __name__ == "__main__":
    sys.exit(main())
