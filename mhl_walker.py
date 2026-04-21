#!/usr/bin/env python3
"""MHL-Walker — generate signed sidecars from MHLs that the DIT tool already wrote.

Zero re-read of clip bytes: the clip-integrity hash in each sidecar is lifted
directly from the MHL's own declaration. Only small auxiliary files (AMF, CDL,
FDL, the MHL itself) get hashed — they're KB-scale, not GB.

This runs *after* Silverstack/YoYotta/Hedge/ShotPut Pro finish an offload.
Tool-agnostic: any MHL v1 (XML) or v2 (YAML) works.

Usage:
  python3 mhl_walker.py <production-root> [--out-dir sidecars] [--signing-kid KID]
"""
import argparse, base64, json, sys, time, uuid
from datetime import datetime, timezone
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from canonical import canonical_bytes, event_hash, file_digest, HASH_ALGS  # type: ignore[import-not-found]
from mhl import parse_mhl  # type: ignore[import-not-found]

HERE      = Path(__file__).parent
PRIV_KEYS = HERE / "keys.priv.json"

CLIP_EXTS = {".mxf", ".ari", ".r3d", ".braw", ".mov", ".mp4", ".dpx", ".exr"}
MIME_MAP  = {
    "mxf":  ("digital.movingImage",   "application/mxf"),
    "ari":  ("digital.imageSequence", "image/x-arri-arriraw"),
    "r3d":  ("digital.movingImage",   "video/redcode"),
    "braw": ("digital.movingImage",   "video/braw"),
    "mov":  ("digital.movingImage",   "video/quicktime"),
    "mp4":  ("digital.movingImage",   "video/mp4"),
    "dpx":  ("digital.imageSequence", "image/x-dpx"),
    "exr":  ("digital.imageSequence", "image/x-exr"),
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _pick_hash_from_mhl_entry(entry: dict) -> tuple[str, str] | None:
    """Return (alg, value) from an MHL Hashes[] entry, preferring strong hashes."""
    for alg in ("sha512", "sha256", "blake3", "c4", "sha1", "xxh3", "xxh64", "md5"):
        if alg in entry and entry[alg]:
            return alg, str(entry[alg])
    return None


def _rel(p: Path, base: Path) -> str:
    p = p.resolve()
    return f"./{p.relative_to(base).as_posix()}" if str(p).startswith(str(base)) else str(p)


def _aux_artifact(path: Path, base: Path, role: str, kind: str, subtype=None):
    a = {
        "id":   f"urn:uuid:{uuid.uuid4()}",
        "role": role, "kind": kind,
        "path": _rel(path, base),
        "hash": {"alg": "sha256", "value": file_digest(path, "sha256")},
    }
    if subtype: a["subtype"] = subtype
    return a


def _mhl_artifact(mhl_path: Path, base: Path, mhl_entry: str):
    return {
        "id":        f"urn:uuid:{uuid.uuid4()}",
        "role":      "integrity", "kind": "asc-mhl",
        "path":      _rel(mhl_path, base),
        "mhlEntry":  mhl_entry,
        "hash":      {"alg": "sha256", "value": file_digest(mhl_path, "sha256")},
        "immutable": True,
    }


def build_sidecar_from_mhl_entry(
    mhl_path: Path, mhl_entry: str, clip_abs: Path, hash_alg: str, hash_val: str,
    base: Path, amf_dir: Path | None, cdl_dir: Path | None, fdl: Path | None,
    signer_priv, signer_kid: str,
) -> dict:
    clip_name = clip_abs.stem
    ts        = _iso_now()

    clip_integrity = {
        "id":   f"urn:uuid:{uuid.uuid4()}",
        "role": "clip-integrity", "kind": "source-file",
        "path": _rel(clip_abs, base),
        "hash": {"alg": hash_alg, "value": hash_val},  # <-- lifted from MHL, no re-read
    }

    artifacts = [clip_integrity, _mhl_artifact(mhl_path, base, mhl_entry)]

    if amf_dir:
        p = amf_dir / f"{clip_name}.amf"
        if p.exists(): artifacts.append(_aux_artifact(p, base, "color-pipeline", "amf"))
    if cdl_dir:
        p = cdl_dir / f"{clip_name}.cdl"
        if p.exists(): artifacts.append(_aux_artifact(p, base, "color-pipeline", "cdl"))
    if fdl and fdl.exists():
        artifacts.append(_aux_artifact(fdl, base, "framing", "asc-fdl"))

    clip_uuid = str(uuid.uuid4())
    sc_uuid   = f"astsc-{clip_uuid}"

    event = {
        "seq": 1, "ts": ts,
        "actor":  {"id": "urn:email:mhl-walker@the-dwc.com", "role": "IngestService"},
        "tool":   {"name": "mhl_walker.py", "version": "0.1"},
        "action": "create",
        "target": f"urn:uuid:{clip_uuid}",
        "prevHash": None,
    }
    event["hash"] = event_hash(event)
    event["sig"]  = {"alg": "ed25519", "kid": signer_kid,
                      "value": base64.b64encode(signer_priv.sign(canonical_bytes(event))).decode()}

    ext = clip_abs.suffix.lower().lstrip(".")
    struct_type, mime = MIME_MAP.get(ext, ("digital.movingImage", "application/octet-stream"))

    return {
        "Asset": [{
            "schemaVersion": "https://movielabs.com/omc/json/schema/v2.8",
            "entityType": "Asset",
            "identifier": [
                {"identifierScope": "dwc:clip-uuid",       "identifierValue": clip_uuid},
                {"identifierScope": "dwc:source-filename", "identifierValue": clip_name},
            ],
            "name": clip_name,
            "description": f"Ingested from {mhl_path.name} via mhl_walker",
            "provenance": {
                "CreatedBy": {"identifier": [
                    {"identifierScope": "dwc:email", "identifierValue": "mhl-walker@the-dwc.com"}]},
                "createdOn": ts,
                "reason":    "MHL walk — no clip re-read",
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
                         "value": [event]},
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
                "identifier": [{"identifierScope": "dwc:clip-sc-uuid", "identifierValue": sc_uuid}],
                "structuralType": struct_type,
                "structuralProperties": {
                    "linkset":    {"recordType": "item", "mediaType": mime},
                    "fileDetails": {
                        "fileName":      clip_name,
                        "filePath":      f"{clip_abs.parent.relative_to(base).as_posix()}/"
                                          if str(clip_abs.parent).startswith(str(base)) else str(clip_abs.parent)+"/",
                        "fileExtension": ext,
                        "mediaType":     mime,
                    },
                    "purpose": "general",
                }
            }
        }]
    }


def find_mhls(root: Path) -> list[Path]:
    return sorted(list(root.rglob("*.mhl")) + list(root.rglob("*.ascmhl")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--out-dir",  type=Path, default=HERE / "sidecars-mhl")
    ap.add_argument("--amf-dir",  type=Path, default=None,
                     help="Directory of per-clip AMFs (default: <root>/Colour-Information/AMF if present)")
    ap.add_argument("--cdl-dir",  type=Path, default=None,
                     help="Directory of per-clip CDLs (default: <root>/Colour-Information/CDLs/CDL_Output if present)")
    ap.add_argument("--fdl",      type=Path, default=None,
                     help="Show-wide FDL file (default: first *.fdl under <root>/Colour-Information/FDL)")
    ap.add_argument("--signing-kid", default="dwc-dit-01")
    ap.add_argument("--validate", action="store_true")
    args = ap.parse_args()

    base = args.root.resolve()
    amf  = args.amf_dir or (base / "Colour-Information/AMF")
    cdl  = args.cdl_dir or (base / "Colour-Information/CDLs/CDL_Output")
    fdl  = args.fdl
    if fdl is None:
        fdl_dir = base / "Colour-Information/FDL"
        if fdl_dir.exists():
            fdls = list(fdl_dir.glob("*.fdl"))
            fdl = fdls[0] if fdls else None
    amf  = amf if amf and amf.exists() else None
    cdl  = cdl if cdl and cdl.exists() else None

    priv_bundle = json.loads(PRIV_KEYS.read_text())
    priv = Ed25519PrivateKey.from_private_bytes(base64.b64decode(priv_bundle[args.signing_kid]))
    args.out_dir.mkdir(parents=True, exist_ok=True)

    mhls = find_mhls(base)
    print(f"Found {len(mhls)} MHL(s) under {base}")
    if not mhls:
        return 1

    t0 = time.perf_counter()
    sidecars_written = 0
    entries_skipped  = 0
    total_clip_bytes = 0

    for mhl in mhls:
        try:
            parsed = parse_mhl(mhl)
        except Exception as e:
            print(f"  {mhl.relative_to(base)}: PARSE FAIL — {e}")
            continue
        roll_dir = mhl.parent
        for entry in parsed.get("Hashes") or []:
            f = entry.get("File")
            if not f: continue
            ext = Path(f).suffix.lower()
            if ext not in CLIP_EXTS:
                entries_skipped += 1
                continue
            clip_abs = (roll_dir / f).resolve()
            if not clip_abs.exists():
                print(f"  skip (missing clip): {clip_abs.relative_to(base) if str(clip_abs).startswith(str(base)) else clip_abs}")
                continue
            picked = _pick_hash_from_mhl_entry(entry)
            if picked is None:
                print(f"  skip (no supported alg): {f}")
                continue
            hash_alg, hash_val = picked
            if hash_alg not in HASH_ALGS:
                print(f"  skip (alg {hash_alg} not in our registry): {f}")
                continue
            mhl_entry_str = f if isinstance(f, str) else str(f)
            doc = build_sidecar_from_mhl_entry(
                mhl, mhl_entry_str, clip_abs, hash_alg, hash_val,
                base, amf, cdl, fdl, priv, args.signing_kid,
            )
            out = args.out_dir / f"{clip_abs.stem}.omc.json"
            out.write_text(json.dumps(doc, indent=2) + "\n")
            sidecars_written += 1
            total_clip_bytes += clip_abs.stat().st_size

    dt = time.perf_counter() - t0
    print(f"\nWrote {sidecars_written} sidecar(s) in {dt:.2f}s  "
          f"({total_clip_bytes/1024**3:.2f} GB of clip data covered, "
          f"{sidecars_written/dt if dt else 0:.1f} sidecars/s)")
    print(f"({entries_skipped} non-clip MHL entries skipped)")

    if args.validate:
        print("\nValidating…")
        import subprocess
        fails = 0
        vt0 = time.perf_counter()
        for sc in sorted(args.out_dir.glob("*.omc.json")):
            r = subprocess.run(
                ["python3", "validate.py", str(sc), "--base-dir", str(base)],
                capture_output=True, text=True)
            if r.returncode != 0:
                fails += 1
                tail = r.stdout.strip().splitlines()[-2:]
                print(f"  FAIL {sc.name}: {' | '.join(tail)}")
        vdt = time.perf_counter() - vt0
        print(f"Validate: {sidecars_written - fails} OK / {fails} FAIL in {vdt:.1f}s")

    return 0


if __name__ == "__main__":
    sys.exit(main())
