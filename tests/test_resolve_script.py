"""Tests for the DaVinci Resolve integration scripts.

The substring scorer and field-extraction logic are pure Python — tested
directly. The Media Pool walk + SetMetadata path uses a mock Resolve
object so the tests run without a live Resolve install.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dwc_sidecar.integrations.resolve import apply_dwc_metadata as apply
from dwc_sidecar.integrations.resolve import ensure_custom_fields


NOW = datetime(2026, 4, 23, 14, 2, 11, tzinfo=timezone.utc)


# ── Substring scorer (port of Import_AMF.py:950-1020) ───────────────────


def test_scorer_exact_match_is_100():
    assert apply.score_name_match("A001_C042_0420AB", "/x/A001_C042_0420AB.omc.json",
                                   "A001_C042_0420AB") == 100


def test_scorer_sidecar_substring_of_clip_is_90():
    assert apply.score_name_match("A001_C042", "/x/A001_C042.omc.json",
                                   "A001_C042_0420AB") == 90


def test_scorer_clip_substring_of_sidecar_is_85():
    assert apply.score_name_match("A001_C042_0420AB", "/x/A001_C042_0420AB.omc.json",
                                   "A001_C042") == 85


def test_scorer_sidecar_base_substring_of_clip_is_80_or_less():
    # sidecar_name == clip_name OR ⊂ clip_name already matched earlier,
    # so put sidecar_base match in play via a different sidecar_name.
    score = apply.score_name_match("different-name", "/x/A001_C042.omc.json",
                                    "A001_C042_0420AB")
    assert score == 80  # sidecar_base ("A001_C042") ⊂ clip_name


def test_scorer_no_match_is_zero():
    assert apply.score_name_match("A001_C042", "/x/A001_C042.omc.json",
                                   "completely-different-clip") == 0


def test_scorer_empty_inputs_zero():
    assert apply.score_name_match("", "", "") == 0
    assert apply.score_name_match("", "", "clip") == 0
    assert apply.score_name_match("name", "", "") == 0


# ── Match orchestration ─────────────────────────────────────────────────


def test_one_clip_per_sidecar_no_cross_wiring():
    """If two sidecars both score highest on the same clip, only one wins —
    the second falls to its next-best clip. Prevents rerun drift."""
    sidecars = [
        ("A001_C042", "/x/A001_C042.omc.json"),
        ("A001_C042", "/x/A001_C042_v2.omc.json"),  # duplicate-by-name
    ]
    clips = ["A001_C042_0420AB", "A001_C043_0420CD"]
    matches = apply.match_sidecars_to_clips(sidecars, clips)
    clip_indices_used = {ci for (ci, _) in matches.values()}
    assert len(clip_indices_used) == len(matches)  # unique clips


def test_threshold_rejects_low_scores():
    sidecars = [("nothing-like", "/x/nothing-like.omc.json")]
    clips = ["A001_C042_0420AB"]
    matches = apply.match_sidecars_to_clips(sidecars, clips, threshold=65)
    assert matches == {}


def test_typical_matching_happy_path():
    sidecars = [
        ("A001_C042_0420AB", "/x/A001_C042_0420AB.omc.json"),
        ("A001_C043_0420CD", "/x/A001_C043_0420CD.omc.json"),
    ]
    clips = ["A001_C042_0420AB", "A001_C043_0420CD", "unrelated"]
    matches = apply.match_sidecars_to_clips(sidecars, clips)
    assert set(matches.keys()) == {0, 1}
    assert matches[0][0] == 0 and matches[0][1] == 100
    assert matches[1][0] == 1 and matches[1][1] == 100


# ── Field extraction ────────────────────────────────────────────────────


def _sidecar_doc(*, events=None, locks=None) -> dict:
    events = events if events is not None else [
        {"seq": 1, "action": "create", "ts": "2026-04-23T10:00:00Z",
         "hash": "sha256:11111111abcd", "sig": {"kid": "dwc-dit-01"}},
        {"seq": 2, "action": "lock",   "ts": "2026-04-23T11:00:00Z",
         "hash": "sha256:60aadd4ffa6e13fd", "sig": {"kid": "dwc-post-01"}},
    ]
    locks = locks if locks is not None else [
        {"target": "events", "by": "dwc-post-01", "at": "2026-04-23T11:00:00Z",
         "sig": {"kid": "dwc-post-01"}}]
    return {"Asset": [{"name": "A001_C042_0420AB",
                       "assetFC": {"functionalProperties": {"customData": [
                           {"domain": "dwc.sidecar.events", "value": events},
                           {"domain": "dwc.sidecar.locks",  "value": locks},
                       ]}}}]}


def test_extract_fields_golden(tmp_path):
    sc = tmp_path / "A001_C042_0420AB.omc.json"
    sc.write_text(json.dumps(_sidecar_doc()))
    fields = apply.extract_dwc_fields(sc, signed=True, now=NOW)
    assert fields["DWC_Signed"]       == "true"
    assert fields["DWC_Kid"]          == "dwc-post-01"
    assert fields["DWC_Events"]       == "2"
    assert fields["DWC_Locks"]        == "1"
    assert fields["DWC_LockedBy"]     == "dwc-post-01"
    assert fields["DWC_LastVerified"] == "2026-04-23T14:02:11Z"
    assert fields["DWC_SidecarPath"]  == "A001_C042_0420AB.omc.json"
    assert fields["DWC_ChainHead"]    == "60aadd4ffa6e"


def test_extract_fields_signed_false_flag(tmp_path):
    sc = tmp_path / "x.omc.json"
    sc.write_text(json.dumps(_sidecar_doc()))
    fields = apply.extract_dwc_fields(sc, signed=False, now=NOW)
    assert fields["DWC_Signed"] == "false"


def test_extract_fields_all_eight_keys_present(tmp_path):
    sc = tmp_path / "x.omc.json"
    sc.write_text(json.dumps(_sidecar_doc()))
    fields = apply.extract_dwc_fields(sc, now=NOW)
    assert set(fields) == set(apply.DWC_FIELDS)


# ── Mock Resolve API ────────────────────────────────────────────────────


class _MockMediaPoolItem:
    """Records SetMetadata calls; can be configured to fail specific keys
    to simulate Resolve's silent ``False`` for un-registered fields."""

    def __init__(self, name: str, *, fail_keys: set[str] | None = None):
        self._name = name
        self._fail_keys = fail_keys or set()
        self.calls: list[tuple[str, str]] = []

    def GetName(self) -> str:
        return self._name

    def GetClipProperty(self) -> dict:
        return {"Clip Name": self._name}

    def SetMetadata(self, key: str, value) -> bool:
        self.calls.append((key, value))
        return key not in self._fail_keys


class _MockFolder:
    def __init__(self, clips, subfolders=()):
        self._clips = clips
        self._subs = subfolders

    def GetClipList(self):
        return self._clips

    def GetSubFolderList(self):
        return self._subs


class _MockMediaPool:
    def __init__(self, root):
        self._root = root

    def GetRootFolder(self):
        return self._root


class _MockProject:
    def __init__(self, mp):
        self._mp = mp

    def GetMediaPool(self):
        return self._mp


class _MockProjectManager:
    def __init__(self, project):
        self._project = project

    def GetCurrentProject(self):
        return self._project


class _MockResolve:
    def __init__(self, project_manager):
        self._pm = project_manager

    def GetProjectManager(self):
        return self._pm


# ── Metadata write flow ─────────────────────────────────────────────────


def test_apply_fields_to_clip_all_succeed():
    clip = _MockMediaPoolItem("A001")
    ok, missing = apply.apply_fields_to_clip(clip, {k: "v" for k in apply.DWC_FIELDS})
    assert ok == len(apply.DWC_FIELDS)
    assert missing == []
    assert {call[0] for call in clip.calls} == set(apply.DWC_FIELDS)


def test_apply_fields_to_clip_partial_failure_is_reported():
    """Resolve returns False for fields not in Project Settings. Other
    fields still land; missing names are collected for a one-shot log."""
    clip = _MockMediaPoolItem("A001", fail_keys={"DWC_ChainHead", "DWC_Locks"})
    ok, missing = apply.apply_fields_to_clip(clip, {k: "v" for k in apply.DWC_FIELDS})
    assert ok == len(apply.DWC_FIELDS) - 2
    assert set(missing) == {"DWC_ChainHead", "DWC_Locks"}


def test_apply_fields_to_clip_exception_is_missing():
    class Raising(_MockMediaPoolItem):
        def SetMetadata(self, key, value):
            raise RuntimeError("API surface changed")
    clip = Raising("A001")
    ok, missing = apply.apply_fields_to_clip(clip, {k: "v" for k in apply.DWC_FIELDS})
    assert ok == 0
    assert all("API surface changed" in m for m in missing)


# ── End-to-end run() with mock Resolve ──────────────────────────────────


def test_run_end_to_end_writes_metadata(tmp_path, capsys):
    # Two sidecars in a directory
    sc_a = tmp_path / "A001_C042_0420AB.omc.json"
    sc_b = tmp_path / "A001_C043_0420CD.omc.json"
    for p in (sc_a, sc_b):
        p.write_text(json.dumps(_sidecar_doc()))

    clips = [
        _MockMediaPoolItem("A001_C042_0420AB"),
        _MockMediaPoolItem("A001_C043_0420CD"),
        _MockMediaPoolItem("unrelated-clip"),
    ]
    mock_resolve = _MockResolve(
        _MockProjectManager(_MockProject(_MockMediaPool(_MockFolder(clips)))))

    rc = apply.run(tmp_path, now=NOW, resolve_obj=mock_resolve)
    assert rc == 0
    # The two matching clips got all 8 fields; the unrelated clip got none.
    assert len(clips[0].calls) == len(apply.DWC_FIELDS)
    assert len(clips[1].calls) == len(apply.DWC_FIELDS)
    assert clips[2].calls == []


def test_run_reports_missing_project_settings(tmp_path, capsys):
    sc = tmp_path / "X.omc.json"
    sc.write_text(json.dumps(_sidecar_doc()))
    clip = _MockMediaPoolItem("X", fail_keys={"DWC_ChainHead"})
    mock_resolve = _MockResolve(
        _MockProjectManager(_MockProject(_MockMediaPool(_MockFolder([clip])))))
    rc = apply.run(tmp_path, now=NOW, resolve_obj=mock_resolve)
    assert rc == 0
    err = capsys.readouterr().err
    assert "Metadata & Scene" in err
    assert "DWC_ChainHead" in err


def test_run_without_resolve_errors_cleanly(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("RESOLVE_SCRIPT_LIB", raising=False)
    rc = apply.run(tmp_path, now=NOW, resolve_obj=None)
    assert rc == 2
    assert "DaVinci Resolve" in capsys.readouterr().err


def test_run_with_no_sidecars_returns_zero(tmp_path):
    mock_resolve = _MockResolve(_MockProjectManager(
        _MockProject(_MockMediaPool(_MockFolder([])))))
    rc = apply.run(tmp_path, now=NOW, resolve_obj=mock_resolve)
    assert rc == 0


def test_walk_media_pool_recurses():
    inner_clips = [_MockMediaPoolItem("inner-1"), _MockMediaPoolItem("inner-2")]
    outer_clips = [_MockMediaPoolItem("outer-1")]
    inner = _MockFolder(inner_clips)
    outer = _MockFolder(outer_clips, subfolders=[inner])
    items = apply.walk_media_pool(outer)
    names = [c.GetName() for c in items]
    assert names == ["outer-1", "inner-1", "inner-2"]


# ── ensure_custom_fields helper ─────────────────────────────────────────


def test_ensure_custom_fields_message_lists_all_eight():
    msg = ensure_custom_fields.format_setup_message()
    for f in apply.DWC_FIELDS:
        assert f in msg
    # Points DITs at the right Project Settings submenu
    assert "Metadata & Scene" in msg


def test_ensure_custom_fields_main_prints(capsys):
    rc = ensure_custom_fields.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DWC_Signed" in out
