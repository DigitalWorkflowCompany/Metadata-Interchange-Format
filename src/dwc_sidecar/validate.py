#!/usr/bin/env python3
"""Ten-stage validator for DWC sidecar files (1, 2, [2.5], 3, 3.5, 4–9).

Stage functions no longer print directly; each returns a structured result
dict ``{stage, title, status, errors, warnings, lines}`` that ``main()``
formats to stdout and ``validate_as_json()`` assembles into a JSON-friendly
report consumed by ``dwc doctor`` and the Pyodide web validator. The CLI's
stdout contract is unchanged: subprocess callers in ``watch.py``,
``mhl_walker.py``, and ``batch.py`` keep grabbing ``stdout.splitlines()[-2:]``.
"""
import json, re, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from jsonschema import Draft202012Validator, FormatChecker, validators

from .canonical import (
    verify_event, canonical_bytes, load_pubkey_b64, file_digest, HASH_ALGS,
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
    "dwc.sidecar.head":      SCHEMAS / "head.schema.json",
}

HOSTED_SCHEMA_BASE = "https://ns.the-dwc.com/sidecar"

# Hash algs with no adversarial-collision hardness. Fine for bit-rot detection,
# wrong primitive for the "this camera original wasn't substituted" claim —
# Stage 6 warns when an integrity-role artifact declares one.
WEAK_HASH_ALGS  = {"md5", "sha1", "xxh64", "xxh3"}
INTEGRITY_ROLES = {"clip-integrity", "integrity"}


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


def _safe_path(base_dir: Path, rel, anchor: Path | None = None) -> Path | None:
    """Resolve `rel` against `anchor` (default: base_dir) and refuse any result
    outside base_dir. Artifact paths and MHL entries are attacker-controlled in
    the untrusted-sidecar threat model; without containment they form an
    arbitrary-file read / hash-prefix oracle (`../../etc/shadow`)."""
    if not isinstance(rel, str) or not rel:
        return None
    base = base_dir.resolve()
    try:
        candidate = ((anchor or base_dir) / rel).resolve()
    except (OSError, ValueError):
        return None
    if not (candidate == base or candidate.is_relative_to(base)):
        return None
    return candidate


def _iter_assets(node):
    """Yield every dict with entityType == 'Asset' anywhere in the doc
    (including Assets nested inside asset groups)."""
    if isinstance(node, dict):
        if node.get("entityType") == "Asset":
            yield node
        for v in node.values():
            yield from _iter_assets(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_assets(v)


def _asset_payloads(asset) -> dict:
    """Return {domain: customData entry} for the asset's *own* payloads.
    Per-asset isolation matters: flattening locks/events across assets lets a
    lock on asset A be satisfied by an event on asset B."""
    cd = (((asset.get("assetFC") or {}).get("functionalProperties") or {})
          .get("customData")) or []
    out: dict = {}
    for e in cd:
        if isinstance(e, dict) and isinstance(e.get("domain"), str):
            out[e["domain"]] = e
    return out


def _asset_label(asset) -> str:
    for i in asset.get("identifier") or []:
        if isinstance(i, dict) and i.get("identifierValue"):
            return str(i["identifierValue"])
    return str(asset.get("name") or "<unnamed asset>")


def _payload_version(entry) -> str | None:
    """Sidecar format version ('v0.1', 'v0.2', …) from a customData entry's
    namespace/schema URL. None when the entry doesn't declare one."""
    for field in ("namespace", "schema"):
        v = entry.get(field)
        if isinstance(v, str):
            m = re.search(r"/sidecar/(v\d+\.\d+)", v)
            if m:
                return m.group(1)
    return None


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
                if not isinstance(ev, dict):
                    lines.append(f"Stage 3 — {path}[{i}].value[{j}]: event is not an object")
                    errs += 1
                    continue
                seq   = ev.get("seq")
                ph    = ev.get("prevHash")
                where = f"{path}[{i}].value[{j}]"
                # Guard: schema-invalid input reaches this stage because stages
                # are independent — a null/absent seq must be an error, not a crash.
                if not isinstance(seq, int):
                    lines.append(f"Stage 3 — {where}: seq {seq!r} is not an integer")
                    errs += 1
                elif seq != prev_seq + 1:
                    lines.append(f"Stage 3 — {where}: seq {seq} not contiguous after {prev_seq}")
                    errs += 1
                if ph != prev_hash:
                    lines.append(f"Stage 3 — {where}: prevHash mismatch (expected {prev_hash!r}, got {ph!r})")
                    errs += 1
                if isinstance(seq, int):
                    prev_seq = seq
                prev_hash = ev.get("hash")
    if errs == 0:
        lines.append("Stage 3 — chain integrity: OK")
    status = "fail" if errs else "pass"
    return _result("3", "Event chain continuity", status=status, errors=errs, lines=lines)


def _parse_iso(s):
    """Parse an ISO-8601 timestamp; treat naive values as UTC. A schema-valid
    ts without an offset must not crash aware-vs-naive comparisons — a
    malicious sidecar could otherwise *abort* validation instead of failing it."""
    if not s: return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _ts_equal(a, b) -> bool:
    """Timestamp equality normalising 'Z' vs '+00:00' (and any offset spelling)."""
    da, db = _parse_iso(a), _parse_iso(b)
    if da is not None and db is not None:
        return da == db
    return a == b


def validate_binding(doc) -> dict:
    """Stage 3.5: bind the signed narrative to the asset and its integrity claims.

    Three structural checks, per asset:
      (a) every event.target resolves inside this asset (an identifier URN or
          one of its artifact ids) — closes cross-sidecar event replay;
      (b) the artifact hashes committed inside signed event bodies match the
          declared dwc.sidecar.artifacts block — and for v0.2 payloads every
          artifact must be committed by at least one signed event, so the
          signatures cover the integrity claims, not just the narrative;
      (c) the chain-head anchor (dwc.sidecar.head) commits to the chain's
          length and tip — truncating the log to a valid prefix is detectable.
    v0.1 payloads predate commitments and heads: (b)/(c) are noted, not failed.
    Cryptographic verification of head/lock signatures happens in Stages 4/5.

    (d) two-party transfer: an 'accept' must commit by hash to an earlier
        'transfer' offer in the same chain, name that offer's receiver as its
        actor and that offer's actor as transfer.from, and be signed by a
        different kid than the offer. A 'transfer' with no matching 'accept' is
        a legitimate in-flight hand-off → WARN, never FAIL."""
    errs = 0
    warns = 0
    lines: list[str] = []
    assets_checked = 0
    for asset in _iter_assets(doc):
        payloads = _asset_payloads(asset)
        ev_entry = payloads.get("dwc.sidecar.events")
        if ev_entry is None:
            continue
        assets_checked += 1
        label    = _asset_label(asset)
        events   = [e for e in (ev_entry.get("value") or []) if isinstance(e, dict)]
        art_list = ((payloads.get("dwc.sidecar.artifacts") or {}).get("value")) or []
        art_list = [a for a in art_list if isinstance(a, dict)]
        version  = _payload_version(ev_entry)

        # (a) event.target ∈ this asset's identifiers ∪ artifact ids
        idset = {f"urn:uuid:{i.get('identifierValue')}"
                 for i in asset.get("identifier") or []
                 if isinstance(i, dict) and i.get("identifierValue")}
        artifact_ids  = {a.get("id") for a in art_list if a.get("id")}
        valid_targets = idset | artifact_ids
        for ev in events:
            tgt = ev.get("target")
            if tgt not in valid_targets:
                lines.append(f"Stage 3.5 — asset {label} event seq={ev.get('seq')}: FAIL — "
                             f"target {tgt!r} is not an identifier or artifact of this asset "
                             f"(possible cross-sidecar replay)")
                errs += 1

        # (b) signed artifact commitments ↔ declared artifact hashes
        declared  = {a.get("id"): a.get("hash") for a in art_list if a.get("id")}
        committed: dict = {}
        for ev in events:
            for c in ev.get("artifacts") or []:
                if isinstance(c, dict) and c.get("id"):
                    committed[c["id"]] = (c.get("hash"), ev.get("seq"))
        for aid, (h, seq) in committed.items():
            if aid not in declared:
                lines.append(f"Stage 3.5 — asset {label} event seq={seq}: FAIL — "
                             f"signed commitment for unknown artifact {aid}")
                errs += 1
            elif declared[aid] != h:
                lines.append(f"Stage 3.5 — asset {label} artifact {aid}: FAIL — "
                             f"declared hash differs from the signed commitment in event "
                             f"seq={seq} (artifact block tampered?)")
                errs += 1
        if version == "v0.1":
            lines.append(f"Stage 3.5 — asset {label}: NOTE — v0.1 payload, signed artifact "
                         f"commitments not required (artifact hashes are unsigned)")
        else:
            for aid in declared:
                if aid not in committed:
                    lines.append(f"Stage 3.5 — asset {label}: FAIL — artifact {aid} is not "
                                 f"covered by any signed event commitment")
                    errs += 1

        # (c) chain-head anchor
        head_entry = payloads.get("dwc.sidecar.head")
        if head_entry is not None:
            head = head_entry.get("value") or {}
            if not events:
                lines.append(f"Stage 3.5 — asset {label}: FAIL — head anchor present but no events")
                errs += 1
            else:
                last = events[-1]
                if head.get("seq") != last.get("seq"):
                    lines.append(f"Stage 3.5 — asset {label}: FAIL — head.seq {head.get('seq')!r} "
                                 f"≠ last event seq {last.get('seq')!r} (chain truncated?)")
                    errs += 1
                if head.get("tipHash") != last.get("hash"):
                    lines.append(f"Stage 3.5 — asset {label}: FAIL — head.tipHash does not match "
                                 f"the last event's hash (chain truncated or rewritten?)")
                    errs += 1
        elif version == "v0.1":
            lines.append(f"Stage 3.5 — asset {label}: NOTE — v0.1 payload, no chain-head anchor "
                         f"(truncation to a valid prefix is undetectable)")
        else:
            lines.append(f"Stage 3.5 — asset {label}: FAIL — missing dwc.sidecar.head chain anchor")
            errs += 1

        # (d) two-party transfer ↔ accept binding
        e, w = _check_transfer_binding(events, label, lines)
        errs += e
        warns += w

    if errs == 0:
        wnote = f", {warns} WARN(s)" if warns else ""
        lines.append(f"Stage 3.5 — binding + commitments: OK ({assets_checked} asset(s) checked){wnote}")
    status = "fail" if errs else ("warn" if warns else "pass")
    return _result("3.5", "Event↔asset binding + signed commitments",
                   status=status, errors=errs, warnings=warns, lines=lines)


def _check_transfer_binding(events, label, lines) -> tuple[int, int]:
    """Stage 3.5 (d): structural sender↔receiver binding of transfer/accept
    pairs. Signature validity and kid↔actor binding are Stage 4's job; here we
    check that an acceptance actually references *this* chain's offer and the
    right two parties. Returns (errors, warnings)."""
    errs = warns = 0
    transfers = [ev for ev in events if isinstance(ev, dict) and ev.get("action") == "transfer"]
    accepts   = [ev for ev in events if isinstance(ev, dict) and ev.get("action") == "accept"]
    by_hash   = {ev.get("hash"): ev for ev in transfers}
    accepted_offers: set = set()

    for ac in accepts:
        t = ac.get("transfer") or {}
        of = t.get("of")
        offer = by_hash.get(of)
        if offer is None:
            lines.append(f"Stage 3.5 — asset {label} accept seq={ac.get('seq')}: FAIL — "
                         f"transfer.of {of!r} does not reference a transfer offer in this chain")
            errs += 1
            continue
        accepted_offers.add(offer.get("hash"))
        offer_to   = (offer.get("transfer") or {}).get("to")
        offer_from = (offer.get("actor") or {}).get("id")
        if (ac.get("actor") or {}).get("id") != offer_to:
            lines.append(f"Stage 3.5 — asset {label} accept seq={ac.get('seq')}: FAIL — "
                         f"acceptor {(ac.get('actor') or {}).get('id')!r} is not the offer's "
                         f"receiver {offer_to!r}")
            errs += 1
        if t.get("from") != offer_from:
            lines.append(f"Stage 3.5 — asset {label} accept seq={ac.get('seq')}: FAIL — "
                         f"transfer.from {t.get('from')!r} ≠ the offer's actor {offer_from!r}")
            errs += 1
        if (ac.get("sig") or {}).get("kid") == (offer.get("sig") or {}).get("kid"):
            lines.append(f"Stage 3.5 — asset {label} accept seq={ac.get('seq')}: FAIL — "
                         f"offer and acceptance signed by the same kid (self-acceptance)")
            errs += 1

    for tr in transfers:
        if tr.get("hash") not in accepted_offers:
            lines.append(f"Stage 3.5 — asset {label} transfer seq={tr.get('seq')}: WARN — "
                         f"custody hand-off to {(tr.get('transfer') or {}).get('to')!r} not "
                         f"counter-signed (transfer in flight)")
            warns += 1
    return errs, warns


def _load_keyring(keyring_path: Path, revocations_path: Path):
    """Load keyring.json (+ optional CRL-style revocations.json).
    Returns (keyring, pubkeys) or (None, None) when no keyring exists."""
    if not keyring_path.exists():
        return None, None
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
    return keyring, pubkeys


def _check_actor_binding(keyring, kid, actor_id, what, lines, stage="4"):
    """Stage 4 kid↔actor binding. When the signing key's keyring entry declares
    an ``actor`` URN, the event's ``actor.id`` must equal it — otherwise any
    trusted key could sign an event claiming any actor, and the 'distinct
    parties' guarantee that threshold locks and transfer counter-signing rely on
    would be vacuous. No ``actor`` declared → unchanged (no enforcement)."""
    bound = (keyring.get(kid) or {}).get("actor")
    if bound and actor_id != bound:
        lines.append(f"Stage {stage} — {what}: FAIL — actor {actor_id!r} does not match "
                     f"the actor bound to key {kid!r} in the keyring ({bound!r})")
        return 1
    return 0


def _check_key_window(keyring, kid, ts_str, what, lines, stage="4"):
    """Validity-window + revocation check. Returns error count."""
    ts     = _parse_iso(ts_str)
    vfrom  = _parse_iso(keyring[kid].get("validFrom"))
    vto    = _parse_iso(keyring[kid].get("validUntil"))
    revkd  = _parse_iso(keyring[kid].get("revokedAt"))
    reason = keyring[kid].get("revocationReason")
    if ts and vfrom and ts < vfrom:
        lines.append(f"Stage {stage} — {what}: FAIL — ts {ts.isoformat()} predates "
                     f"key validFrom {vfrom.isoformat()}")
        return 1
    if ts and vto and ts > vto:
        lines.append(f"Stage {stage} — {what}: FAIL — ts {ts.isoformat()} after "
                     f"key validUntil {vto.isoformat()}")
        return 1
    if ts and revkd and ts > revkd:
        lines.append(f"Stage {stage} — {what}: FAIL — ts {ts.isoformat()} after "
                     f"key revokedAt {revkd.isoformat()} ({reason!r})")
        return 1
    return 0


def _verify_record_sig(record: dict, pub) -> tuple[bool, str]:
    """Verify the Ed25519 signature of a sig-bearing record (lock, head) over
    its JCS canonical bytes (record minus 'hash'/'sig')."""
    import base64
    from cryptography.exceptions import InvalidSignature
    sig = record.get("sig") or {}
    if sig.get("alg") != "ed25519":
        return False, f"unsupported sig.alg {sig.get('alg')!r}"
    try:
        pub.verify(base64.b64decode(sig["value"]), canonical_bytes(record))
    except InvalidSignature:
        return False, "Ed25519 signature invalid"
    except Exception as e:
        return False, f"signature decode error: {e}"
    return True, "ok"


def validate_signatures(doc, keyring_path: Path = KEYRING,
                        revocations_path: Path = REVOCATIONS,
                        require_keyring: bool = False) -> dict:
    """Stage 4: recompute each event's hash over canonical body, verify Ed25519
    signature, and check the event ts falls inside the signing key's validity
    window. Also verifies chain-head anchor signatures.

    A missing keyring means *nothing cryptographic was checked* — that is a
    warning by default and a failure under --require-keyring, never a silent
    pass: fail-open on the one security-critical stage invites forged sidecars
    shipped without a keyring."""
    lines: list[str] = []
    keyring, pubkeys = _load_keyring(keyring_path, revocations_path)
    if keyring is None:
        if require_keyring:
            lines.append(f"Stage 4 — FAIL — no keyring at {keyring_path} and "
                         f"--require-keyring is set: signatures cannot be verified")
            return _result("4", "Ed25519 signatures + key validity",
                           status="fail", errors=1, lines=lines)
        lines.append(f"Stage 4 — WARN — no keyring at {keyring_path}: signatures NOT "
                     f"verified. Obtain the producer's keyring.json, or pass "
                     f"--require-keyring to make this a failure.")
        return _result("4", "Ed25519 signatures + key validity",
                       status="warn", errors=0, warnings=1, lines=lines)
    assert pubkeys is not None  # paired with keyring by _load_keyring

    errs = 0
    checked = 0
    for path, cd in find_custom_data(doc):
        for i, entry in enumerate(cd):
            if not isinstance(entry, dict) or entry.get("domain") != "dwc.sidecar.events":
                continue
            for j, ev in enumerate(entry.get("value") or []):
                if not isinstance(ev, dict):
                    continue
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
                errs += _check_key_window(keyring, kid, ev.get("ts"), where, lines)
                errs += _check_actor_binding(keyring, kid, (ev.get("actor") or {}).get("id"),
                                             where, lines)

    # Chain-head anchors: signature + key window (structural match is Stage 3.5)
    for asset in _iter_assets(doc):
        head_entry = _asset_payloads(asset).get("dwc.sidecar.head")
        if head_entry is None:
            continue
        head = head_entry.get("value") or {}
        kid  = (head.get("sig") or {}).get("kid")
        where = f"asset {_asset_label(asset)} head (kid={kid})"
        pub = pubkeys.get(kid)
        if pub is None:
            lines.append(f"Stage 4 — {where}: FAIL — unknown kid {kid!r}")
            errs += 1
            continue
        ok, reason = _verify_record_sig(head, pub)
        checked += 1
        if not ok:
            lines.append(f"Stage 4 — {where}: FAIL — {reason}")
            errs += 1
            continue
        errs += _check_key_window(keyring, kid, head.get("ts"), where, lines)

    if errs == 0:
        lines.append(f"Stage 4 — signatures + validity: OK ({checked} record(s) verified)")
    status = "fail" if errs else "pass"
    return _result("4", "Ed25519 signatures + key validity",
                   status=status, errors=errs, lines=lines)


def _group_by_domain(doc):
    """Return {domain: [values...]} flattening all customData in the doc."""
    out = {}
    for _, cd in find_custom_data(doc):
        for e in cd:
            if isinstance(e, dict) and isinstance(e.get("domain"), str):
                v = e.get("value")
                if isinstance(v, list):  # object-valued domains (head) don't flatten
                    out.setdefault(e["domain"], []).extend(v)
    return out


def _load_keyring_policies(keyring_path: Path) -> dict:
    """Top-level ``policies`` block of keyring.json (sibling of ``keys``).
    Empty when absent. This is the verifier's out-of-band trust input that the
    document cannot supply for itself — see the policy-downgrade defense."""
    if not keyring_path.exists():
        return {}
    try:
        return load(keyring_path).get("policies") or {}
    except Exception:
        return {}


class _LockResult:
    """Per-lock outcome: the set of valid, event-backed, distinct co-signer kids
    (for both legacy single-sig and threshold locks) plus the error count the
    intrinsic checks raised. The keyring-policy minimum is applied on top."""
    __slots__ = ("valid_signers", "errs", "sig_note")
    def __init__(self):
        self.valid_signers: set[str] = set()
        self.errs = 0
        self.sig_note = False


def _check_legacy_lock(lk, events, keyring, pubkeys, label, idx, lines) -> _LockResult:
    r = _LockResult()
    matches = [
        ev for ev in events
        if isinstance(ev, dict)
        and ev.get("action") == "lock"
        and ev.get("target") == lk.get("target")
        and (ev.get("actor") or {}).get("id") == lk.get("by")
        and _ts_equal(ev.get("ts"), lk.get("at"))
    ]
    if not matches:
        lines.append(f"Stage 5 — asset {label} locks[{idx}]: FAIL — no matching signed "
                     f"lock event in this asset (target={lk.get('target')}, "
                     f"by={lk.get('by')}, at={lk.get('at')})")
        r.errs += 1
        return r
    ev = matches[0]
    lk_kid = (lk.get("sig") or {}).get("kid")
    ev_kid = (ev.get("sig") or {}).get("kid")
    if lk_kid != ev_kid:
        lines.append(f"Stage 5 — asset {label} locks[{idx}]: FAIL — sig.kid {lk_kid!r} "
                     f"does not match event sig.kid {ev_kid!r}")
        r.errs += 1
        return r
    if pubkeys is None:
        r.sig_note = True
        return r
    pub = pubkeys.get(lk_kid)
    if pub is None:
        lines.append(f"Stage 5 — asset {label} locks[{idx}]: FAIL — unknown kid {lk_kid!r}")
        r.errs += 1
        return r
    ok, reason = _verify_record_sig(lk, pub)
    if not ok:
        lines.append(f"Stage 5 — asset {label} locks[{idx}]: FAIL — lock signature: {reason}")
        r.errs += 1
        return r
    r.valid_signers.add(lk_kid)
    return r


def _check_threshold_lock(lk, events, keyring, pubkeys, label, idx, lines) -> _LockResult:
    """m-of-n threshold lock: each co-signature must (1) name a kid in policy.of,
    (2) verify over JCS(lock minus sig/sigs), (3) be backed by a matching signed
    'lock' event in this asset, and (4) survive the key validity/revocation
    window at its event's ts. The count of distinct kids meeting all four must
    reach policy.m."""
    r = _LockResult()
    policy = lk.get("policy") or {}
    m  = policy.get("m")
    of = policy.get("of") or []
    if not isinstance(m, int) or m < 1:
        lines.append(f"Stage 5 — asset {label} locks[{idx}]: FAIL — policy.m {m!r} invalid")
        r.errs += 1
        return r
    if len(of) < m:
        lines.append(f"Stage 5 — asset {label} locks[{idx}]: FAIL — policy.of has {len(of)} "
                     f"entrie(s), fewer than m={m}")
        r.errs += 1
        return r
    if pubkeys is None:
        # No keyring → nothing cryptographic was checked (Stage 4 already warned).
        r.sig_note = True
        return r

    of_set = set(of)
    for sig in lk.get("sigs") or []:
        kid = sig.get("kid")
        if kid not in of_set:
            lines.append(f"Stage 5 — asset {label} locks[{idx}]: FAIL — co-signer {kid!r} "
                         f"is not in policy.of")
            r.errs += 1
            continue
        pub = pubkeys.get(kid)
        if pub is None:
            lines.append(f"Stage 5 — asset {label} locks[{idx}]: FAIL — unknown co-signer kid {kid!r}")
            r.errs += 1
            continue
        # The co-signature signs JCS(lock minus sig/sigs); verify it against this kid.
        ok, reason = _verify_threshold_sig(lk, sig, pub)
        if not ok:
            lines.append(f"Stage 5 — asset {label} locks[{idx}]: FAIL — co-signer {kid!r} "
                         f"signature: {reason}")
            r.errs += 1
            continue
        backing = next((ev for ev in events
                        if isinstance(ev, dict)
                        and ev.get("action") == "lock"
                        and ev.get("target") == lk.get("target")
                        and (ev.get("sig") or {}).get("kid") == kid), None)
        if backing is None:
            lines.append(f"Stage 5 — asset {label} locks[{idx}]: FAIL — co-signer {kid!r} has no "
                         f"matching signed lock event in this asset")
            r.errs += 1
            continue
        if _check_key_window(keyring, kid, backing.get("ts"),
                             f"asset {label} locks[{idx}] co-signer {kid}", lines, stage="5"):
            r.errs += 1
            continue
        r.valid_signers.add(kid)  # set dedupes a kid that signs twice

    if len(r.valid_signers) < m:
        lines.append(f"Stage 5 — asset {label} locks[{idx}]: FAIL — threshold not met "
                     f"({len(r.valid_signers)} of {m})")
        r.errs += 1
    return r


def _verify_threshold_sig(lk: dict, sig: dict, pub) -> tuple[bool, str]:
    """Verify one co-signature in a threshold lock's ``sigs`` array. The signed
    bytes are JCS(lock minus 'sig'/'sigs') — the same body for every co-signer,
    so the policy is under each signature."""
    import base64
    from cryptography.exceptions import InvalidSignature
    if sig.get("alg") != "ed25519":
        return False, f"unsupported sig.alg {sig.get('alg')!r}"
    try:
        pub.verify(base64.b64decode(sig["value"]), canonical_bytes(lk))
    except InvalidSignature:
        return False, "Ed25519 signature invalid"
    except Exception as e:
        return False, f"signature decode error: {e}"
    return True, "ok"


def _enforce_keyring_lock_policy(lock_policies, lk, valid_signers, label, idx, lines) -> int:
    """Policy-downgrade defense: when the verifier's keyring declares a minimum
    lock policy for this scope, enforce it regardless of what the document's own
    policy says. Stops an attacker who controls one trusted key from writing a
    fresh 1-of-1 lock to bypass a 2-of-n requirement."""
    pol = (lock_policies.get("lock") or {}).get(lk.get("scope"))
    if not pol:
        return 0
    need_m  = pol.get("m", 1)
    allowed = set(pol.get("of") or [])
    eff = (valid_signers & allowed) if allowed else set(valid_signers)
    if len(eff) < need_m:
        lines.append(f"Stage 5 — asset {label} locks[{idx}]: FAIL — keyring policy requires "
                     f"{need_m}-of within {sorted(allowed) or 'any'}, but only "
                     f"{len(eff)} valid co-signer(s) qualify (policy-downgrade defense)")
        return 1
    return 0


def _warn_unlock_below_threshold(lk, events, label, idx, lines) -> int:
    """Threshold unlock semantics (warning-only): a lock that needed m signers
    to set should need m to clear. We can't reconstruct full lock lifecycle from
    the derived view, so we WARN when the event log shows unlock events for a
    threshold-locked target that never reach m distinct policy.of kids."""
    policy = lk.get("policy") or {}
    m  = policy.get("m")
    of = set(policy.get("of") or [])
    if not isinstance(m, int):
        return 0
    unlock_kids = {kid for ev in events
                   if isinstance(ev, dict) and ev.get("action") == "unlock"
                   and ev.get("target") == lk.get("target")
                   and (kid := (ev.get("sig") or {}).get("kid")) is not None}
    unlock_kids &= of
    if unlock_kids and len(unlock_kids) < m:
        lines.append(f"Stage 5 — asset {label} locks[{idx}]: WARN — threshold lock has "
                     f"{len(unlock_kids)} unlock event(s) but needs {m} to clear "
                     f"(unlock below threshold; full lock-lifecycle reconstruction is out of scope)")
        return 1
    return 0


def validate_lock_event_crosscheck(doc, keyring_path: Path = KEYRING,
                                   revocations_path: Path = REVOCATIONS) -> dict:
    """Stage 5: every locks[] entry must be backed by signed lock event(s) *within
    the same asset* and verify cryptographically. A single-signer lock pairs to
    one event and checks its own sig; an m-of-n threshold lock requires m valid,
    event-backed, distinct co-signers over the shared JCS body. A keyring-declared
    minimum policy is enforced on top as a floor (policy-downgrade defense)."""
    keyring, pubkeys = _load_keyring(keyring_path, revocations_path)
    lock_policies = _load_keyring_policies(keyring_path)

    errs = 0
    warns = 0
    total_locks = 0
    sig_note = False
    lines: list[str] = []
    for asset in _iter_assets(doc):
        payloads = _asset_payloads(asset)
        locks  = ((payloads.get("dwc.sidecar.locks")  or {}).get("value")) or []
        events = ((payloads.get("dwc.sidecar.events") or {}).get("value")) or []
        label  = _asset_label(asset)
        for idx, lk in enumerate(locks):
            if not isinstance(lk, dict):
                continue
            total_locks += 1
            if lk.get("policy") is not None or lk.get("sigs") is not None:
                r = _check_threshold_lock(lk, events, keyring, pubkeys, label, idx, lines)
                warns += _warn_unlock_below_threshold(lk, events, label, idx, lines)
            else:
                r = _check_legacy_lock(lk, events, keyring, pubkeys, label, idx, lines)
            errs += r.errs
            sig_note = sig_note or r.sig_note
            if lock_policies:
                errs += _enforce_keyring_lock_policy(lock_policies, lk, r.valid_signers,
                                                     label, idx, lines)
    if sig_note:
        lines.append("Stage 5 — NOTE — lock signatures not verified (no keyring; see Stage 4)")
    if errs == 0:
        wnote = f", {warns} WARN(s)" if warns else ""
        lines.append(f"Stage 5 — lock↔event crosscheck: OK ({total_locks} lock(s) paired){wnote}")
    status = "fail" if errs else ("warn" if warns else "pass")
    return _result("5", "Lock ↔ signed event crosscheck",
                   status=status, errors=errs, warnings=warns, lines=lines)


def _mhl_declared_hash_for_path(doc, base_dir, clip_path_str):
    """If any MHL artifact in the doc declares a hash for the same file as `clip_path_str`,
    return (alg, value). Otherwise None."""
    groups    = _group_by_domain(doc)
    artifacts = groups.get("dwc.sidecar.artifacts") or []
    for m in artifacts:
        if m.get("kind") != "asc-mhl":
            continue
        mhl_path  = _safe_path(base_dir, m.get("path", ""))
        mhl_entry = m.get("mhlEntry")
        if mhl_path is None or not (mhl_path.exists() and mhl_entry):
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
    warns = 0
    checked = 0
    skipped = 0
    lines: list[str] = []
    for idx, a in enumerate(artifacts):
        h    = a.get("hash") or {}
        alg  = h.get("alg")
        want = h.get("value")
        where = f"artifacts[{idx}] kind={a.get('kind')} path={a.get('path')}"

        if (a.get("role") in INTEGRITY_ROLES and alg in WEAK_HASH_ALGS):
            lines.append(f"Stage 6 — {where}: WARN — {alg} has no adversarial-collision "
                         f"resistance; use sha256/sha512/blake3/c4 for {a.get('role')} artifacts")
            warns += 1

        path = _safe_path(base_dir, a.get("path", ""))
        if path is None:
            lines.append(f"Stage 6 — {where}: FAIL — path escapes base-dir")
            errs += 1
            continue
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

        try:
            got = file_digest(path, alg)
        except OSError as e:
            lines.append(f"Stage 6 — {where}: FAIL — unreadable: {e}")
            errs += 1
            continue
        checked += 1
        if got != want:
            lines.append(f"Stage 6 — {where}: FAIL — {alg} mismatch "
                         f"(declared {want[:16]}…, actual {got[:16]}…)")
            errs += 1
    if errs == 0:
        note = f" ({skipped} delegated to Stage 8 via --trust-mhl)" if skipped else ""
        wnote = f", {warns} WARN(s)" if warns else ""
        lines.append(f"Stage 6 — artifact file integrity: OK ({checked} file(s) hashed){note}{wnote}")
    status = "fail" if errs else ("warn" if warns else "pass")
    return _result("6", "Artifact file integrity",
                   status=status, errors=errs, warnings=warns, lines=lines)


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
        mhl_path  = _safe_path(base_dir, a.get("path", ""))
        mhl_entry = a.get("mhlEntry")
        where = f"artifacts[{idx}] MHL path={a.get('path')} entry={mhl_entry}"
        if mhl_path is None:
            lines.append(f"Stage 8 — {where}: FAIL — MHL path escapes base-dir")
            errs += 1
            continue
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
        # MHL paths are relative to the MHL's own directory (with a base_dir
        # fallback); both resolutions are attacker-influenced strings, so both
        # are containment-checked against base_dir.
        candidates = [
            _safe_path(base_dir, entry["File"], anchor=mhl_path.parent),
            _safe_path(base_dir, entry["File"]),
        ]
        if all(c is None for c in candidates):
            lines.append(f"Stage 8 — {where}: FAIL — MHL entry path escapes base-dir")
            errs += 1
            continue
        camera_file = next((c for c in candidates if c is not None and c.exists()), None)
        if camera_file is None:
            lines.append(f"Stage 8 — {where}: SKIP — camera file {entry['File']!r} not present; "
                         f"MHL entry declares {alg}={declared[:16]}…")
            continue
        try:
            got = file_digest(camera_file, alg)
        except OSError as e:
            lines.append(f"Stage 8 — {where}: FAIL — unreadable: {e}")
            errs += 1
            continue
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
        cdl_path = _safe_path(base_dir, cdl_art.get("path", ""))
        if cdl_path is None:
            lines.append(f"Stage 9 — cdl {cdl_art.get('path')}: WARN path escapes base-dir"); warns += 1; continue
        if not cdl_path.exists():
            lines.append(f"Stage 9 — cdl {cdl_art.get('path')}: SKIP file not present"); continue
        try:
            cdl_vals = parse_cdl(cdl_path)
        except Exception as e:
            lines.append(f"Stage 9 — cdl {cdl_path.name}: WARN parse error: {e}"); warns += 1; continue

        for amf_art in amf_arts:
            amf_path = _safe_path(base_dir, amf_art.get("path", ""))
            if amf_path is None:
                lines.append(f"Stage 9 — amf {amf_art.get('path')}: WARN path escapes base-dir"); warns += 1; continue
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
    """Stage 2.5 (opt-in): byte-compare each local schema against its hosted copy.
    The URL is the schema's own $id, so each version checks against its own
    published directory. Any divergence is a drift error — the published schema
    is the canonical, immutable form and local must match."""
    import hashlib, subprocess

    lines: list[str] = [f"Stage 2.5 — hosted-schema drift ({HOSTED_SCHEMA_BASE}/…)"]
    errs = 0
    for path in DWC_SCHEMAS.values():
        name    = path.name
        local   = path.read_bytes()
        url     = json.loads(local).get("$id") or f"{HOSTED_SCHEMA_BASE}/{name}"
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
                missing_is_skip: bool = False,
                require_keyring: bool = False) -> list[dict]:
    """Run all stages in the canonical order and return their results. Shared
    by main() (which prints) and validate_as_json() (which returns a dict)."""
    kr  = keyring_path     if keyring_path     is not None else KEYRING
    rv  = revocations_path if revocations_path is not None else REVOCATIONS
    results = [
        validate_omc(doc),
        validate_dwc_extensions(doc),
    ]
    if check_hosted:
        results.append(check_hosted_schemas())
    results.extend([
        validate_chain_integrity(doc),
        validate_binding(doc),
        validate_signatures(doc, keyring_path=kr, revocations_path=rv,
                            require_keyring=require_keyring),
        validate_lock_event_crosscheck(doc, keyring_path=kr, revocations_path=rv),
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
                     missing_is_skip: bool = False,
                     require_keyring: bool = False) -> dict:
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
                          missing_is_skip=missing_is_skip, require_keyring=require_keyring)
    errors   = sum(r["errors"] for r in results)
    warnings = sum(r["warnings"] for r in results)
    return {
        "target": str(target),
        "base_dir": str(base),
        "stages": results,
        "errors": errors,
        "warnings": warnings,
        "summary": _summary(errors, warnings),
    }


def _summary(errors: int, warnings: int) -> str:
    if errors:
        return f"FAIL ({errors} error(s))"
    if warnings:
        return f"OK ({warnings} warning(s))"
    return "OK"


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
    ap.add_argument("--require-keyring", action="store_true",
                     help="Fail (not warn) when keyring.json is absent. Use whenever "
                          "you are verifying a sidecar from an untrusted sender — "
                          "without a keyring no signature is actually checked.")
    args = ap.parse_args(argv[1:])
    target = args.target.resolve()
    base   = (args.base_dir or target.parent).resolve()
    print(f"→ {target}\n  base-dir: {base}\n")
    doc = load(target)
    results = _run_stages(doc, base, trust_mhl=args.trust_mhl, check_hosted=args.check_hosted,
                          require_keyring=args.require_keyring)
    rc = _print_results(results)
    warnings = sum(r["warnings"] for r in results)
    print("SUMMARY:", _summary(rc, warnings))
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
