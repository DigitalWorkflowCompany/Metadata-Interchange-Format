#!/usr/bin/env python3
"""`dwc transfer offer` / `dwc transfer accept` — two-party custody hand-off.

    dwc transfer offer  <sidecar> --to urn:email:io@post.com --signing-kid dwc-dit-01

        Appends a sender-signed 'transfer' event that re-commits the artifact
        hashes being handed over and names the receiver (transfer.to).

    dwc transfer accept <sidecar> --signing-kid dwc-post-io-01 --base-dir <receiving-root>

        RE-VERIFIES the sidecar against the receiver's local copy of the files
        (full validator, --require-keyring) and refuses to counter-sign on any
        error — the acceptance signature attests a *successful verification at
        the receiving site*. Then appends a receiver-signed 'accept' event that
        commits by hash to the exact offer (transfer.of) and names the offerer
        (transfer.from).

Both go through append.append_event (verify-before-append) and
signers.get_signer (HSM/cloud backends work unchanged)."""
import argparse
import json
import sys
from pathlib import Path

from .append import append_event, load_sidecar_asset, save_sidecar
from .canonical import artifact_commitments
from .signers import get_signer
from .validate import _asset_payloads, KEYRING


def _actor_for_kid(kid: str, explicit: str | None) -> str:
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
        f"ERROR: no actor for kid {kid!r}. Pass --actor <urn:email:…>, or bind the key "
        f"in keyring.json (dwc keygen --actor …) so Stage 4 accepts the event's actor.id.")


def _clip_target(asset: dict) -> str:
    for i in asset.get("identifier") or []:
        if isinstance(i, dict) and i.get("identifierScope") == "dwc:clip-uuid" and i.get("identifierValue"):
            return f"urn:uuid:{i['identifierValue']}"
    raise SystemExit("ERROR: asset has no dwc:clip-uuid identifier to target")


def _artifacts(asset: dict) -> list:
    arts = ((_asset_payloads(asset).get("dwc.sidecar.artifacts") or {}).get("value")) or []
    return [a for a in arts if isinstance(a, dict) and a.get("id") and a.get("hash")]


def _cmd_offer(argv) -> int:
    ap = argparse.ArgumentParser(prog="dwc transfer offer",
                                  description="Offer custody of an asset to another party")
    ap.add_argument("sidecar", type=Path)
    ap.add_argument("--to", required=True, help="Receiver actor URN (or bare e-mail)")
    ap.add_argument("--signing-kid", required=True)
    ap.add_argument("--actor", help="Sender actor URN (default: keyring binding)")
    ap.add_argument("--role", default="DIT")
    ap.add_argument("--clip-uuid")
    args = ap.parse_args(argv)

    to = args.to if args.to.startswith("urn:") else f"urn:email:{args.to}"
    doc, asset = load_sidecar_asset(args.sidecar, clip_uuid=args.clip_uuid)
    actor = _actor_for_kid(args.signing_kid, args.actor)
    target = _clip_target(asset)

    ev = append_event(asset, {
        "actor": {"id": actor, "role": args.role},
        "tool":  {"name": "dwc transfer offer", "version": "0.6.0"},
        "action": "transfer",
        "target": target,
        "transfer": {"to": to},
        # Re-commit the bytes being handed over so 'accept' attests *these* hashes.
        "artifacts": artifact_commitments(_artifacts(asset)),
    }, get_signer(args.signing_kid))
    save_sidecar(args.sidecar, doc)

    print(f"✓ offered custody of {target}")
    print(f"  from {actor}  →  to {to}")
    print(f"  transfer event seq={ev['seq']} hash={ev['hash']}")
    print(f"  receiver counter-signs with:  dwc transfer accept {args.sidecar} "
          f"--signing-kid <their-kid> --base-dir <their-root>")
    return 0


def _cmd_accept(argv) -> int:
    ap = argparse.ArgumentParser(prog="dwc transfer accept",
                                  description="Counter-sign a custody offer after re-verifying")
    ap.add_argument("sidecar", type=Path)
    ap.add_argument("--signing-kid", required=True)
    ap.add_argument("--actor", help="Receiver actor URN (default: keyring binding)")
    ap.add_argument("--role", default="IO")
    ap.add_argument("--base-dir", type=Path,
                     help="Receiver's local root for the referenced files "
                          "(default: the sidecar's own directory)")
    ap.add_argument("--clip-uuid")
    args = ap.parse_args(argv)

    # Re-verify FIRST — the acceptance signature means "I verified these bytes".
    from .validate import validate_as_json
    base = args.base_dir or Path(args.sidecar).resolve().parent
    report = validate_as_json(args.sidecar, base_dir=base, require_keyring=True)
    if report["errors"]:
        print(f"✗ refusing to accept: sidecar fails verification at this site "
              f"({report['summary']})", file=sys.stderr)
        for s in report["stages"]:
            if s["errors"]:
                for ln in s["lines"]:
                    if "FAIL" in ln:
                        print(f"    {ln}", file=sys.stderr)
        return 1

    doc, asset = load_sidecar_asset(args.sidecar, clip_uuid=args.clip_uuid)
    events = ((_asset_payloads(asset).get("dwc.sidecar.events") or {}).get("value")) or []
    transfers = [e for e in events if isinstance(e, dict) and e.get("action") == "transfer"]
    accepted  = {(e.get("transfer") or {}).get("of") for e in events
                 if isinstance(e, dict) and e.get("action") == "accept"}
    pending = [t for t in transfers if t.get("hash") not in accepted]
    if not pending:
        print("✗ no pending transfer offer to accept in this asset", file=sys.stderr)
        return 1
    offer = pending[-1]

    actor = _actor_for_kid(args.signing_kid, args.actor)
    offer_to = (offer.get("transfer") or {}).get("to")
    if actor != offer_to:
        print(f"✗ refusing to accept: this key's actor {actor!r} is not the offer's "
              f"receiver {offer_to!r}", file=sys.stderr)
        return 1

    ev = append_event(asset, {
        "actor": {"id": actor, "role": args.role},
        "tool":  {"name": "dwc transfer accept", "version": "0.6.0"},
        "action": "accept",
        "target": offer.get("target"),
        "transfer": {"of": offer.get("hash"), "from": (offer.get("actor") or {}).get("id")},
    }, get_signer(args.signing_kid))
    save_sidecar(args.sidecar, doc)

    print(f"✓ accepted custody of {offer.get('target')}")
    print(f"  verified {report['summary']} at base-dir {base}")
    print(f"  accept event seq={ev['seq']} commits to offer {offer.get('hash')}")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] not in ("offer", "accept"):
        print("usage: dwc transfer {offer|accept} <sidecar> [...]", file=sys.stderr)
        return 2
    return _cmd_offer(argv[1:]) if argv[0] == "offer" else _cmd_accept(argv[1:])


if __name__ == "__main__":
    sys.exit(main())
