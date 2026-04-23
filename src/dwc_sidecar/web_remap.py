"""Artifact-path remap for the in-browser web validator.

Production sidecars commonly record artifact paths as absolute paths
(``/Volumes/Mag_A001/...``) or as paths rooted at a production tree that
doesn't exist inside Pyodide's virtual filesystem. Before Stage 6 can hash
the artifacts the user dropped in the zip, every ``artifact.path`` in the
sidecar must be rewritten to a path that actually resolves against
``/work/``.

Per plan §4.4a the strategy is:

  1. Match by filename (basename) first — the common case for
     well-structured zips where filenames are unique across the bundle.
  2. If a basename matches multiple files in the zip tree, tie-break by
     the longest trailing path-component overlap with the original path
     ("shortest unique suffix"). A sidecar referring to
     ``Mag_A001/Camera/A001/file.ari`` prefers a candidate at
     ``Camera/A001/file.ari`` over a same-named file elsewhere.
  3. If no basename matches any file in the zip, leave the original path
     in place — Stage 6 with ``missing_is_skip=True`` reports the
     artifact as SKIP rather than FAIL, surfacing "not provided in zip"
     without breaking the whole run.

Kept as a stand-alone module (not inlined in ``app.js``) so pytest can
exercise the tie-breaking rules without needing a running Pyodide.
"""
from __future__ import annotations

import os
from pathlib import Path


def _walk_custom_data(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "customData" and isinstance(v, list):
                yield v
            yield from _walk_custom_data(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_custom_data(v)


def build_basename_index(work_dir: Path) -> dict[str, list[str]]:
    """Return ``{basename: [relpath, …]}`` for every file under
    ``work_dir``. Paths in the values are relative to ``work_dir`` and use
    ``os.sep``; callers should treat them as opaque strings for path
    joining, not for pretty display."""
    index: dict[str, list[str]] = {}
    for root, _dirs, files in os.walk(work_dir):
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), work_dir)
            index.setdefault(f, []).append(rel)
    return index


def _best_suffix_match(old_path: str, candidates: list[str]) -> str:
    """Pick the candidate whose trailing path components most closely match
    those of ``old_path``. Ties are broken by input order so the result is
    deterministic across a re-run with the same zip."""
    parts = old_path.replace("\\", "/").split("/")
    best, best_score = candidates[0], -1
    for cand in candidates:
        cand_parts = cand.split(os.sep)
        score = 0
        for a, b in zip(reversed(parts), reversed(cand_parts)):
            if a == b:
                score += 1
            else:
                break
        if score > best_score:
            best_score, best = score, cand
    return best


def remap_artifact_paths(doc: dict, index: dict[str, list[str]]) -> dict:
    """Rewrite every ``artifact.path`` under ``dwc.sidecar.artifacts``
    customData groups to a path present in ``index``.

    Mutates ``doc`` in place and returns it for chaining."""
    for cd in _walk_custom_data(doc):
        for entry in cd:
            if (not isinstance(entry, dict)
                    or entry.get("domain") != "dwc.sidecar.artifacts"):
                continue
            for artifact in entry.get("value") or []:
                old_path = artifact.get("path", "")
                if not old_path:
                    continue
                basename = os.path.basename(old_path.replace("\\", "/"))
                candidates = index.get(basename, [])
                if len(candidates) == 1:
                    artifact["path"] = candidates[0]
                elif len(candidates) > 1:
                    artifact["path"] = _best_suffix_match(old_path, candidates)
                # else: leave as-is; Stage 6 will emit SKIP when
                # missing_is_skip is True.
    return doc
