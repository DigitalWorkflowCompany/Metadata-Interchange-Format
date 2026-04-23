"""Tests for `dwc_sidecar.ale_emitter`.

Plan §1.9 matrix — every test drives ``update_ale`` / ``extract_row_from_sidecar``
directly without instantiating a ``Watcher`` (plan testability review #4).
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from dwc_sidecar import ale_emitter


NOW = datetime(2026, 4, 23, 14, 2, 11, tzinfo=timezone.utc)


def _sidecar_doc(*, name: str = "A001_C042_0420AB",
                 events: list[dict] | None = None,
                 locks: list[dict] | None = None) -> dict:
    events = events if events is not None else [
        {"seq": 1, "action": "create", "ts": "2026-04-23T10:00:00Z",
         "hash": "sha256:111111111111abcdef", "sig": {"kid": "dwc-dit-01"}},
        {"seq": 2, "action": "lock",   "ts": "2026-04-23T11:00:00Z",
         "hash": "sha256:60aadd4ffa6e13fd", "sig": {"kid": "dwc-post-01"}},
    ]
    locks = locks if locks is not None else [
        {"target": "events", "by": "dwc-post-01", "at": "2026-04-23T11:00:00Z",
         "sig": {"kid": "dwc-post-01"}}]
    return {"Asset": [{
        "name": name,
        "assetFC": {"functionalProperties": {"customData": [
            {"domain": "dwc.sidecar.events", "value": events},
            {"domain": "dwc.sidecar.locks",  "value": locks},
        ]}},
    }]}


def _write_sidecar(tmp_path: Path, name: str, **kw) -> Path:
    doc = _sidecar_doc(name=name, **kw)
    p = tmp_path / f"{name}.omc.json"
    p.write_text(json.dumps(doc))
    return p


# ── Pure-function: row extraction ────────────────────────────────────────


def test_extract_row_has_all_twelve_columns(tmp_path):
    sc = _write_sidecar(tmp_path, "A001_C042_0420AB")
    row = ale_emitter.extract_row_from_sidecar(sc, now=NOW, signed=True,
                                                ale_dir=tmp_path)
    assert set(row) == set(ale_emitter.COLUMNS)


def test_extract_row_golden_values(tmp_path):
    sc = _write_sidecar(tmp_path, "A001_C042_0420AB")
    row = ale_emitter.extract_row_from_sidecar(sc, now=NOW, signed=True,
                                                ale_dir=tmp_path)
    assert row["Name"]              == "A001_C042_0420AB"
    assert row["Tape"]              == "A001"
    assert row["DWC_Signed"]        == "true"
    assert row["DWC_Kid"]           == "dwc-post-01"
    assert row["DWC_Events"]        == "2"
    assert row["DWC_Locks"]         == "1"
    assert row["DWC_LockedBy"]      == "dwc-post-01"
    assert row["DWC_LastVerified"]  == "2026-04-23T14:02:11Z"
    assert row["DWC_SidecarPath"]   == "A001_C042_0420AB.omc.json"
    # Chain head = tip event's hash, alg-prefix stripped, first 12 hex
    assert row["DWC_ChainHead"]     == "60aadd4ffa6e"


def test_extract_row_signed_false_flag(tmp_path):
    sc = _write_sidecar(tmp_path, "A001_C042_0420AB")
    row = ale_emitter.extract_row_from_sidecar(sc, now=NOW, signed=False,
                                                ale_dir=tmp_path)
    assert row["DWC_Signed"] == "false"


def test_extract_row_empty_events_and_locks(tmp_path):
    sc = _write_sidecar(tmp_path, "A001_C042_0420AB", events=[], locks=[])
    row = ale_emitter.extract_row_from_sidecar(sc, now=NOW, signed=True,
                                                ale_dir=tmp_path)
    assert row["DWC_Events"]     == "0"
    assert row["DWC_Locks"]      == "0"
    assert row["DWC_Kid"]        == ""
    assert row["DWC_LockedBy"]   == ""
    assert row["DWC_ChainHead"]  == ""


def test_tape_regex_matches_a_cam_prefix():
    assert ale_emitter.tape_from_name("A001_C042_0420AB")   == "A001"
    assert ale_emitter.tape_from_name("A002C001_260115")    == "A002"
    assert ale_emitter.tape_from_name("A012_anything")      == "A012"
    assert ale_emitter.tape_from_name("no-prefix")          == ""
    assert ale_emitter.tape_from_name("")                   == ""


def test_ale_path_for_day_uses_utc_date(tmp_path):
    p = ale_emitter.ale_path_for_day(tmp_path, now=NOW)
    assert p == tmp_path / "dwc-columns-2026-04-23.ale"


# ── Round-trip ───────────────────────────────────────────────────────────


def test_round_trip_preserves_all_dwc_columns(tmp_path):
    sc = _write_sidecar(tmp_path, "A001_C042_0420AB")
    ale_path = tmp_path / "dwc-columns.ale"
    src_row = ale_emitter.extract_row_from_sidecar(sc, now=NOW, signed=True,
                                                    ale_dir=tmp_path)
    ale_emitter.update_ale(ale_path, src_row, now=NOW)

    parsed = ale_emitter.parse_ale(ale_path.read_text(encoding="utf-8"))
    assert len(parsed) == 1
    for col in ale_emitter.DWC_COLUMNS:
        assert parsed[0][col] == src_row[col], f"{col} drifted in round-trip"


# ── Dedup semantics (plan edge-cases review #5) ─────────────────────────


def test_dedup_on_sidecar_path_later_row_wins(tmp_path):
    sc = _write_sidecar(tmp_path, "A001_C042_0420AB")
    ale_path = tmp_path / "dwc-columns.ale"

    row_a = ale_emitter.extract_row_from_sidecar(sc, now=NOW, signed=True,
                                                  ale_dir=tmp_path)
    row_a["DWC_Events"]     = "2"
    row_a["DWC_LastVerified"] = "2026-04-23T10:00:00Z"
    ale_emitter.update_ale(ale_path, row_a, now=NOW)

    # Later emission for the same sidecar path — different seq / later ts.
    row_b = dict(row_a)
    row_b["DWC_Events"]     = "5"
    row_b["DWC_LastVerified"] = "2026-04-23T14:00:00Z"
    ale_emitter.update_ale(ale_path, row_b, now=NOW)

    parsed = ale_emitter.parse_ale(ale_path.read_text(encoding="utf-8"))
    assert len(parsed) == 1
    assert parsed[0]["DWC_Events"]       == "5"
    assert parsed[0]["DWC_LastVerified"] == "2026-04-23T14:00:00Z"


def test_dedup_does_not_merge_same_name_different_path(tmp_path):
    """Multi-roll shoots reset C-numbers per reel — two different clips
    can legitimately share Name but never share SidecarPath (plan §1.6)."""
    (tmp_path / "reel-A").mkdir()
    (tmp_path / "reel-B").mkdir()
    sc_a = _write_sidecar(tmp_path / "reel-A", "shared-name")
    sc_b = _write_sidecar(tmp_path / "reel-B", "shared-name")
    ale_path = tmp_path / "dwc-columns.ale"

    row_a = ale_emitter.extract_row_from_sidecar(sc_a, now=NOW, signed=True,
                                                  ale_dir=tmp_path)
    row_b = ale_emitter.extract_row_from_sidecar(sc_b, now=NOW, signed=True,
                                                  ale_dir=tmp_path)
    assert row_a["Name"]             == row_b["Name"]
    assert row_a["DWC_SidecarPath"]  != row_b["DWC_SidecarPath"]

    ale_emitter.update_ale(ale_path, row_a, now=NOW)
    ale_emitter.update_ale(ale_path, row_b, now=NOW)

    parsed = ale_emitter.parse_ale(ale_path.read_text(encoding="utf-8"))
    assert len(parsed) == 2


# ── Format fidelity ──────────────────────────────────────────────────────


def test_line_endings_are_crlf(tmp_path):
    sc = _write_sidecar(tmp_path, "A001_C042_0420AB")
    ale_path = tmp_path / "dwc-columns.ale"
    row = ale_emitter.extract_row_from_sidecar(sc, now=NOW, signed=True,
                                                ale_dir=tmp_path)
    ale_emitter.update_ale(ale_path, row, now=NOW)
    raw = ale_path.read_bytes()
    # CRLF between every line, including the last one
    assert b"\r\n" in raw
    # No bare LFs (every LF should be preceded by CR)
    for idx, b in enumerate(raw):
        if b == 0x0A:
            assert raw[idx - 1] == 0x0D, f"bare LF at offset {idx}"


def test_tab_delimiter_survives_space_values(tmp_path):
    """Avid ALE rules: tab separates columns. Space inside a value is fine
    and must not be treated as a delimiter."""
    row = {c: "" for c in ale_emitter.COLUMNS}
    row["Name"] = "clip with spaces"
    row["DWC_Kid"] = "dwc dit with spaces"
    row["DWC_SidecarPath"] = "a.omc.json"
    ale_path = tmp_path / "out.ale"
    ale_emitter.update_ale(ale_path, row, now=NOW)

    parsed = ale_emitter.parse_ale(ale_path.read_text(encoding="utf-8"))
    assert parsed[0]["Name"]    == "clip with spaces"
    assert parsed[0]["DWC_Kid"] == "dwc dit with spaces"


def test_tab_in_value_is_sanitized(tmp_path):
    """A stray tab in a value would shift every subsequent column."""
    row = {c: "" for c in ale_emitter.COLUMNS}
    row["Name"] = "evil\tname"
    row["DWC_SidecarPath"] = "a.omc.json"
    ale_path = tmp_path / "out.ale"
    ale_emitter.update_ale(ale_path, row, now=NOW)

    parsed = ale_emitter.parse_ale(ale_path.read_text(encoding="utf-8"))
    assert len(parsed) == 1
    # Tab replaced with space; column alignment intact
    assert "\t" not in parsed[0]["Name"]
    assert "evil" in parsed[0]["Name"] and "name" in parsed[0]["Name"]


def test_crlf_in_value_is_sanitized(tmp_path):
    """Embedded line ending would split the row into phantom rows."""
    row = {c: "" for c in ale_emitter.COLUMNS}
    row["Name"] = "first\r\nsecond"
    row["DWC_SidecarPath"] = "a.omc.json"
    ale_path = tmp_path / "out.ale"
    ale_emitter.update_ale(ale_path, row, now=NOW)

    parsed = ale_emitter.parse_ale(ale_path.read_text(encoding="utf-8"))
    assert len(parsed) == 1
    assert "\n" not in parsed[0]["Name"]
    assert "\r" not in parsed[0]["Name"]


def test_unicode_clipname_round_trip(tmp_path):
    """DITs on French/Spanish shoots often have accented characters in slate
    names. UTF-8 round-trip must preserve them."""
    sc = _write_sidecar(tmp_path, "A002_Café_0420EF")
    ale_path = tmp_path / "out.ale"
    row = ale_emitter.extract_row_from_sidecar(sc, now=NOW, signed=True,
                                                ale_dir=tmp_path)
    ale_emitter.update_ale(ale_path, row, now=NOW)

    parsed = ale_emitter.parse_ale(ale_path.read_text(encoding="utf-8"))
    assert parsed[0]["Name"] == "A002_Café_0420EF"


# ── Crash-safety (plan edge-cases review #1) ────────────────────────────


def test_stale_tmp_is_deleted_before_read(tmp_path):
    """A leftover .tmp from a prior crash must not be read as input data."""
    ale_path = tmp_path / "out.ale"
    tmp_file = ale_path.with_suffix(".ale.tmp")

    # Seed the production file with a row we want preserved
    initial = {c: "" for c in ale_emitter.COLUMNS}
    initial["DWC_SidecarPath"] = "real.omc.json"
    initial["Name"]            = "production-row"
    ale_emitter.update_ale(ale_path, initial, now=NOW)

    # Simulate a crash artifact: a .tmp file with garbage that, if read as
    # input, would add a misleading row to the next rewrite
    tmp_file.write_text("garbage\nrow\n")
    assert tmp_file.exists()

    next_row = {c: "" for c in ale_emitter.COLUMNS}
    next_row["DWC_SidecarPath"] = "new.omc.json"
    next_row["Name"] = "new-row"
    ale_emitter.update_ale(ale_path, next_row, now=NOW)

    # The .tmp was deleted before the read step, so rewrite = real content only
    parsed = ale_emitter.parse_ale(ale_path.read_text(encoding="utf-8"))
    names = sorted(r["Name"] for r in parsed)
    assert names == ["new-row", "production-row"]
    # .tmp is consumed by os.replace
    assert not tmp_file.exists()


# ── Watch integration: ALE I/O failure must not propagate ───────────────


def test_watcher_swallows_ale_exception(tmp_path, monkeypatch, capsys):
    """Plan §1.6: ALE rewrite failure logs WARN, never blocks sidecar work."""
    from dwc_sidecar import watch

    def boom(*_a, **_kw):
        raise OSError("disk full")
    monkeypatch.setattr(watch, "update_ale", boom)

    # Construct a minimal Watcher-shaped object without going through __init__.
    w = watch.Watcher.__new__(watch.Watcher)
    w.emit_ale = True
    w.out_dir  = tmp_path
    sc = _write_sidecar(tmp_path, "A001_C042_0420AB")

    # Must not raise
    w._emit_ale_row(sc, signed=True)
    # …but logs a WARN line
    out = capsys.readouterr().out
    assert "disk full" in out
