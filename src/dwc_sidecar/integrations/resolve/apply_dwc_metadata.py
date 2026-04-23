"""Walk the active DaVinci Resolve project's Media Pool, match each clip
to a DWC sidecar by filename, and write eight ``DWC_*`` provenance fields
onto the clip's custom metadata via ``MediaPoolItem.SetMetadata``.

Invocation:

  - From inside Resolve: Workspace → Scripts → Utility → ``apply_dwc_metadata``.
    Resolve populates a global ``resolve`` object for scripts run from its
    menu, so this file discovers it without a ``RESOLVE_SCRIPT_LIB`` round-trip.

  - Headless / pipeline: ``python3 -m dwc_sidecar.integrations.resolve.apply_dwc_metadata``
    with ``RESOLVE_SCRIPT_LIB`` pointing at
    ``fusionscript.{so,dll,dylib}`` — see the vendor READMEs committed
    at ``resources/documentation/DaVinciResolve{20,21}_Scripting_README.txt``.

Sidecar → clip matching uses a substring scorer ported from
``~/Documents/Resolve-Tools/Import-AMF/Import_AMF.py:950–1020``
(prior art with established real-production tuning). Threshold 65 with
100/90/85/80/75/70/65 tiers; each clip matches at most one sidecar so
reruns don't cross-wire a clip to a different sidecar than before.

Custom metadata fields must pre-exist in the Resolve project's
Metadata & Scene settings. ``SetMetadata`` silently returns ``False``
for unknown fields — use the sibling ``ensure_custom_fields.py`` helper
to surface the setup steps.

``SetThirdPartyMetadata`` is intentionally not used. The vendor README
exposes it as a separate namespace, but it doesn't appear in the
substantial prior art at ``~/Documents/Resolve-Tools/`` — suggesting
it's either broken or not surfaced in Resolve's UI. Stick with
``SetMetadata`` and document the pre-existing-field constraint.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


# Eight DWC_* fields mirror the ALE emitter (plan §1.4). Resolve has no
# six-slot limit like Silverstack's setCustomN, so all eight go directly.
DWC_FIELDS: tuple[str, ...] = (
    "DWC_Signed", "DWC_Kid", "DWC_Events", "DWC_Locks",
    "DWC_LockedBy", "DWC_LastVerified", "DWC_SidecarPath", "DWC_ChainHead",
)

SCORE_THRESHOLD = 65


# ── Pure: sidecar → DWC fields dict ─────────────────────────────────────


def _strip_hash_prefix(h: str) -> str:
    return h.split(":", 1)[1] if ":" in h else h


def _walk_custom_data(node: Any):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "customData" and isinstance(v, list):
                yield v
            yield from _walk_custom_data(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_custom_data(v)


def extract_dwc_fields(sidecar_path: Path, *,
                       signed: bool = True,
                       now: datetime | None = None) -> dict[str, str]:
    """Load a sidecar and return the eight-field dict ready for
    ``SetMetadata`` — mirrors the shape of ``ale_emitter.extract_row_from_sidecar``
    but without the four Avid-standard columns."""
    doc = json.loads(Path(sidecar_path).read_text())

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

    events_sorted = sorted(events,
                           key=lambda e: (e.get("ts") or "", e.get("seq") or 0))

    latest_kid, chain_head = "", ""
    if events_sorted:
        tip = events_sorted[-1]
        latest_kid = ((tip.get("sig") or {}).get("kid") or "")
        chain_head = _strip_hash_prefix(tip.get("hash") or "")[:12]

    locked_by = ""
    if locks:
        lock_events = sorted(
            (ev for ev in events if ev.get("action") == "lock"),
            key=lambda e: (e.get("ts") or "", e.get("seq") or 0),
        )
        if lock_events:
            locked_by = ((lock_events[-1].get("sig") or {}).get("kid") or "")

    now = now or datetime.now(timezone.utc)
    return {
        "DWC_Signed":       "true" if signed else "false",
        "DWC_Kid":          latest_kid,
        "DWC_Events":       str(len(events)),
        "DWC_Locks":        str(len(locks)),
        "DWC_LockedBy":     locked_by,
        "DWC_LastVerified": now.replace(microsecond=0).isoformat()
                               .replace("+00:00", "Z"),
        "DWC_SidecarPath":  Path(sidecar_path).name,
        "DWC_ChainHead":    chain_head,
    }


# ── Pure: substring scorer (port of Import_AMF.py:match_amf_to_clips) ──


def _strip_double_ext(basename: str) -> str:
    """DWC sidecars use ``.omc.json`` — a double extension. ``os.path.splitext``
    only removes the last one, leaving ``.omc`` trailing noise. Strip that
    explicitly so substring matches against clip names work; fall back to
    the standard single-ext split for non-DWC paths."""
    if basename.endswith(".omc.json"):
        return basename[:-len(".omc.json")]
    return os.path.splitext(basename)[0]


def score_name_match(sidecar_name: str, sidecar_path: str, clip_name: str) -> int:
    """Return 0–100; higher is a better match. Scores mirror the
    Import_AMF.py scorer; all substring checks require both sides to be
    non-empty so a blank clip name or sidecar stem can't match everything."""
    sidecar_base = _strip_double_ext(os.path.basename(sidecar_path)) \
                   if sidecar_path else ""

    if sidecar_name and clip_name and sidecar_name == clip_name:
        return 100
    if sidecar_name and clip_name and sidecar_name in clip_name:
        return 90
    if sidecar_name and clip_name and clip_name in sidecar_name:
        return 85
    if sidecar_base and clip_name and sidecar_base in clip_name:
        return 80
    if sidecar_base and clip_name and clip_name in sidecar_base:
        return 75
    if sidecar_path and clip_name:
        base = _strip_double_ext(os.path.basename(sidecar_path))
        if base and base in clip_name:
            return 70
        if base and clip_name in base:
            return 65
    return 0


def match_sidecars_to_clips(
    sidecars: Sequence[tuple[str, str]],
    clip_names: Sequence[str],
    *,
    threshold: int = SCORE_THRESHOLD,
) -> dict[int, tuple[int, int]]:
    """Return ``{sidecar_index: (clip_index, score)}`` for matches at or
    above ``threshold``. One clip matches at most one sidecar — reruns are
    stable against the sidecar order given a deterministic ``clip_names``."""
    matches: dict[int, tuple[int, int]] = {}
    used: set[int] = set()
    for si, (name, path) in enumerate(sidecars):
        best_ci:    int | None = None
        best_score: int        = 0
        for ci, clip in enumerate(clip_names):
            if ci in used:
                continue
            s = score_name_match(name, path, clip)
            if s > best_score:
                best_score = s
                best_ci    = ci
        if best_ci is not None and best_score >= threshold:
            matches[si] = (best_ci, best_score)
            used.add(best_ci)
    return matches


# ── Metadata write (mockable surface) ───────────────────────────────────


def apply_fields_to_clip(
    mp_item: Any,
    fields: dict[str, str],
) -> tuple[int, list[str]]:
    """Call ``SetMetadata`` for every DWC_* field. Returns
    ``(ok_count, missing_field_names)``.

    Resolve's ``SetMetadata`` silently returns ``False`` for fields not
    pre-created in Project Settings → Metadata & Scene (plan risks §1.10,
    Import_AMF.py:1407). We treat ``False`` or any exception as "field
    missing" and continue with the rest — partial application is useful."""
    missing: list[str] = []
    ok = 0
    for key in DWC_FIELDS:
        value = fields.get(key, "")
        try:
            result = mp_item.SetMetadata(key, value)
        except Exception as e:
            missing.append(f"{key} (exception: {e})")
            continue
        if result:
            ok += 1
        else:
            missing.append(key)
    return ok, missing


# ── Resolve plumbing (Media Pool walker + connection) ───────────────────


def connect_to_resolve() -> Any:
    """Resolve the ``resolve`` global when running inside Resolve; otherwise
    load ``DaVinciResolveScript`` from ``RESOLVE_SCRIPT_LIB`` for headless
    invocation. Returns the top-level Resolve object or ``None`` if neither
    is available."""
    if "resolve" in globals():
        return globals()["resolve"]
    if "bmd" in globals():
        try:
            return globals()["bmd"].scriptapp("Resolve")
        except Exception:
            pass
    lib = os.environ.get("RESOLVE_SCRIPT_LIB")
    if not lib:
        return None
    lib_dir = os.path.dirname(lib.split(";")[0].split(":")[0])
    if lib_dir and lib_dir not in sys.path:
        sys.path.insert(0, lib_dir)
    try:
        import DaVinciResolveScript as dvr_script  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        return dvr_script.scriptapp("Resolve")
    except Exception:
        return None


def walk_media_pool(folder: Any) -> list[Any]:
    """Recurse a Media Pool folder, returning all ``MediaPoolItem``
    objects. Order is depth-first, folder-declared order — stable for
    repeated runs so matching reruns are deterministic."""
    items: list[Any] = []
    clips = folder.GetClipList() or []
    items.extend(clips)
    for sub in (folder.GetSubFolderList() or []):
        items.extend(walk_media_pool(sub))
    return items


def get_clip_name(mp_item: Any) -> str:
    """Prefer the clip property Resolve displays; fall back to
    ``GetName``. Exact field varies across Resolve 20 / 21."""
    try:
        props = mp_item.GetClipProperty() or {}
        name = props.get("Clip Name") or props.get("File Name") or ""
        if name:
            return os.path.splitext(str(name))[0]
    except Exception:
        pass
    try:
        name = mp_item.GetName() or ""
        return os.path.splitext(str(name))[0]
    except Exception:
        return ""


def discover_sidecars(sidecar_dir: Path) -> list[tuple[str, str]]:
    """Scan ``sidecar_dir`` for ``*.omc.json``; return
    ``[(sidecar_stem, absolute_path), …]`` ready for matching."""
    out: list[tuple[str, str]] = []
    for p in sorted(Path(sidecar_dir).glob("*.omc.json")):
        stem = p.stem
        if stem.endswith(".omc"):
            stem = stem[:-4]
        out.append((stem, str(p)))
    return out


# ── CLI / main ──────────────────────────────────────────────────────────


def run(sidecar_dir: Path, *, now: datetime | None = None,
        resolve_obj: Any = None) -> int:
    """Run the full sidecars → clips → SetMetadata flow against a live
    Resolve project. Returns a process-style exit code (0 = success).

    ``resolve_obj`` is injectable so tests can drive the whole function
    with a mock Resolve without reaching the real runtime."""
    r = resolve_obj or connect_to_resolve()
    if r is None:
        print("ERROR: could not connect to DaVinci Resolve. Either run this "
              "script from inside Resolve (Workspace → Scripts → Utility) or "
              "set RESOLVE_SCRIPT_LIB and run externally.", file=sys.stderr)
        return 2

    project_manager = r.GetProjectManager()
    project = project_manager.GetCurrentProject() if project_manager else None
    if project is None:
        print("ERROR: no current project open in Resolve.", file=sys.stderr)
        return 3

    media_pool = project.GetMediaPool()
    root = media_pool.GetRootFolder() if media_pool else None
    if root is None:
        print("ERROR: project has no Media Pool root folder.", file=sys.stderr)
        return 3

    clips = walk_media_pool(root)
    clip_names = [get_clip_name(c) for c in clips]
    sidecars = discover_sidecars(Path(sidecar_dir))
    if not sidecars:
        print(f"No *.omc.json files in {sidecar_dir}.", file=sys.stderr)
        return 0

    matches = match_sidecars_to_clips(sidecars, clip_names)
    if not matches:
        print(f"No clip matches found for {len(sidecars)} sidecars (threshold "
              f"{SCORE_THRESHOLD}).", file=sys.stderr)
        return 0

    all_missing: set[str] = set()
    applied = 0
    for si, (ci, score) in sorted(matches.items()):
        name, path = sidecars[si]
        try:
            fields = extract_dwc_fields(Path(path), signed=True, now=now)
        except Exception as e:
            print(f"WARN: could not parse {path}: {e}", file=sys.stderr)
            continue
        ok, missing = apply_fields_to_clip(clips[ci], fields)
        applied += 1
        for m in missing:
            all_missing.add(m)
        print(f"{name} → {clip_names[ci]}  (score={score}, {ok}/{len(DWC_FIELDS)} fields)")

    if all_missing:
        print()
        print("Some DWC_* fields returned SetMetadata=False — they aren't "
              "configured as custom metadata in this project.", file=sys.stderr)
        print("Add via Project Settings → General Options → Metadata & Scene, "
              "or run ensure_custom_fields.py for the setup walk-through.",
              file=sys.stderr)
        print(f"Missing fields: {sorted(all_missing)}", file=sys.stderr)
    print(f"Applied metadata to {applied} clip(s).")
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Apply DWC sidecar metadata to clips in the current "
                    "DaVinci Resolve project's Media Pool.")
    ap.add_argument("sidecar_dir", type=Path,
                    help="Directory containing the *.omc.json sidecars.")
    args = ap.parse_args(argv)
    return run(args.sidecar_dir)


if __name__ == "__main__":
    sys.exit(main())
