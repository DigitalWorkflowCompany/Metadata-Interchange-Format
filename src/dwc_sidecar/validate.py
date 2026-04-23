#!/usr/bin/env python3
"""Nine-stage validator for DWC sidecar files.

Stage functions no longer print directly; each returns a structured result
dict ``{stage, title, status, errors, warnings, lines}`` that ``main()``
formats to stdout and ``validate_as_json()`` assembles into a JSON-friendly
report consumed by ``dwc doctor`` and the Pyodide web validator. The CLI's
stdout contract is unchanged: subprocess callers in ``watch.py``,
``mhl_walker.py``, and ``batch.py`` keep grabbing ``stdout.splitlines()[-2:]``.
"""
import json, sys
from datetime import datetime
from pathlib import Path
from typing import Iterable
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


def _result(stage, title, *, status, errors=0, warnings=0, lines):
    return {
        "stage": stage,
        "title": title,
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "lines": list(lines),
    }


def _collect_schema_errors(errs, depth=0) -> list[str]:
    out: list[str] = []
    for e in sorted(errs, key=lambda x: list(x.absolute_path)):
        path = "/".join(str(p) for p in e.absolute_path) or "<root>"
        msg  = e.message if len(e.message) <= 220 else e.message[:220] + " […]"
        out.append(f"{'  '*depth}at {path}")
        out.append(f"{'  '*depth}  {msg}")
        if e.context:
            out.extend(_collect_schema_errors(e.context, depth + 1))
    return out


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


def validate_omc(doc, strict=False) -> dict:
    schema = load(OMC)
    Cls = _OmcStrictValidator if strict else _OmcValidator
    v = Cls(schema, format_checker=FormatChecker())
    errs = list(v.iter_errors(doc))
    stage = "7" if strict else "1"
    label = "OMC v2.8 + x-controlledValues" if strict else "OMC v2.8"
    lines: list[str] = []
    if errs:
        lines.append(f"Stage {stage} — {label}: FAIL ({len(errs)} top-level error(s))")
        lines.extend(_collect_schema_errors(errs))
        return _result(stage, label, status="fail", errors=len(errs), lines=lines)
    lines.append(f"Stage {stage} — {label}: OK")
    return _result(stage, label, status="pass", lines=lines)


def validate_dwc_extensions(doc) -> dict:
    total, checked = 0, 0
    lines: list[str] = []
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
                lines.append(f"Stage 2 — {loc}: FAIL ({len(errs)} error(s))")
                lines.extend(_collect_schema_errors(errs, depth=1))
                total += len(errs)
            else:
                lines.append(f"Stage 2 — {loc}: OK")
    if checked == 0:
        lines.append("Stage 2 — no DWC customData entries found")
    status = "fail" if total else "pass"
    return _result("2", "DWC payload schemas", status=status, errors=total, lines=lines)


def validate_chain_integrity(doc) -> dict:
    """Not JSON-Schema-able: ensure events form a contiguous hash-chained sequence."""
    errs = 0
    lines: list[str] = []
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
                    lines.append(f"Stage 3 — {where}: seq {seq} not contiguous after {prev_seq}")
                    errs += 1
                if ph != prev_hash:
                    lines.append(f"Stage 3 — {where}: prevHash mismatch (expected {prev_hash!r}, got {ph!r})")
                    errs += 1
                prev_seq, prev_hash = seq, ev.get("hash")
    if errs == 0:
        lines.append("Stage 3 — chain integrity: OK")
    status = "fail" if errs else "pass"
    return _result("3", "Event chain continuity", status=status, errors=errs, lines=lines)


def _parse_iso(s):
    if not s: return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def validate_signatures(doc, keyring_path: Path = KEYRING,
                        revocations_path: Path = REVOCATIONS) -> dict:
    """Stage 4: recompute each event's hash over canonical body, verify Ed25519
    signature, and check the event ts falls inside the signing key's validity window."""
    lines: list[str] = []
    if not keyring_path.exists():
        lines.append("Stage 4 — no keyring.json found (run sign-example.py to create one)")
        return _result("4", "Ed25519 signatures + key validity",
                       status="pass", errors=0, lines=lines)
    keyring = load(keyring_path)["keys"]

    # Accept both legacy flat (kid → base64-string) and rotating/revocable (kid → object) formats
    def expand(entry):
        if isinstance(entry, str):
            return {"publicKey": entry, "validFrom": None, "validUntil": None,
                    "revokedAt": None, "revocationReason": None}
        return {**{"revokedAt": None, "revocationReason": None}, **entry}
    keyring = {kid: expand(v) for kid, v in keyring.items()}

    # Merge CRL-style revocations.json (a separate distributable artefact — overrides keyring)
    if revocations_path.exists():
        crl = load(revocations_path).get("revocations", [])
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
                    lines.append(f"Stage 4 — {where}: FAIL — unknown kid {kid!r}")
                    errs += 1
                    continue
                ok, reason = verify_event(ev, pub)
                checked += 1
                if not ok:
                    lines.append(f"Stage 4 — {where}: FAIL — {reason}")
                    errs += 1
                    continue
                # Window + revocation checks
                ts     = _parse_iso(ev.get("ts"))
                vfrom  = _parse_iso(keyring[kid].get("validFrom"))
                vto    = _parse_iso(keyring[kid].get("validUntil"))
                revkd  = _parse_iso(keyring[kid].get("revokedAt"))
                reason = keyring[kid].get("revocationReason")
                if ts and vfrom and ts < vfrom:
                    lines.append(f"Stage 4 — {where}: FAIL — event ts {ts.isoformat()} predates "
                                 f"key validFrom {vfrom.isoformat()}")
                    errs += 1
                elif ts and vto and ts > vto:
                    lines.append(f"Stage 4 — {where}: FAIL — event ts {ts.isoformat()} after "
                                 f"key validUntil {vto.isoformat()}")
                    errs += 1
                elif ts and revkd and ts > revkd:
                    lines.append(f"Stage 4 — {where}: FAIL — event ts {ts.isoformat()} after "
                                 f"key revokedAt {revkd.isoformat()} ({reason!r})")
                    errs += 1
    if errs == 0:
        lines.append(f"Stage 4 — signatures + validity: OK ({checked} event(s) verified)")
    status = "fail" if errs else "pass"
    return _result("4", "Ed25519 signatures + key validity",
                   status=status, errors=errs, lines=lines)


def _group_by_domain(doc):
    """Return {domain: [values...]} flattening all customData in the doc."""
    out = {}
    for _, cd in find_custom_data(doc):
        for e in cd:
            if isinstance(e, dict) and isinstance(e.get("domain"), str):
                out.setdefault(e["domain"], []).extend(e.get("value") or [])
    return out


def validate_lock_event_crosscheck(doc) -> dict:
    """Stage 5: every locks[] entry must have a matching signed lock event
    (same target, same 'by' / actor.id, same 'at' / ts)."""
    groups = _group_by_domain(doc)
    locks  = groups.get("dwc.sidecar.locks")  or []
    events = groups.get("dwc.sidecar.events") or []

    errs = 0
    lines: list[str] = []
    for idx, lk in enumerate(locks):
        matches = [
            ev for ev in events
            if ev.get("action") == "lock"
            and ev.get("target") == lk.get("target")
            and (ev.get("actor") or {}).get("id") == lk.get("by")
            and ev.get("ts") == lk.get("at")
        ]
        if not matches:
            lines.append(f"Stage 5 — locks[{idx}]: FAIL — no matching signed lock event "
                         f"(target={lk.get('target')}, by={lk.get('by')}, at={lk.get('at')})")
            errs += 1
            continue
        # Also require the lock's sig kid to match the event's sig kid
        ev = matches[0]
        lk_kid = (lk.get("sig") or {}).get("kid")
        ev_kid = (ev.get("sig") or {}).get("kid")
        if lk_kid != ev_kid:
            lines.append(f"Stage 5 — locks[{idx}]: FAIL — sig.kid {lk_kid!r} does not match "
                         f"event sig.kid {ev_kid!r}")
            errs += 1
    if errs == 0:
        lines.append(f"Stage 5 — lock↔event crosscheck: OK ({len(locks)} lock(s) paired)")
    status = "fail" if errs else "pass"
    return _result("5", "Lock ↔ signed event crosscheck",
                   status=status, errors=errs, lines=lines)


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


def validate_artifact_files(doc, base_dir, trust_mhl=False, *,
                             missing_is_skip: bool = False) -> dict:
    """Stage 6: resolve each artifact.path relative to base_dir, read the file,
    hash it with the declared alg, and compare to the declared value.

    ``missing_is_skip`` is set by the web validator (plan §4.4a) because a user
    dropping a sidecar zip may legitimately omit the 30GB camera original;
    that's not a FAIL, it's just outside-scope for in-browser verification.
    CLI callers keep the default (FAIL on missing file)."""
    groups    = _group_by_domain(doc)
    artifacts = groups.get("dwc.sidecar.artifacts") or []

    errs = 0
    checked = 0
    skipped = 0
    lines: list[str] = []
    for idx, a in enumerate(artifacts):
        path = base_dir / a.get("path", "")
        h    = a.get("hash") or {}
        alg  = h.get("alg")
        want = h.get("value")
        where = f"artifacts[{idx}] kind={a.get('kind')} path={a.get('path')}"

        if not path.exists():
            if missing_is_skip:
                lines.append(f"Stage 6 — {where}: SKIP — file not provided in zip")
                skipped += 1
                continue
            lines.append(f"Stage 6 — {where}: FAIL — file not found")
            errs += 1
            continue
        if not isinstance(alg, str) or not isinstance(want, str):
            lines.append(f"Stage 6 — {where}: FAIL — missing or malformed hash block")
            errs += 1
            continue
        if alg not in HASH_ALGS:
            lines.append(f"Stage 6 — {where}: SKIP — unsupported alg {alg!r}")
            continue

        if trust_mhl:
            mhl_hash = _mhl_declared_hash_for_path(doc, base_dir, a.get("path", ""))
            if mhl_hash and mhl_hash == (alg, want):
                skipped += 1
                continue  # Stage 8 will verify the MHL's claim against the bytes

        got = file_digest(path, alg)
        checked += 1
        if got != want:
            lines.append(f"Stage 6 — {where}: FAIL — {alg} mismatch "
                         f"(declared {want[:16]}…, actual {got[:16]}…)")
            errs += 1
    if errs == 0:
        note = f" ({skipped} delegated to Stage 8 via --trust-mhl)" if skipped else ""
        lines.append(f"Stage 6 — artifact file integrity: OK ({checked} file(s) hashed){note}")
    status = "fail" if errs else "pass"
    return _result("6", "Artifact file integrity",
                   status=status, errors=errs, lines=lines)


def validate_mhl_inner(doc, base_dir) -> dict:
    """Stage 8: for each artifact with kind=asc-mhl, parse the MHL (v2 YAML),
    find the hash entry for mhlEntry, re-hash the referenced camera file,
    compare to the MHL's own declared hash."""
    groups    = _group_by_domain(doc)
    artifacts = groups.get("dwc.sidecar.artifacts") or []

    errs = 0
    checked = 0
    lines: list[str] = []
    for idx, a in enumerate(artifacts):
        if a.get("kind") != "asc-mhl":
            continue
        mhl_path  = base_dir / a.get("path", "")
        mhl_entry = a.get("mhlEntry")
        where = f"artifacts[{idx}] MHL path={a.get('path')} entry={mhl_entry}"
        if not mhl_path.exists():
            lines.append(f"Stage 8 — {where}: SKIP — MHL not present")
            continue
        try:
            mhl = parse_mhl(mhl_path)
        except Exception as e:
            lines.append(f"Stage 8 — {where}: FAIL — MHL not parseable: {e}")
            errs += 1
            continue
        hashes = (mhl or {}).get("Hashes") or []
        entry  = next((h for h in hashes if h.get("File") == mhl_entry), None)
        if entry is None:
            lines.append(f"Stage 8 — {where}: FAIL — no Hashes entry for {mhl_entry!r}")
            errs += 1
            continue
        alg = next((k for k in HASH_ALGS if k in entry), None)
        if alg is None:
            lines.append(f"Stage 8 — {where}: FAIL — MHL entry uses no supported alg")
            errs += 1
            continue
        declared = entry[alg]
        camera_file = mhl_path.parent / entry["File"]
        if not camera_file.exists():
            alt = base_dir / entry["File"]
            if alt.exists():
                camera_file = alt
            else:
                lines.append(f"Stage 8 — {where}: SKIP — camera file {entry['File']!r} not present; "
                             f"MHL entry declares {alg}={declared[:16]}…")
                continue
        got = file_digest(camera_file, alg)
        checked += 1
        if got != declared:
            lines.append(f"Stage 8 — {where}: FAIL — {alg} mismatch for camera file "
                         f"(MHL says {declared[:16]}…, actual {got[:16]}…)")
            errs += 1
    if errs == 0:
        lines.append(f"Stage 8 — MHL inner consistency: OK ({checked} file(s) re-hashed against MHL)")
    status = "fail" if errs else "pass"
    return _result("8", "MHL inner consistency",
                   status=status, errors=errs, lines=lines)


def validate_cdl_consistency(doc, base_dir) -> dict:
    """Stage 9: warning-only comparison of standalone CDL vs AMF lookTransforms."""
    groups    = _group_by_domain(doc)
    artifacts = groups.get("dwc.sidecar.artifacts") or []
    cdl_arts  = [a for a in artifacts if a.get("kind") == "cdl"]
    amf_arts  = [a for a in artifacts if a.get("kind") == "amf"]

    lines: list[str] = []
    if not cdl_arts:
        lines.append("Stage 9 — CDL consistency: SKIP (no CDL artifact in sidecar)")
        return _result("9", "CDL consistency", status="pass", errors=0, lines=lines)
    if not amf_arts:
        lines.append("Stage 9 — CDL consistency: SKIP (CDL present but no AMF to compare)")
        return _result("9", "CDL consistency", status="pass", errors=0, lines=lines)

    warns   = 0
    pairs   = 0
    matches = 0
    for cdl_art in cdl_arts:
        cdl_path = base_dir / cdl_art.get("path", "")
        if not cdl_path.exists():
            lines.append(f"Stage 9 — cdl {cdl_art.get('path')}: SKIP file not present"); continue
        try:
            cdl_vals = parse_cdl(cdl_path)
        except Exception as e:
            lines.append(f"Stage 9 — cdl {cdl_path.name}: WARN parse error: {e}"); warns += 1; continue

        for amf_art in amf_arts:
            amf_path = base_dir / amf_art.get("path", "")
            if not amf_path.exists(): continue
            try:
                amf_looks = extract_cdl_from_amf(amf_path)
            except Exception as e:
                lines.append(f"Stage 9 — amf {amf_path.name}: WARN parse error: {e}"); warns += 1; continue
            if not amf_looks: continue

            pairs += 1
            paired_matches = [look for look in amf_looks if cdl_values_equal(cdl_vals, look)]
            if paired_matches:
                matches += 1
                continue

            def _fmt(v): return f"({v[0]:.3f}, {v[1]:.3f}, {v[2]:.3f})"
            for i, look in enumerate(amf_looks):
                desc  = look.get("description") or "?"
                appl  = "applied" if look["applied"] else "reference-only"
                lines.append(f"Stage 9 — WARN {cdl_path.stem}: standalone CDL ≠ AMF look[{i}] '{desc}' ({appl})")
                lines.append(f"            CDL    slope={_fmt(cdl_vals['slope'])} offset={_fmt(cdl_vals['offset'])} "
                             f"power={_fmt(cdl_vals['power'])} sat={cdl_vals['saturation']:.3f}")
                lines.append(f"            AMF    slope={_fmt(look['slope'])} offset={_fmt(look['offset'])} "
                             f"power={_fmt(look['power'])} sat={look['saturation']:.3f}")
            warns += 1

    if warns == 0:
        lines.append(f"Stage 9 — CDL consistency: OK ({matches}/{pairs} pair(s) match)")
    else:
        lines.append(f"Stage 9 — CDL consistency: {warns} WARN(s), {matches}/{pairs} pair(s) match "
                     "(warnings do not fail validation)")
    status = "warn" if warns else "pass"
    return _result("9", "CDL consistency",
                   status=status, errors=0, warnings=warns, lines=lines)


def check_hosted_schemas() -> dict:
    """Stage 2.5 (opt-in): byte-compare each local schema against its hosted copy
    at HOSTED_SCHEMA_BASE. Any divergence is a drift error — the published schema
    is the canonical, immutable form and local must match."""
    import hashlib, subprocess

    lines: list[str] = [f"Stage 2.5 — hosted-schema drift ({HOSTED_SCHEMA_BASE})"]
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
            lines.append(f"  {name:30s} FETCH FAIL ({msg})")
            errs += 1
            continue
        except FileNotFoundError:
            lines.append("  curl not available on this system — --check-hosted unavailable")
            return _result("2.5", "Hosted schema drift",
                           status="fail", errors=1, lines=lines)
        rh = hashlib.sha256(r.stdout).hexdigest()
        if lh == rh:
            lines.append(f"  {name:30s} OK  ({lh[:12]})")
        else:
            lines.append(f"  {name:30s} DRIFT  local={lh[:12]} hosted={rh[:12]}")
            errs += 1
    status = "fail" if errs else "pass"
    return _result("2.5", "Hosted schema drift",
                   status=status, errors=errs, lines=lines)


def _run_stages(doc, base_dir: Path, *, trust_mhl: bool, check_hosted: bool,
                keyring_path: Path | None = None,
                revocations_path: Path | None = None,
                missing_is_skip: bool = False) -> list[dict]:
    """Run all stages in the canonical order and return their results. Shared
    by main() (which prints) and validate_as_json() (which returns a dict)."""
    results = [
        validate_omc(doc),
        validate_dwc_extensions(doc),
    ]
    if check_hosted:
        results.append(check_hosted_schemas())
    results.extend([
        validate_chain_integrity(doc),
        validate_signatures(
            doc,
            keyring_path    = keyring_path    if keyring_path    is not None else KEYRING,
            revocations_path= revocations_path if revocations_path is not None else REVOCATIONS,
        ),
        validate_lock_event_crosscheck(doc),
        validate_artifact_files(doc, base_dir, trust_mhl=trust_mhl,
                                missing_is_skip=missing_is_skip),
        validate_omc(doc, strict=True),
        validate_mhl_inner(doc, base_dir),
        validate_cdl_consistency(doc, base_dir),
    ])
    return results


def validate_as_json(sidecar_path: Path, base_dir: Path | None = None, *,
                     trust_mhl: bool = False, check_hosted: bool = False,
                     keyring_path: Path | None = None,
                     revocations_path: Path | None = None,
                     missing_is_skip: bool = False) -> dict:
    """Run the 9-stage validator and return a structured report. No stdout,
    no os.chdir — safe to call from long-lived processes and from Pyodide
    where CWD is a shared resource across async calls.

    base_dir defaults to the sidecar's own directory. Used to resolve relative
    artifact paths; pass an explicit value when sidecar paths don't match the
    local filesystem (e.g. production paths inside a zip extracted to /work/).

    keyring_path defaults to CWD-relative ``keyring.json`` (preserving CLI
    behaviour). The web validator passes an explicit path so Stage 4 resolves
    the keyring inside the dropped bundle rather than against process CWD."""
    target = Path(sidecar_path).resolve()
    base   = Path(base_dir).resolve() if base_dir is not None else target.parent
    doc    = load(target)
    results = _run_stages(doc, base, trust_mhl=trust_mhl, check_hosted=check_hosted,
                          keyring_path=keyring_path, revocations_path=revocations_path,
                          missing_is_skip=missing_is_skip)
    errors  = sum(r["errors"] for r in results)
    return {
        "target": str(target),
        "base_dir": str(base),
        "stages": results,
        "errors": errors,
        "summary": "OK" if errors == 0 else f"FAIL ({errors} error(s))",
    }


def _print_results(results: Iterable[dict]) -> int:
    rc = 0
    for r in results:
        for line in r["lines"]:
            print(line)
        print()
        rc += r["errors"]
    return rc


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
    results = _run_stages(doc, base, trust_mhl=args.trust_mhl, check_hosted=args.check_hosted)
    rc = _print_results(results)
    print("SUMMARY:", "OK" if rc == 0 else f"FAIL ({rc} error(s))")
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
