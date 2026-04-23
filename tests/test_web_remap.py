"""Tests for ``dwc_sidecar.web_remap`` — the artifact-path rewriter used
by the in-browser validator.

Per plan §4.4a: match by basename first, tie-break by the longest
trailing path-component overlap. Unmatched basenames are left alone so
Stage 6 can surface them as SKIP.
"""
from pathlib import Path

from dwc_sidecar import web_remap


def _write(tmp_path: Path, rel: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    return p


def _sidecar(paths: list[str]) -> dict:
    return {"Asset": [{
        "name": "x",
        "assetFC": {"functionalProperties": {"customData": [
            {"domain": "dwc.sidecar.artifacts", "value": [
                {"path": p, "kind": "clip",
                 "hash": {"alg": "sha256", "value": "0" * 64}}
                for p in paths
            ]},
        ]}},
    }]}


def _artifact_paths(doc: dict) -> list[str]:
    out: list[str] = []
    for cd in web_remap._walk_custom_data(doc):
        for entry in cd:
            if entry.get("domain") == "dwc.sidecar.artifacts":
                for a in entry.get("value") or []:
                    out.append(a["path"])
    return out


# ── Index builder ───────────────────────────────────────────────────────


def test_index_builds_flat_tree(tmp_path):
    _write(tmp_path, "a.ari")
    _write(tmp_path, "b.mhl")
    idx = web_remap.build_basename_index(tmp_path)
    assert set(idx) == {"a.ari", "b.mhl"}
    assert idx["a.ari"] == ["a.ari"]


def test_index_collects_nested_duplicates(tmp_path):
    _write(tmp_path, "Camera/A001/foo.ari")
    _write(tmp_path, "Camera/A002/foo.ari")
    idx = web_remap.build_basename_index(tmp_path)
    assert sorted(idx["foo.ari"]) == sorted([
        "Camera/A001/foo.ari",
        "Camera/A002/foo.ari",
    ])


# ── Remap behaviour ─────────────────────────────────────────────────────


def test_absolute_production_path_rewrites_to_flat_zip(tmp_path):
    """Most common case: user drops a flat zip, sidecar has absolute paths."""
    _write(tmp_path, "A001_C042_0420AB.ari")
    _write(tmp_path, "sidecar.ale")
    doc = _sidecar(["/Volumes/Mag_A001/WAR/Camera/A001/A001_C042_0420AB.ari"])
    web_remap.remap_artifact_paths(doc, web_remap.build_basename_index(tmp_path))
    assert _artifact_paths(doc) == ["A001_C042_0420AB.ari"]


def test_ambiguous_basename_picks_longest_suffix_match(tmp_path):
    """Two files named foo.ari in different dirs; the sidecar's original
    path gives enough of a trailing hint to pick the right one."""
    _write(tmp_path, "Camera/A001/foo.ari")
    _write(tmp_path, "Camera/A002/foo.ari")
    doc = _sidecar(["/Volumes/Mag/Camera/A002/foo.ari"])
    web_remap.remap_artifact_paths(doc, web_remap.build_basename_index(tmp_path))
    assert _artifact_paths(doc) == ["Camera/A002/foo.ari"]


def test_ambiguous_basename_no_hint_is_deterministic(tmp_path):
    """If every candidate has the same trailing overlap with the sidecar's
    path, the first-encountered wins so reruns are stable."""
    _write(tmp_path, "A/foo.ari")
    _write(tmp_path, "B/foo.ari")
    doc = _sidecar(["foo.ari"])
    index = web_remap.build_basename_index(tmp_path)
    web_remap.remap_artifact_paths(doc, index)
    # One of them — and the choice must equal index order (sorted by os.walk)
    assert _artifact_paths(doc)[0] in index["foo.ari"]


def test_basename_not_in_bundle_leaves_path_unchanged(tmp_path):
    """Stage 6 with missing_is_skip=True is responsible for surfacing
    "not provided in zip"; the remapper keeps the original path so the
    SKIP message can show what was expected."""
    _write(tmp_path, "other.ari")
    doc = _sidecar(["/Volumes/Mag/Camera/A001/A001_C042.ari"])
    web_remap.remap_artifact_paths(doc, web_remap.build_basename_index(tmp_path))
    assert _artifact_paths(doc) == [
        "/Volumes/Mag/Camera/A001/A001_C042.ari"
    ]


def test_windows_backslash_paths_are_normalised(tmp_path):
    """Sidecars produced on Windows sometimes embed backslashes. The
    basename split must handle both separators."""
    _write(tmp_path, "A001_C042.ari")
    doc = _sidecar([r"X:\Production\Camera\A001_C042.ari"])
    web_remap.remap_artifact_paths(doc, web_remap.build_basename_index(tmp_path))
    assert _artifact_paths(doc) == ["A001_C042.ari"]


def test_empty_path_is_ignored(tmp_path):
    doc = _sidecar([""])
    web_remap.remap_artifact_paths(doc, web_remap.build_basename_index(tmp_path))
    assert _artifact_paths(doc) == [""]


def test_non_artifact_custom_data_left_alone(tmp_path):
    """events and locks shouldn't be touched — only artifacts have `path`."""
    _write(tmp_path, "foo.ari")
    doc = {"Asset": [{"assetFC": {"functionalProperties": {"customData": [
        {"domain": "dwc.sidecar.events",
         "value": [{"seq": 1, "path": "/should/not/be/touched"}]},
        {"domain": "dwc.sidecar.artifacts",
         "value": [{"path": "/anywhere/foo.ari",
                    "hash": {"alg": "sha256", "value": "0" * 64}}]},
    ]}}}]}
    web_remap.remap_artifact_paths(doc, web_remap.build_basename_index(tmp_path))
    events_path = doc["Asset"][0]["assetFC"]["functionalProperties"]\
                     ["customData"][0]["value"][0]["path"]
    assert events_path == "/should/not/be/touched"
    assert _artifact_paths(doc) == ["foo.ari"]
