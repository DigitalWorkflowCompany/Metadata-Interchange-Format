#!/usr/bin/env python3
"""ALE emitter — Avid Log Exchange export of DWC provenance metadata.

An ALE is a tab-separated text file with a small fixed header block; we
emit one row per sidecar with four Avid-standard columns (Name, Tape,
Start, End) plus eight ``DWC_*`` columns carrying the signed state of
the provenance chain (plan §1.4).

Consumed by YoYotta ID, ShotPut Pro, Avid Media Composer, and Silverstack
≤ 9.1 — tools without a richer scripting surface. Silverstack 9.2+ and
Resolve get native scripting (sibling modules under ``integrations/``).

File layout, crash-safety, dedup semantics:

- UTF-8 with CRLF line endings (Avid convention; safest across Windows
  Avid systems, accepted by Silverstack and YoYotta).
- Per-day filename ``dwc-columns-YYYY-MM-DD.ale`` so the file stays
  bounded on a multi-day shoot (plan §1.6).
- Dedup key is ``DWC_SidecarPath``, never ``Name`` — C-numbers reset
  per reel on multi-roll shoots, so two different clips can legitimately
  share a Name (plan edge-cases review #5).
- Atomic rewrite (plan edge-cases review #1): unconditionally delete any
  stale ``.tmp`` from a prior crash before reading the production file,
  write new contents to ``.tmp``, then ``os.replace``. The window between
  ``.tmp`` write and ``os.replace`` can lose the last row on process kill
  — accepted trade-off; regenerate with ``dwc ale-export``.
- Values sanitised for tab/CR/LF (review #3/#4) — a stray tab would shift
  every following column; an embedded line-ending would split the row.

Start/End timecode columns default to ``01:00:00:00`` placeholders. OMC
v2.8's ``functionalCharacteristics.timecode`` isn't populated in our
stub corpus, and DIT tools re-derive timecode from the clip itself at
import. A real deployment with timecode metadata can override these via
``dwc ale-export --tape`` and future per-column overrides.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


# Order matters — DIT tools render columns in file order. Eight DWC_* columns
# are the product contribution; the four Avid-standard columns are table stakes.
AVID_COLUMNS = ("Name", "Tape", "Start", "End")
DWC_COLUMNS  = (
    "DWC_Signed", "DWC_Kid", "DWC_Events", "DWC_Locks",
    "DWC_LockedBy", "DWC_LastVerified", "DWC_SidecarPath", "DWC_ChainHead",
)
COLUMNS = AVID_COLUMNS + DWC_COLUMNS

# A-cam reel prefix: "A001" from "A001_C042_0420AB", "A001C042…", etc. Define
# locally per plan §1.5a — no cross-module dependency on mhl_walker.
TAPE_REGEX = re.compile(r"^([A-Z]\d{3})")

# A literal tab would shift every subsequent column; CR or LF would split the
# row into phantom rows. Replace with a single space — losing alignment is
# better than losing correctness.
_SANITIZE = str.maketrans({"\t": " ", "\r": " ", "\n": " "})


def sanitize_value(v: object) -> str:
    return str(v).translate(_SANITIZE)


def _iso_z(dt: datetime) -> str:
    return dt.replace(microsecond=0).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def tape_from_name(name: str) -> str:
    m = TAPE_REGEX.match(name)
    return m.group(1) if m else ""


def ale_path_for_day(out_dir: Path, now: datetime | None = None) -> Path:
    """Return ``dwc-columns-YYYY-MM-DD.ale`` inside ``out_dir``."""
    d = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    return Path(out_dir) / f"dwc-columns-{d}.ale"


def _walk_custom_data(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "customData" and isinstance(v, list):
                yield v
            yield from _walk_custom_data(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_custom_data(v)


def extract_row_from_sidecar(
    sidecar_path: Path,
    *,
    now: datetime,
    signed: bool = True,
    ale_dir: Path | None = None,
) -> dict[str, str]:
    """Build an ALE row dict from a sidecar JSON document.

    ``signed`` is caller-supplied because the authoritative answer comes from
    Stage 4 of the validator — not from reading the file alone (a clever
    corruption could remove the event body while leaving signatures in place).

    ``ale_dir`` is the directory the ALE will be written to; the row's
    ``DWC_SidecarPath`` is computed relative to it. If omitted, the bare
    filename is used — fine for a sidecar and ALE in the same directory.
    """
    doc = json.loads(Path(sidecar_path).read_text())
    asset = (doc.get("Asset") or [{}])[0]

    name = (asset.get("name")
            or ((asset.get("AssetSC") or {})
                .get("structuralProperties") or {})
                .get("fileDetails", {}).get("fileName")
            or Path(sidecar_path).stem)

    events: list[dict] = []
    locks:  list[dict] = []
    for group in _walk_custom_data(doc):
        for entry in group:
            if not isinstance(entry, dict):
                continue
            dom = entry.get("domain")
            if dom == "dwc.sidecar.events":
                events.extend(entry.get("value") or [])
            elif dom == "dwc.sidecar.locks":
                locks.extend(entry.get("value") or [])

    # Multi-asset sidecars (clip + proxy, reels) aggregate events from every
    # asset. "Most recent" is by event timestamp — the value falls back on
    # sequence order when timestamps are absent.
    events_sorted = sorted(events, key=lambda e: (e.get("ts") or "", e.get("seq") or 0))
    latest_kid = ""
    chain_head = ""
    if events_sorted:
        tip = events_sorted[-1]
        latest_kid = ((tip.get("sig") or {}).get("kid") or "")
        # Event hashes are stored as "<alg>:<hex>"; ALE consumers want bare hex.
        raw_hash = (tip.get("hash") or "")
        chain_head = (raw_hash.split(":", 1)[1] if ":" in raw_hash else raw_hash)[:12]

    locked_by = ""
    if locks:
        lock_events = sorted(
            (ev for ev in events if ev.get("action") == "lock"),
            key=lambda e: (e.get("ts") or "", e.get("seq") or 0),
        )
        if lock_events:
            locked_by = ((lock_events[-1].get("sig") or {}).get("kid") or "")

    sidecar_abs = Path(sidecar_path).resolve()
    if ale_dir is not None:
        try:
            sidecar_rel = sidecar_abs.relative_to(Path(ale_dir).resolve())
            sidecar_str = str(sidecar_rel)
        except ValueError:
            sidecar_str = str(sidecar_abs)
    else:
        sidecar_str = sidecar_abs.name

    return {
        "Name":             name,
        "Tape":             tape_from_name(name),
        "Start":            "01:00:00:00",
        "End":              "01:00:00:00",
        "DWC_Signed":       "true" if signed else "false",
        "DWC_Kid":          latest_kid,
        "DWC_Events":       str(len(events)),
        "DWC_Locks":        str(len(locks)),
        "DWC_LockedBy":     locked_by,
        "DWC_LastVerified": _iso_z(now),
        "DWC_SidecarPath":  sidecar_str,
        "DWC_ChainHead":    chain_head,
    }


def format_ale(rows: Iterable[dict[str, str]]) -> str:
    """Emit an ALE as a single string. Caller writes with ``encoding='utf-8'``
    and ``newline=''`` (or just bytes) to preserve the CRLF line endings."""
    out: list[str] = [
        "Heading",
        "FIELD_DELIM\tTABS",
        "VIDEO_FORMAT\t1080",
        "AUDIO_FORMAT\t48khz",
        "FPS\t24",
        "",
        "Column",
        "\t".join(COLUMNS),
        "",
        "Data",
    ]
    for row in rows:
        out.append("\t".join(sanitize_value(row.get(col, "")) for col in COLUMNS))
    return "\r\n".join(out) + "\r\n"


def parse_ale(text: str) -> list[dict[str, str]]:
    """Read an ALE back to a list of row dicts. Tolerant of trailing blank
    lines, extra header fields, and mixed LF/CRLF input."""
    lines = text.replace("\r\n", "\n").split("\n")
    columns: list[str] = []
    rows: list[dict[str, str]] = []
    section: str | None = None
    for ln in lines:
        stripped = ln.strip()
        if stripped == "Heading":
            section = "heading"; continue
        if stripped == "Column":
            section = "column";  continue
        if stripped == "Data":
            section = "data";    continue
        if not stripped:
            continue
        if section == "column" and not columns:
            columns = ln.split("\t")
        elif section == "data" and columns:
            values = ln.split("\t")
            rows.append({c: values[i] if i < len(values) else ""
                         for i, c in enumerate(columns)})
    return rows


def update_ale(ale_path: Path, row: dict[str, str],
               now: datetime | None = None) -> None:
    """Atomically insert/replace ``row`` in ``ale_path``, deduped on
    ``DWC_SidecarPath``.

    Crash-safety (§1.6): stale ``.tmp`` from a prior crash is deleted before
    we read the production file, so a half-written temp file is never
    mistaken for real data. ``now`` is kept in the signature for future
    callers that want to backdate rows — currently unused here because rows
    carry their own ``DWC_LastVerified``."""
    _ = now  # reserved for future use; kept for stable signature
    ale_path = Path(ale_path)
    tmp = ale_path.with_suffix(ale_path.suffix + ".tmp")

    if tmp.exists():
        tmp.unlink()

    existing: list[dict[str, str]] = []
    if ale_path.exists():
        try:
            existing = parse_ale(ale_path.read_text(encoding="utf-8"))
        except Exception:
            existing = []

    key = row.get("DWC_SidecarPath", "")
    deduped = [r for r in existing if r.get("DWC_SidecarPath", "") != key]
    deduped.append(row)

    ale_path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(format_ale(deduped), encoding="utf-8")
    os.replace(tmp, ale_path)


# ── CLI ──────────────────────────────────────────────────────────────────


def _stage_4_passed(sidecar_path: Path, base_dir: Path) -> bool:
    """Re-run the validator and return whether Stage 4 (signatures + key
    validity) passed for this sidecar. On exception, return False — a
    sidecar whose validator crashes isn't trustworthy."""
    from .validate import validate_as_json
    try:
        report = validate_as_json(Path(sidecar_path).resolve(), base_dir=base_dir)
    except Exception:
        return False
    stage_4 = next((s for s in report["stages"] if s["stage"] == "4"), None)
    return stage_4 is not None and stage_4["status"] == "pass"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Export DWC provenance metadata to an Avid Log Exchange (ALE) file.")
    ap.add_argument("sidecars", nargs="+", type=Path,
                    help="One or more *.omc.json paths")
    ap.add_argument("--out", type=Path, default=None,
                    help="Output ALE path. Default: dwc-columns.ale next to the first sidecar.")
    ap.add_argument("--validate", action="store_true",
                    help="Run the 9-stage validator on each sidecar first; a Stage 4 failure "
                         "produces DWC_Signed=false (WARN in output), does not abort the export.")
    ap.add_argument("--base-dir", type=Path, default=None,
                    help="Artifact-resolution root for --validate. Default: parent of first sidecar.")
    ap.add_argument("--tape", default=None, help="Override the Tape column for every row")
    args = ap.parse_args(argv)

    out = args.out or (args.sidecars[0].parent / "dwc-columns.ale")
    out = Path(out).resolve()
    base = (args.base_dir or args.sidecars[0].parent).resolve()
    ale_dir = out.parent

    now = datetime.now(timezone.utc)
    for sc in args.sidecars:
        signed = _stage_4_passed(sc, base) if args.validate else True
        if args.validate and not signed:
            print(f"WARN: {sc} — Stage 4 failed; emitting DWC_Signed=false", file=sys.stderr)
        row = extract_row_from_sidecar(sc, now=now, signed=signed, ale_dir=ale_dir)
        if args.tape:
            row["Tape"] = args.tape
        update_ale(out, row, now=now)
    print(f"ALE written → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
