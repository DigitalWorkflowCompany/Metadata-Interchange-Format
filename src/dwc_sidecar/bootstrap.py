#!/usr/bin/env python3
"""Bootstrap a fresh OMC v2.8 + DWC sidecar from a clip + its referenced metadata
files (AMF / FDL / MHL / ALE). Signs the initial `create` event with a key from
keys.priv.json. Output validates clean against all 8 stages.

Example:
  python3 bootstrap.py \\
    --clip Camera/A001/A001_C042_0420AB.ari \\
    --mhl  delivery/A001.mhl --mhl-entry Camera/A001/A001_C042_0420AB.ari \\
    --amf  amf/A001_C042_0420AB.amf \\
    --fdl  fdl/S042.fdl \\
    --ale  resolve/day01.ale \\
    --actor dit@the-dwc.com --role DIT \\
    --tool Silverstack --tool-version 8.4.1 \\
    --signing-kid dwc-dit-01 \\
    --out  bootstrapped.omc.json
"""
import argparse, base64, json, sys, uuid
from datetime import datetime, timezone
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .canonical import canonical_bytes, event_hash, file_digest

PRIV_KEYS = Path("keys.priv.json")

# structuralType guesses by file extension
_EXT_STRUCT = {
    ".ari":  ("digital.imageSequence", "ARRIRAW",              "image/x-arri-arriraw"),
    ".dpx":  ("digital.imageSequence", "DPX",                  "image/x-dpx"),
    ".exr":  ("digital.imageSequence", "OpenEXR",              "image/x-exr"),
    ".mov":  ("digital.movingImage",   "QuickTime/ProRes",     "video/quicktime"),
    ".mxf":  ("digital.movingImage",   "MXF",                  "application/mxf"),
    ".mp4":  ("digital.movingImage",   "H.264",                "video/mp4"),
    ".r3d":  ("digital.movingImage",   "REDCODE RAW",          "video/redcode"),
    ".braw": ("digital.movingImage",   "Blackmagic RAW",       "video/braw"),
}


def _guess_structural(path: Path):
    s = _EXT_STRUCT.get(path.suffix.lower(),
                        ("digital.movingImage", "unknown", "application/octet-stream"))
    return s


def _artifact(role, kind, path: Path, base: Path, subtype=None, mhl_entry=None):
    rel = path.relative_to(base) if path.is_absolute() else path
    a = {
        "id":   f"urn:uuid:{uuid.uuid4()}",
        "role": role,
        "kind": kind,
        "path": f"./{rel.as_posix()}",
        "hash": {"alg": "sha256", "value": file_digest(path, "sha256")},
    }
    if subtype:   a["subtype"]   = subtype
    if mhl_entry: a["mhlEntry"]  = mhl_entry; a["immutable"] = True
    return a


def main():
    ap = argparse.ArgumentParser(description="Bootstrap a DWC/OMC clip sidecar")
    ap.add_argument("--clip", required=True, type=Path, help="Camera original file (required)")
    ap.add_argument("--mhl",       type=Path, help="ASC MHL file covering the clip")
    ap.add_argument("--mhl-entry", type=str,  help="Path within the MHL pointing to the clip")
    ap.add_argument("--amf",       type=Path, help="AMF color pipeline file")
    ap.add_argument("--fdl",       type=Path, help="ASC FDL framing file")
    ap.add_argument("--ale",       type=Path, help="Avid ALE export")
    ap.add_argument("--cdl",       type=Path, help="ASC CDL file (standalone colour decision)")
    ap.add_argument("--clip-hash", default="xxh64",
                     choices=["md5","sha1","sha256","sha512","blake3","xxh64","xxh3","c4"],
                     help="Hash alg used for the clip-integrity artifact (default xxh64 — "
                          "matches ASC MHL v1 speed profile)")
    ap.add_argument("--actor", default="user@example.com",
                                    help="Email of the creating actor")
    ap.add_argument("--role",   default="DIT",       help="Crew role")
    ap.add_argument("--tool",   default="bootstrap.py", help="Tool name")
    ap.add_argument("--tool-version", default="0.1", help="Tool version")
    ap.add_argument("--signing-kid",  default="dwc-dit-01",
                                       help="kid from keys.priv.json used to sign the create event")
    ap.add_argument("--base-dir",     type=Path,
                                       help="Base directory for relative paths (default: clip's parent)")
    ap.add_argument("--out",          type=Path, required=True)
    args = ap.parse_args()

    clip = args.clip.resolve()
    base = (args.base_dir or Path.cwd()).resolve()
    if not clip.exists():
        print(f"ERROR: clip not found: {clip}", file=sys.stderr); return 2

    # --- build artifacts (tolerant of missing optional paths) ---
    def _maybe(flag_name, path, role, kind, subtype=None, mhl_entry=None):
        if not path:
            return None
        p = path.resolve()
        if not p.exists():
            print(f"  warn: --{flag_name} {p} not found — skipping", file=sys.stderr)
            return None
        return _artifact(role=role, kind=kind, path=p, base=base,
                          subtype=subtype, mhl_entry=mhl_entry)

    artifacts = []
    if args.mhl and not args.mhl_entry:
        print("ERROR: --mhl requires --mhl-entry", file=sys.stderr); return 2

    # The clip itself is a first-class artifact (clip-integrity). Its hash goes
    # directly into the sidecar so integrity of the bytes on disk does not depend
    # on the MHL still being present.
    clip_hash_alg = args.clip_hash
    clip_art = {
        "id":   f"urn:uuid:{uuid.uuid4()}",
        "role": "clip-integrity",
        "kind": "source-file",
        "path": f"./{(clip.relative_to(base) if str(clip).startswith(str(base)) else clip).as_posix()}",
        "hash": {"alg": clip_hash_alg, "value": file_digest(clip, clip_hash_alg)},
    }
    artifacts.append(clip_art)

    for a in [
        _maybe("mhl", args.mhl, role="integrity",      kind="asc-mhl",
                mhl_entry=args.mhl_entry),
        _maybe("amf", args.amf, role="color-pipeline", kind="amf"),
        _maybe("fdl", args.fdl, role="framing",        kind="asc-fdl"),
        _maybe("ale", args.ale, role="edit-metadata",  kind="resolve-export",
                subtype="ale"),
        _maybe("cdl", args.cdl, role="color-pipeline", kind="cdl"),
    ]:
        if a: artifacts.append(a)

    # --- identifiers ---
    clip_uuid = str(uuid.uuid4())
    sc_uuid   = f"astsc-{clip_uuid}"
    now_iso   = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    struct_type, codec, mime = _guess_structural(clip)

    # --- initial create event (signed) ---
    create_event = {
        "seq": 1,
        "ts":  now_iso,
        "actor":  {"id": f"urn:email:{args.actor}", "role": args.role},
        "tool":   {"name": args.tool, "version": args.tool_version},
        "action": "create",
        "target": f"urn:uuid:{clip_uuid}",
        "prevHash": None,
    }
    if not PRIV_KEYS.exists():
        print(f"ERROR: {PRIV_KEYS.name} not found — run sign-example.py first", file=sys.stderr)
        return 2
    priv_bundle = json.loads(PRIV_KEYS.read_text())
    if args.signing_kid not in priv_bundle:
        print(f"ERROR: kid {args.signing_kid!r} not in {PRIV_KEYS.name}", file=sys.stderr); return 2
    priv = Ed25519PrivateKey.from_private_bytes(base64.b64decode(priv_bundle[args.signing_kid]))
    create_event["hash"] = event_hash(create_event)
    create_event["sig"]  = {"alg": "ed25519", "kid": args.signing_kid,
                             "value": base64.b64encode(priv.sign(canonical_bytes(create_event))).decode()}

    # --- assemble sidecar ---
    doc = {
        "Asset": [
            {
                "schemaVersion": "https://movielabs.com/omc/json/schema/v2.8",
                "entityType": "Asset",
                "identifier": [
                    {"identifierScope": "dwc:clip-uuid",       "identifierValue": clip_uuid},
                    {"identifierScope": "dwc:source-filename", "identifierValue": clip.stem},
                ],
                "name": clip.stem,
                "description": f"Bootstrapped sidecar for {clip.name}",
                "provenance": {
                    "CreatedBy": {"identifier": [
                        {"identifierScope": "dwc:email", "identifierValue": args.actor}
                    ]},
                    "Role": {"identifier": [
                        {"identifierScope": "dwc:crew-role", "identifierValue": args.role}
                    ]},
                    "createdOn": now_iso,
                    "reason":    f"Bootstrap by {args.tool} {args.tool_version}",
                },
                "assetFC": {
                    "functionalType": "capture.ocf",
                    "functionalProperties": {
                        "customData": [
                            {"domain": "dwc.sidecar.artifacts",
                             "namespace": "https://ns.the-dwc.com/sidecar/v0.1",
                             "schema":    "https://ns.the-dwc.com/sidecar/v0.1/artifacts.schema.json",
                             "value": artifacts},
                            {"domain": "dwc.sidecar.events",
                             "namespace": "https://ns.the-dwc.com/sidecar/v0.1",
                             "schema":    "https://ns.the-dwc.com/sidecar/v0.1/events.schema.json",
                             "value": [create_event]},
                            {"domain": "dwc.sidecar.locks",
                             "namespace": "https://ns.the-dwc.com/sidecar/v0.1",
                             "schema":    "https://ns.the-dwc.com/sidecar/v0.1/locks.schema.json",
                             "value": []},
                        ]
                    }
                },
                "AssetSC": {
                    "schemaVersion": "https://movielabs.com/omc/json/schema/v2.8",
                    "entityType": "AssetSC",
                    "identifier": [
                        {"identifierScope": "dwc:clip-sc-uuid", "identifierValue": sc_uuid}
                    ],
                    "structuralType": struct_type,
                    "structuralProperties": {
                        "codec": codec,
                        "linkset":    {"recordType": "item", "mediaType": mime},
                        "fileDetails": {
                            "fileName":      clip.stem,
                            "filePath":      clip.parent.relative_to(base).as_posix() + "/"
                                              if str(clip.parent).startswith(str(base)) else str(clip.parent) + "/",
                            "fileExtension": clip.suffix.lstrip("."),
                            "mediaType":     mime,
                        },
                        "purpose": "general",
                    }
                }
            }
        ]
    }

    args.out.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"✓ bootstrapped {args.out} ({len(artifacts)} artifact(s))")
    print(f"  clip uuid: {clip_uuid}")
    print(f"  signed by: {args.signing_kid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
