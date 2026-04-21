#!/usr/bin/env python3
"""Walk a production tree and bootstrap + validate a sidecar per clip.

Usage:
  python3 batch.py <production-root> [--out-dir sidecars] [--hash xxh64|sha256|c4]
"""
import argparse, base64, json, sys, time, uuid
from datetime import datetime, timezone
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from canonical import canonical_bytes, event_hash, file_digest  # type: ignore[import-not-found]

HERE      = Path(__file__).parent
PRIV_KEYS = HERE / "keys.priv.json"
EXT_OK    = {".mxf", ".ari", ".r3d", ".braw", ".mov", ".dpx", ".exr"}


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def find_mhl_for_roll(roll_dir: Path) -> Path | None:
    mhls = list(roll_dir.glob("*.mhl")) + list(roll_dir.glob("*.ascmhl"))
    return sorted(mhls)[0] if mhls else None


def find_clip_files(ocf_root: Path) -> list[tuple[Path, Path]]:
    """Return (clip_file, roll_dir) tuples. roll_dir is where the MHL lives."""
    out = []
    for clip in ocf_root.rglob("*"):
        if clip.is_file() and clip.suffix.lower() in EXT_OK:
            # roll_dir = ancestor containing a .mhl
            for parent in [clip.parent, *clip.parents]:
                if parent == ocf_root.parent:
                    break
                if find_mhl_for_roll(parent):
                    out.append((clip, parent))
                    break
    return sorted(out)


def build_sidecar(clip: Path, roll_dir: Path, base: Path,
                   amf_dir: Path, cdl_dir: Path, fdl: Path | None,
                   hash_alg: str, signer_priv, signer_kid: str) -> dict:
    clip_name = clip.stem
    mhl       = find_mhl_for_roll(roll_dir)
    # derive mhl_entry = path relative to roll_dir, POSIX-style
    mhl_entry = clip.relative_to(roll_dir).as_posix() if mhl else None
    amf       = amf_dir / f"{clip_name}.amf" if amf_dir else None
    cdl       = cdl_dir / f"{clip_name}.cdl" if cdl_dir else None

    artifacts = []

    def _rel(p: Path) -> str:
        p = p.resolve()
        return f"./{p.relative_to(base).as_posix()}" if str(p).startswith(str(base)) else str(p)

    def _make_art(path: Path, role, kind, subtype=None, mhl_entry=None,
                   alg="sha256"):
        a = {
            "id":   f"urn:uuid:{uuid.uuid4()}",
            "role": role, "kind": kind,
            "path": _rel(path),
            "hash": {"alg": alg, "value": file_digest(path, alg)},
        }
        if subtype:   a["subtype"]   = subtype
        if mhl_entry: a["mhlEntry"]  = mhl_entry; a["immutable"] = True
        return a

    # 1. Clip-integrity (fast hash)
    artifacts.append(_make_art(clip, "clip-integrity", "source-file", alg=hash_alg))
    # 2. MHL
    if mhl:
        artifacts.append(_make_art(mhl, "integrity", "asc-mhl", mhl_entry=mhl_entry))
    # 3. AMF
    if amf and amf.exists():
        artifacts.append(_make_art(amf, "color-pipeline", "amf"))
    # 4. CDL
    if cdl and cdl.exists():
        artifacts.append(_make_art(cdl, "color-pipeline", "cdl"))
    # 5. FDL
    if fdl and fdl.exists():
        artifacts.append(_make_art(fdl, "framing", "asc-fdl"))

    clip_uuid = str(uuid.uuid4())
    sc_uuid   = f"astsc-{clip_uuid}"
    ts        = _iso_now()

    event = {
        "seq": 1, "ts": ts,
        "actor":  {"id": "urn:email:batch@the-dwc.com", "role": "IngestService"},
        "tool":   {"name": "batch.py", "version": "0.1"},
        "action": "create",
        "target": f"urn:uuid:{clip_uuid}",
        "prevHash": None,
    }
    event["hash"] = event_hash(event)
    event["sig"]  = {"alg": "ed25519", "kid": signer_kid,
                      "value": base64.b64encode(signer_priv.sign(canonical_bytes(event))).decode()}

    struct_type = "digital.movingImage" if clip.suffix.lower() in {".mxf",".mov",".mp4",".r3d",".braw"} else "digital.imageSequence"
    mime        = {"mxf":"application/mxf","mov":"video/quicktime","mp4":"video/mp4",
                    "ari":"image/x-arri-arriraw","r3d":"video/redcode","braw":"video/braw",
                    "dpx":"image/x-dpx","exr":"image/x-exr"}.get(clip.suffix.lower().lstrip("."),
                                                                  "application/octet-stream")

    return {
        "Asset": [{
            "schemaVersion": "https://movielabs.com/omc/json/schema/v2.8",
            "entityType": "Asset",
            "identifier": [
                {"identifierScope": "dwc:clip-uuid",       "identifierValue": clip_uuid},
                {"identifierScope": "dwc:source-filename", "identifierValue": clip_name},
            ],
            "name": clip_name,
            "description": f"Batch-ingested {clip.name} from {roll_dir.name}",
            "provenance": {
                "CreatedBy": {"identifier": [
                    {"identifierScope": "dwc:email", "identifierValue": "batch@the-dwc.com"}]},
                "createdOn": ts,
                "reason":    "Batch ingest via batch.py",
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
                        "filePath":      str(clip.parent.relative_to(base)) + "/" if str(clip.parent).startswith(str(base)) else str(clip.parent)+"/",
                        "fileExtension": clip.suffix.lstrip("."),
                        "mediaType":     mime,
                    },
                    "purpose": "general",
                }
            }
        }]
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--out-dir", type=Path, default=HERE / "sidecars")
    ap.add_argument("--hash", default="xxh64", choices=["md5","sha1","sha256","sha512","blake3","xxh64","xxh3","c4"])
    ap.add_argument("--signing-kid", default="dwc-dit-01")
    ap.add_argument("--validate", action="store_true", help="Run validate.py on each produced sidecar")
    args = ap.parse_args()

    base    = args.root.resolve()
    ocf     = base / "1_OCF"
    amf_dir = base / "Colour-Information/AMF"
    cdl_dir = base / "Colour-Information/CDLs/CDL_Output"
    fdls    = list((base / "Colour-Information/FDL").glob("*.fdl")) if (base / "Colour-Information/FDL").exists() else []
    fdl     = fdls[0] if fdls else None

    priv_bundle = json.loads(PRIV_KEYS.read_text())
    priv = Ed25519PrivateKey.from_private_bytes(base64.b64decode(priv_bundle[args.signing_kid]))

    args.out_dir.mkdir(parents=True, exist_ok=True)

    clips = find_clip_files(ocf)
    print(f"Found {len(clips)} clip(s) under {ocf}")
    if not clips:
        return 1

    total_bytes = sum(c.stat().st_size for c, _ in clips)
    print(f"Total size: {total_bytes / 1024**3:.2f} GB\n")

    ingest_start = time.perf_counter()
    per_clip_stats = []
    for i, (clip, roll) in enumerate(clips, 1):
        t0   = time.perf_counter()
        size = clip.stat().st_size
        try:
            doc = build_sidecar(clip, roll, base, amf_dir, cdl_dir, fdl,
                                  args.hash, priv, args.signing_kid)
        except Exception as e:
            print(f"[{i}/{len(clips)}] {clip.name}  FAIL {e}")
            continue
        t1 = time.perf_counter()
        out = args.out_dir / f"{clip.stem}.omc.json"
        out.write_text(json.dumps(doc, indent=2) + "\n")
        dt = t1 - t0
        mbps = (size / 1024**2) / dt if dt else 0
        per_clip_stats.append((clip.name, size, dt))
        print(f"[{i}/{len(clips)}] {clip.name:<34} {size/1024**2:>8.1f} MB  {dt:>5.1f}s  {mbps:>6.0f} MB/s  → {out.name}")
    ingest_total = time.perf_counter() - ingest_start

    print(f"\nIngest: {len(per_clip_stats)} sidecar(s) in {ingest_total:.1f}s "
          f"({(total_bytes/1024**3)/ingest_total:.2f} GB/s aggregate)")

    if args.validate:
        print("\nValidating…")
        import subprocess
        vstart = time.perf_counter()
        fails = 0
        for clip_name, _, _ in per_clip_stats:
            sc = args.out_dir / f"{Path(clip_name).stem}.omc.json"
            r = subprocess.run(
                ["python3", "validate.py", str(sc), "--base-dir", str(base)],
                capture_output=True, text=True)
            ok = r.returncode == 0
            if not ok:
                fails += 1
                tail = r.stdout.strip().splitlines()[-2:]
                print(f"  FAIL {sc.name}: {' | '.join(tail)}")
        vtotal = time.perf_counter() - vstart
        print(f"Validate: {len(per_clip_stats) - fails} OK / {fails} FAIL in {vtotal:.1f}s")

    return 0


if __name__ == "__main__":
    sys.exit(main())
