#!/usr/bin/env python3
"""Two-stage validator for DWC sidecar files.

Stage 1: validate the whole document against MovieLabs OMC v2.8 JSON Schema.
Stage 2: walk every customData entry with a DWC domain and validate its
         'value' payload against the matching DWC extension schema.
"""
import json, sys
from datetime import datetime
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker, validators

from .canonical import (
    verify_event, load_pubkey_b64, file_digest, HASH_ALGS,
)
from .mhl import parse_mhl
from .cdl import parse_cdl, extract_cdl_from_amf, cdl_values_equal

HERE        = Path(__file__).parent
DATA        = HERE / "data"
OMC         = DATA / "OMC" / "OMC-JSON" / "OMC-JSON-v2.8.schema.json"
SCHEMAS     = DATA / "schemas"
# Deployment-specific files resolved against the caller's CWD, not the package dir.
DEFAULT     = Path("example-clip.omc.json")
KEYRING     = Path("keyring.json")
REVOCATIONS = Path("revocations.json")

DWC_SCHEMAS = {
    "dwc.sidecar.artifacts": SCHEMAS / "artifacts.schema.json",
    "dwc.sidecar.events":    SCHEMAS / "events.schema.json",
    "dwc.sidecar.locks":     SCHEMAS / "locks.schema.json",
}

HOSTED_SCHEMA_BASE = "https://ns.the-dwc.com/sidecar/v0.1"


def load(p): return json.loads(Path(p).read_text())


def walk_errors(errs, depth=0):
    rc = 0
    for e in sorted(errs, key=lambda x: list(x.absolute_path)):
        rc += 1
        path = "/".join(str(p) for p in e.absolute_path) or "<root>"
        msg  = e.message if len(e.message) <= 220 else e.message[:220] + " […]"
        print(f"{'  '*depth}at {path}")
        print(f"{'  '*depth}  {msg}")
        if e.context:
            walk_errors(e.context, depth + 1)
    return rc


def find_custom_data(node, trail=()):
    """Yield (jsonPath, customDataArray) for every customData array in the doc."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "customData" and isinstance(v, list):
                yield ("/".join(str(x) for x in trail + (k,)), v)
            yield from find_custom_data(v, trail + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from find_custom_data(v, trail + (i,))


def _x_controlled(_validator, allowed, instance, _schema):
    """Treat OMC's x-controlledValues exactly like enum for string instances."""
    if isinstance(instance, str) and instance not in allowed:
        from jsonschema.exceptions import ValidationError
        yield ValidationError(f"{instance!r} is not in x-controlledValues {list(allowed)[:8]}…")

_OmcValidator      = Draft202012Validator
_OmcStrictValidator = validators.extend(
    Draft202012Validator, {"x-controlledValues": _x_controlled}
)


def validate_omc(doc, strict=False):
    schema = load(OMC)
    Cls = _OmcStrictValidator if strict else _OmcValidator
    v = Cls(schema, format_checker=FormatChecker())
    errs = list(v.iter_errors(doc))
    label = "OMC v2.8 + x-controlledValues" if strict else "OMC v2.8"
    if errs:
        print(f"Stage {'7' if strict else '1'} — {label}: FAIL ({len(errs)} top-level error(s))")
        walk_errors(errs)
        return len(errs)
    print(f"Stage {'7' if strict else '1'} — {label}: OK")
    return 0


def validate_dwc_extensions(doc):
    total, checked = 0, 0
    for path, cd in find_custom_data(doc):
        for i, entry in enumerate(cd):
            if not isinstance(entry, dict):
                continue
            domain = entry.get("domain")
            if not isinstance(domain, str):
                continue
            schema_file = DWC_SCHEMAS.get(domain)
            if not schema_file:
                continue
            checked += 1
            schema = load(schema_file)
            v = Draft202012Validator(schema, format_checker=FormatChecker())
            errs = list(v.iter_errors(entry.get("value")))
            loc = f"{path}[{i}]  domain={domain}"
            if errs:
                print(f"Stage 2 — {loc}: FAIL ({len(errs)} error(s))")
                walk_errors(errs, depth=1)
                total += len(errs)
            else:
                print(f"Stage 2 — {loc}: OK")
    if checked == 0:
        print("Stage 2 — no DWC customData entries found")
    return total


def validate_chain_integrity(doc):
    """Not JSON-Schema-able: ensure events form a contiguous hash-chained sequence."""
    errs = 0
    for path, cd in find_custom_data(doc):
        for i, entry in enumerate(cd):
            if not isinstance(entry, dict) or entry.get("domain") != "dwc.sidecar.events":
                continue
            events = entry.get("value") or []
            prev_hash, prev_seq = None, 0
            for j, ev in enumerate(events):
                seq   = ev.get("seq")
                ph    = ev.get("prevHash")
                where = f"{path}[{i}].value[{j}]"
                if seq != prev_seq + 1:
                    print(f"Stage 3 — {where}: seq {seq} not contiguous after {prev_seq}")
                    errs += 1
                if ph != prev_hash:
                    print(f"Stage 3 — {where}: prevHash mismatch (expected {prev_hash!r}, got {ph!r})")
                    errs += 1
                prev_seq, prev_hash = seq, ev.get("hash")
    if errs == 0:
        print("Stage 3 — chain integrity: OK")
    return errs


def _parse_iso(s):
    if not s: return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def validate_signatures(doc):
    """Stage 4: recompute each event's hash over canonical body, verify Ed25519
    signature, and check the event ts falls inside the signing key's validity window."""
    if not KEYRING.exists():
        print("Stage 4 — no keyring.json found (run sign-example.py to create one)")
        return 0
    keyring = load(KEYRING)["keys"]

    # Accept both legacy flat (kid → base64-string) and rotating/revocable (kid → object) formats
    def expand(entry):
        if isinstance(entry, str):
            return {"publicKey": entry, "validFrom": None, "validUntil": None,
                    "revokedAt": None, "revocationReason": None}
        return {**{"revokedAt": None, "revocationReason": None}, **entry}
    keyring = {kid: expand(v) for kid, v in keyring.items()}

    # Merge CRL-style revocations.json (a separate distributable artefact — overrides keyring)
    if REVOCATIONS.exists():
        crl = load(REVOCATIONS).get("revocations", [])
        for r in crl:
            kid = r.get("kid")
            if kid in keyring:
                keyring[kid]["revokedAt"]        = r.get("revokedAt")
                keyring[kid]["revocationReason"] = r.get("reason")

    pubkeys = {kid: load_pubkey_b64(v["publicKey"]) for kid, v in keyring.items()}

    errs = 0
    checked = 0
    for path, cd in find_custom_data(doc):
        for i, entry in enumerate(cd):
            if not isinstance(entry, dict) or entry.get("domain") != "dwc.sidecar.events":
                continue
            for j, ev in enumerate(entry.get("value") or []):
                kid = (ev.get("sig") or {}).get("kid")
                where = f"{path}[{i}].value[{j}] (seq={ev.get('seq')}, kid={kid})"
                pub = pubkeys.get(kid)
                if pub is None:
                    print(f"Stage 4 — {where}: FAIL — unknown kid {kid!r}")
                    errs += 1
                    continue
                ok, reason = verify_event(ev, pub)
                checked += 1
                if not ok:
                    print(f"Stage 4 — {where}: FAIL — {reason}")
                    errs += 1
                    continue
                # Window + revocation checks
                ts     = _parse_iso(ev.get("ts"))
                vfrom  = _parse_iso(keyring[kid].get("validFrom"))
                vto    = _parse_iso(keyring[kid].get("validUntil"))
                revkd  = _parse_iso(keyring[kid].get("revokedAt"))
                reason = keyring[kid].get("revocationReason")
                if ts and vfrom and ts < vfrom:
                    print(f"Stage 4 — {where}: FAIL — event ts {ts.isoformat()} predates "
                          f"key validFrom {vfrom.isoformat()}")
                    errs += 1
                elif ts and vto and ts > vto:
                    print(f"Stage 4 — {where}: FAIL — event ts {ts.isoformat()} after "
                          f"key validUntil {vto.isoformat()}")
                    errs += 1
                elif ts and revkd and ts > revkd:
                    print(f"Stage 4 — {where}: FAIL — event ts {ts.isoformat()} after "
                          f"key revokedAt {revkd.isoformat()} ({reason!r})")
                    errs += 1
    if errs == 0:
        print(f"Stage 4 — signatures + validity: OK ({checked} event(s) verified)")
    return errs


def _group_by_domain(doc):
    """Return {domain: [values...]} flattening all customData in the doc."""
    out = {}
    for _, cd in find_custom_data(doc):
        for e in cd:
            if isinstance(e, dict) and isinstance(e.get("domain"), str):
                out.setdefault(e["domain"], []).extend(e.get("value") or [])
    return out


def validate_lock_event_crosscheck(doc):
    """Stage 5: every locks[] entry must have a matching signed lock event
    (same target, same 'by' / actor.id, same 'at' / ts)."""
    groups = _group_by_domain(doc)
    locks  = groups.get("dwc.sidecar.locks")  or []
    events = groups.get("dwc.sidecar.events") or []

    errs = 0
    for idx, lk in enumerate(locks):
        matches = [
            ev for ev in events
            if ev.get("action") == "lock"
            and ev.get("target") == lk.get("target")
            and (ev.get("actor") or {}).get("id") == lk.get("by")
            and ev.get("ts") == lk.get("at")
        ]
        if not matches:
            print(f"Stage 5 — locks[{idx}]: FAIL — no matching signed lock event "
                  f"(target={lk.get('target')}, by={lk.get('by')}, at={lk.get('at')})")
            errs += 1
            continue
        # Also require the lock's sig kid to match the event's sig kid
        ev = matches[0]
        lk_kid = (lk.get("sig") or {}).get("kid")
        ev_kid = (ev.get("sig") or {}).get("kid")
        if lk_kid != ev_kid:
            print(f"Stage 5 — locks[{idx}]: FAIL — sig.kid {lk_kid!r} does not match "
                  f"event sig.kid {ev_kid!r}")
            errs += 1
    if errs == 0:
        print(f"Stage 5 — lock↔event crosscheck: OK ({len(locks)} lock(s) paired)")
    return errs


def _mhl_declared_hash_for_path(doc, base_dir, clip_path_str):
    """If any MHL artifact in the doc declares a hash for the same file as `clip_path_str`,
    return (alg, value). Otherwise None."""
    groups    = _group_by_domain(doc)
    artifacts = groups.get("dwc.sidecar.artifacts") or []
    for m in artifacts:
        if m.get("kind") != "asc-mhl":
            continue
        mhl_path  = base_dir / m.get("path", "")
        mhl_entry = m.get("mhlEntry")
        if not (mhl_path.exists() and mhl_entry):
            continue
        # Does the clip path end with the MHL entry path? (mhlEntry is relative-to-MHL)
        if not clip_path_str.endswith(mhl_entry):
            continue
        try:
            parsed = parse_mhl(mhl_path)
        except Exception:
            continue
        for e in parsed.get("Hashes") or []:
            if e.get("File") == mhl_entry:
                for alg in HASH_ALGS:
                    if alg in e and e[alg]:
                        return alg, str(e[alg])
    return None


def validate_artifact_files(doc, base_dir, trust_mhl=False):
    """Stage 6: resolve each artifact.path relative to base_dir, read the file,
    hash it with the declared alg, and compare to the declared value.

    If trust_mhl=True, skip re-reading any artifact whose declared hash matches
    what an MHL in the same doc independently declares (Stage 8 will check the
    MHL's claim against the bytes instead — no information loss, one pass saved)."""
    groups    = _group_by_domain(doc)
    artifacts = groups.get("dwc.sidecar.artifacts") or []

    errs = 0
    checked = 0
    skipped = 0
    for idx, a in enumerate(artifacts):
        path = base_dir / a.get("path", "")
        h    = a.get("hash") or {}
        alg  = h.get("alg")
        want = h.get("value")
        where = f"artifacts[{idx}] kind={a.get('kind')} path={a.get('path')}"

        if not path.exists():
            print(f"Stage 6 — {where}: FAIL — file not found")
            errs += 1
            continue
        if not isinstance(alg, str) or not isinstance(want, str):
            print(f"Stage 6 — {where}: FAIL — missing or malformed hash block")
            errs += 1
            continue
        if alg not in HASH_ALGS:
            print(f"Stage 6 — {where}: SKIP — unsupported alg {alg!r}")
            continue

        if trust_mhl:
            mhl_hash = _mhl_declared_hash_for_path(doc, base_dir, a.get("path", ""))
            if mhl_hash and mhl_hash == (alg, want):
                skipped += 1
                continue  # Stage 8 will verify the MHL's claim against the bytes

        got = file_digest(path, alg)
        checked += 1
        if got != want:
            print(f"Stage 6 — {where}: FAIL — {alg} mismatch "
                  f"(declared {want[:16]}…, actual {got[:16]}…)")
            errs += 1
    if errs == 0:
        note = f" ({skipped} delegated to Stage 8 via --trust-mhl)" if skipped else ""
        print(f"Stage 6 — artifact file integrity: OK ({checked} file(s) hashed){note}")
    return errs


def validate_mhl_inner(doc, base_dir):
    """Stage 8: for each artifact with kind=asc-mhl, parse the MHL (v2 YAML),
    find the hash entry for mhlEntry, re-hash the referenced camera file,
    compare to the MHL's own declared hash."""
    groups    = _group_by_domain(doc)
    artifacts = groups.get("dwc.sidecar.artifacts") or []

    errs = 0
    checked = 0
    for idx, a in enumerate(artifacts):
        if a.get("kind") != "asc-mhl":
            continue
        mhl_path  = base_dir / a.get("path", "")
        mhl_entry = a.get("mhlEntry")
        where = f"artifacts[{idx}] MHL path={a.get('path')} entry={mhl_entry}"
        if not mhl_path.exists():
            print(f"Stage 8 — {where}: SKIP — MHL not present")
            continue
        try:
            mhl = parse_mhl(mhl_path)
        except Exception as e:
            print(f"Stage 8 — {where}: FAIL — MHL not parseable: {e}")
            errs += 1
            continue
        hashes = (mhl or {}).get("Hashes") or []
        entry  = next((h for h in hashes if h.get("File") == mhl_entry), None)
        if entry is None:
            print(f"Stage 8 — {where}: FAIL — no Hashes entry for {mhl_entry!r}")
            errs += 1
            continue
        # pick first alg present
        alg = next((k for k in HASH_ALGS if k in entry), None)
        if alg is None:
            print(f"Stage 8 — {where}: FAIL — MHL entry uses no supported alg")
            errs += 1
            continue
        declared = entry[alg]
        camera_file = mhl_path.parent / entry["File"]
        # MHL typically stores File as relative to MHL; if absent on disk, warn but don't fail
        if not camera_file.exists():
            # fall back: try sidecar's base_dir
            alt = base_dir / entry["File"]
            if alt.exists():
                camera_file = alt
            else:
                print(f"Stage 8 — {where}: SKIP — camera file {entry['File']!r} not present; "
                      f"MHL entry declares {alg}={declared[:16]}…")
                continue
        got = file_digest(camera_file, alg)
        checked += 1
        if got != declared:
            print(f"Stage 8 — {where}: FAIL — {alg} mismatch for camera file "
                  f"(MHL says {declared[:16]}…, actual {got[:16]}…)")
            errs += 1
    if errs == 0:
        print(f"Stage 8 — MHL inner consistency: OK ({checked} file(s) re-hashed against MHL)")
    return errs


def validate_cdl_consistency(doc, base_dir):
    """Stage 9: for each (standalone CDL, AMF) pair in the sidecar, compare the
    standalone CDL's SOP/Sat against each lookTransform in the AMF. Warnings
    only — divergence is an informational finding, not a validation failure,
    since AMFs often carry independent reference-only grades."""
    groups    = _group_by_domain(doc)
    artifacts = groups.get("dwc.sidecar.artifacts") or []
    cdl_arts  = [a for a in artifacts if a.get("kind") == "cdl"]
    amf_arts  = [a for a in artifacts if a.get("kind") == "amf"]

    if not cdl_arts:
        print("Stage 9 — CDL consistency: SKIP (no CDL artifact in sidecar)")
        return 0
    if not amf_arts:
        print("Stage 9 — CDL consistency: SKIP (CDL present but no AMF to compare)")
        return 0

    warns   = 0
    pairs   = 0
    matches = 0
    for cdl_art in cdl_arts:
        cdl_path = base_dir / cdl_art.get("path", "")
        if not cdl_path.exists():
            print(f"Stage 9 — cdl {cdl_art.get('path')}: SKIP file not present"); continue
        try:
            cdl_vals = parse_cdl(cdl_path)
        except Exception as e:
            print(f"Stage 9 — cdl {cdl_path.name}: WARN parse error: {e}"); warns += 1; continue

        for amf_art in amf_arts:
            amf_path = base_dir / amf_art.get("path", "")
            if not amf_path.exists(): continue
            try:
                amf_looks = extract_cdl_from_amf(amf_path)
            except Exception as e:
                print(f"Stage 9 — amf {amf_path.name}: WARN parse error: {e}"); warns += 1; continue
            if not amf_looks: continue

            pairs += 1
            paired_matches = [look for look in amf_looks if cdl_values_equal(cdl_vals, look)]
            if paired_matches:
                matches += 1
                continue

            # Surface the divergence
            def _fmt(v): return f"({v[0]:.3f}, {v[1]:.3f}, {v[2]:.3f})"
            for i, look in enumerate(amf_looks):
                desc  = look.get("description") or "?"
                appl  = "applied" if look["applied"] else "reference-only"
                print(f"Stage 9 — WARN {cdl_path.stem}: standalone CDL ≠ AMF look[{i}] '{desc}' ({appl})")
                print(f"            CDL    slope={_fmt(cdl_vals['slope'])} offset={_fmt(cdl_vals['offset'])} "
                      f"power={_fmt(cdl_vals['power'])} sat={cdl_vals['saturation']:.3f}")
                print(f"            AMF    slope={_fmt(look['slope'])} offset={_fmt(look['offset'])} "
                      f"power={_fmt(look['power'])} sat={look['saturation']:.3f}")
            warns += 1

    if warns == 0:
        print(f"Stage 9 — CDL consistency: OK ({matches}/{pairs} pair(s) match)")
    else:
        print(f"Stage 9 — CDL consistency: {warns} WARN(s), {matches}/{pairs} pair(s) match "
              "(warnings do not fail validation)")
    return 0  # warnings only — never contributes to rc


def check_hosted_schemas():
    """Stage 2.5 (opt-in): byte-compare each local schema against its hosted copy
    at HOSTED_SCHEMA_BASE. Any divergence is a drift error — the published schema
    is the canonical, immutable form and local must match."""
    import hashlib, subprocess

    print(f"Stage 2.5 — hosted-schema drift ({HOSTED_SCHEMA_BASE})")
    errs = 0
    for path in DWC_SCHEMAS.values():
        name    = path.name
        url     = f"{HOSTED_SCHEMA_BASE}/{name}"
        local   = path.read_bytes()
        lh      = hashlib.sha256(local).hexdigest()
        try:
            r = subprocess.run(
                ["curl", "-sfS", "--max-time", "15", url],
                capture_output=True, check=True,
            )
        except subprocess.CalledProcessError as e:
            msg = e.stderr.decode(errors="replace").strip() or f"exit {e.returncode}"
            print(f"  {name:30s} FETCH FAIL ({msg})")
            errs += 1
            continue
        except FileNotFoundError:
            print("  curl not available on this system — --check-hosted unavailable")
            return 1
        rh = hashlib.sha256(r.stdout).hexdigest()
        if lh == rh:
            print(f"  {name:30s} OK  ({lh[:12]})")
        else:
            print(f"  {name:30s} DRIFT  local={lh[:12]} hosted={rh[:12]}")
            errs += 1
    return errs


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("target",  nargs="?", default=str(DEFAULT), type=Path)
    ap.add_argument("--base-dir", type=Path,
                     help="Directory used to resolve relative artifact paths. "
                          "Default: the sidecar's own directory.")
    ap.add_argument("--trust-mhl", action="store_true",
                     help="Skip Stage 6 re-read of any artifact whose declared hash "
                          "matches what an MHL in the same sidecar declares for the "
                          "same file. Stage 8 will verify the MHL's claim against the "
                          "bytes — no information loss, but one I/O pass is saved.")
    ap.add_argument("--check-hosted", action="store_true",
                     help="Additionally byte-compare each local schema against its "
                          "hosted copy at " + HOSTED_SCHEMA_BASE + ". Off by default "
                          "so validation stays offline-safe; used in CI.")
    args = ap.parse_args(argv[1:])
    target = args.target.resolve()
    base   = (args.base_dir or target.parent).resolve()
    print(f"→ {target}\n  base-dir: {base}\n")
    doc = load(target)

    rc = 0
    rc += validate_omc(doc)
    print()
    rc += validate_dwc_extensions(doc)
    print()
    if args.check_hosted:
        rc += check_hosted_schemas()
        print()
    rc += validate_chain_integrity(doc)
    print()
    rc += validate_signatures(doc)
    print()
    rc += validate_lock_event_crosscheck(doc)
    print()
    rc += validate_artifact_files(doc, base, trust_mhl=args.trust_mhl)
    print()
    rc += validate_omc(doc, strict=True)
    print()
    rc += validate_mhl_inner(doc, base)
    print()
    rc += validate_cdl_consistency(doc, base)
    print()
    print("SUMMARY:", "OK" if rc == 0 else f"FAIL ({rc} error(s))")
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
