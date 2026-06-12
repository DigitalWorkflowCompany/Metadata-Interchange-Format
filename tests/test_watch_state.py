"""watch.py state machine: collision resolution, state persistence, and the
processed-MHL re-hash short-circuit. None of this was covered before — the
review found the short-circuit didn't exist (processed_sha written, never
read) and that a third MHL could silently reclaim a disputed clean filename.
"""
import json
from pathlib import Path

import pytest

import dwc_sidecar.watch as watch
from dwc_sidecar.watch import Watcher, STATE


def _doc_with_ci(value: str, alg: str = "sha256") -> dict:
    return {"Asset": [{"assetFC": {"functionalProperties": {"customData": [
        {"domain": "dwc.sidecar.artifacts",
         "value": [{"role": "clip-integrity",
                    "hash": {"alg": alg, "value": value}}]}
    ]}}}]}


@pytest.fixture
def watcher(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)   # STATE is CWD-relative
    root = tmp_path / "root"; root.mkdir()
    out  = tmp_path / "out";  out.mkdir()
    return Watcher(root, out, None, None, None, signer=None,
                   poll_interval=0.01, stable_seconds=0,
                   validate_each=False, quarantine_dir=tmp_path / "quarantine",
                   emit_ale=False)


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def _target(watcher) -> Path:
    return watcher.out_dir / "CLIP001.omc.json"


def test_first_write_uses_clean_name(watcher):
    out, action = watcher._resolve_collision(_target(watcher), _doc_with_ci(HASH_A), "m1")
    assert (out, action) == (_target(watcher), "write")


def test_refresh_when_same_hash(watcher):
    t = _target(watcher)
    t.write_text(json.dumps(_doc_with_ci(HASH_A)))
    out, action = watcher._resolve_collision(t, _doc_with_ci(HASH_A), "m2")
    assert out is None and action == "refresh"
    assert watcher._stats["refreshed"] == 1


def test_conflict_suffixes_both_versions(watcher):
    t = _target(watcher)
    t.write_text(json.dumps(_doc_with_ci(HASH_A)))
    out, action = watcher._resolve_collision(t, _doc_with_ci(HASH_B), "m2")
    assert action == "conflict"
    assert not t.exists(), "clean filename must be retired on conflict"
    assert (watcher.out_dir / f"CLIP001.{HASH_A[:8]}.omc.json").exists()
    assert out.name == f"CLIP001.{HASH_B[:8]}.omc.json"


def test_third_mhl_cannot_reclaim_clean_name(watcher):
    """After a conflict the clean name stays retired: a third, different
    version must get its own suffix, not the undisputed-looking filename."""
    t = _target(watcher)
    t.write_text(json.dumps(_doc_with_ci(HASH_A)))
    out_b, _ = watcher._resolve_collision(t, _doc_with_ci(HASH_B), "m2")
    out_b.write_text(json.dumps(_doc_with_ci(HASH_B)))

    out_c, action = watcher._resolve_collision(t, _doc_with_ci(HASH_C), "m3")
    assert action == "conflict"
    assert out_c != t
    assert out_c.name == f"CLIP001.{HASH_C[:8]}.omc.json"

    # …and a re-offer of an already-known version refreshes, not rewrites
    out_a, action_a = watcher._resolve_collision(t, _doc_with_ci(HASH_A), "m4")
    assert out_a is None and action_a == "refresh"


def test_suffix_prefix_collision_lengthens(watcher):
    """Two different full hashes sharing the first 8 chars must not map to
    the same file (CLAUDE.md convention #3)."""
    shared = "deadbeef"
    h1 = shared + "1" * 56
    h2 = shared + "2" * 56
    t = _target(watcher)
    p1 = watcher._suffixed_path(t, "CLIP001", ("sha256", h1))
    p1.write_text(json.dumps(_doc_with_ci(h1)))
    p2 = watcher._suffixed_path(t, "CLIP001", ("sha256", h2))
    assert p1 != p2
    assert p2.name == f"CLIP001.{h2[:16]}.omc.json"


def test_state_round_trip(watcher, tmp_path, monkeypatch):
    watcher._processed = {"sha1", "sha2"}
    watcher._record_emission("CLIP001", _target(watcher), "signed")
    watcher._save_state()
    assert STATE.exists()

    w2 = Watcher(watcher.root, watcher.out_dir, None, None, None, signer=None,
                 poll_interval=0.01, stable_seconds=0,
                 validate_each=False, quarantine_dir=tmp_path / "q",
                 emit_ale=False)
    assert w2._processed == {"sha1", "sha2"}
    assert w2._emitted[-1]["clipName"] == "CLIP001"


def test_corrupt_state_warns_and_resets(watcher, tmp_path, capsys):
    STATE.write_text("{ not json")
    w2 = Watcher(watcher.root, watcher.out_dir, None, None, None, signer=None,
                 poll_interval=0.01, stable_seconds=0,
                 validate_each=False, quarantine_dir=tmp_path / "q",
                 emit_ale=False)
    assert w2._processed == set()
    assert "unreadable" in capsys.readouterr().out


def test_atomic_write_leaves_no_tmp(tmp_path):
    p = tmp_path / "f.json"
    watch._atomic_write_text(p, "hello")
    assert p.read_text() == "hello"
    assert list(tmp_path.glob("*.tmp")) == []


def test_processed_mhl_not_rehashed_every_poll(watcher, monkeypatch):
    """An already-processed, unchanged MHL must be skipped without a SHA-256
    pass — the old loop re-hashed every stable MHL on every poll."""
    mhl = watcher.root / "roll.mhl"
    mhl.write_text("Version: 2.0.0\nHashes: []\n")
    real_sha = watch._sha256(mhl)
    watcher._processed.add(real_sha)

    calls = {"n": 0}
    def counting_sha(path):
        calls["n"] += 1
        return real_sha
    monkeypatch.setattr(watch, "_sha256", counting_sha)

    watcher._scan_once()              # DETECT — no hash yet
    watcher._scan_once()              # stable → hash once → marked processed
    assert calls["n"] == 1
    watcher._scan_once()              # short-circuit: no further hashing
    watcher._scan_once()
    assert calls["n"] == 1
