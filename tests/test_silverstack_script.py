"""Tests for the Silverstack Lua script via a subprocess harness.

The script runs inside Silverstack's embedded Lua 5.5 runtime in production.
Here we spawn a system ``lua`` binary (5.1+), stub ``videoClip`` /
``resource`` with the methods the script calls, invoke ``onStampVideo``,
and read back which ``setCustom1..setCustom6`` calls were issued and with
what values.

Skipped automatically if ``lua`` isn't on ``PATH`` — Pomfort's runtime
isn't installable outside Silverstack, so the only way to exercise the
script in CI is with a standalone Lua interpreter.
"""
import json
import shutil
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest


_LUA = shutil.which("lua")
pytestmark = pytest.mark.skipif(_LUA is None, reason="lua not installed")
LUA: str = _LUA or ""  # only referenced after the skip gate

SCRIPT = (Path(__file__).resolve().parent.parent
          / "src" / "dwc_sidecar" / "integrations" / "silverstack"
          / "apply_dwc_metadata.lua")


def _write_harness(tmp_path: Path) -> Path:
    """A minimal Lua harness: stubs the Silverstack-side API, loads the
    real script, calls onStampVideo with whatever args the test passes on
    argv, and prints captured setCustomN calls as a parseable key=value
    block. The emitted block is delimited so accidental print() calls in
    the real script can't corrupt the readback."""
    harness = tmp_path / "harness.lua"
    harness.write_text(dedent(r"""
        -- arg[1]: path to apply_dwc_metadata.lua
        -- arg[2]: clip path passed to resource:path()
        -- arg[3]: empty or "<missing>" to make the harness omit resource.path

        local script_path = arg[1]
        local clip_path   = arg[2]
        local path_mode   = arg[3] or "present"

        local calls = {}
        local meta = {}
        function meta.setCustom1(self, v) calls[#calls+1] = "Custom1=" .. tostring(v) end
        function meta.setCustom2(self, v) calls[#calls+1] = "Custom2=" .. tostring(v) end
        function meta.setCustom3(self, v) calls[#calls+1] = "Custom3=" .. tostring(v) end
        function meta.setCustom4(self, v) calls[#calls+1] = "Custom4=" .. tostring(v) end
        function meta.setCustom5(self, v) calls[#calls+1] = "Custom5=" .. tostring(v) end
        function meta.setCustom6(self, v) calls[#calls+1] = "Custom6=" .. tostring(v) end

        local asset = {}
        function asset:metadata() return meta end

        local resource = {}
        if path_mode ~= "missing" then
            function resource:path() return clip_path end
        end

        -- Load the real script; its top-level code wires hooks as globals
        dofile(script_path)

        -- The real onStampVideo may print() diagnostics; fence our output
        print("<<BEGIN-CALLS>>")
        local _ok, err = pcall(onStampVideo, asset, 0, resource)
        if not _ok then print("ERROR=" .. tostring(err)) end
        for _, c in ipairs(calls) do print(c) end
        print("<<END-CALLS>>")
    """))
    return harness


def _run_harness(tmp_path: Path, clip_path: str,
                 path_mode: str = "present") -> dict[str, str]:
    harness = _write_harness(tmp_path)
    r = subprocess.run(
        [LUA, str(harness), str(SCRIPT), clip_path, path_mode],
        capture_output=True, text=True, timeout=10,
    )
    assert r.returncode == 0, f"lua harness failed: {r.stderr}\n{r.stdout}"
    out = r.stdout
    assert "<<BEGIN-CALLS>>" in out and "<<END-CALLS>>" in out
    body = out.split("<<BEGIN-CALLS>>", 1)[1].split("<<END-CALLS>>", 1)[0]
    pairs: dict[str, str] = {}
    for line in body.strip().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            pairs[k] = v
    return pairs


def _write_sidecar(clip_path: Path, *,
                   events: list[dict] | None = None,
                   locks: list[dict] | None = None) -> Path:
    events = events if events is not None else [
        {"seq": 1, "action": "create", "ts": "2026-04-23T10:00:00Z",
         "hash": "sha256:11111111abcdef", "sig": {"kid": "dwc-dit-01"}},
        {"seq": 2, "action": "lock",   "ts": "2026-04-23T11:00:00Z",
         "hash": "sha256:60aadd4ffa6e13", "sig": {"kid": "dwc-post-01"}},
    ]
    locks = locks if locks is not None else [
        {"target": "events", "by": "dwc-post-01", "at": "2026-04-23T11:00:00Z",
         "sig": {"kid": "dwc-post-01"}}]
    doc = {"Asset": [{"name": clip_path.stem,
                      "assetFC": {"functionalProperties": {"customData": [
                          {"domain": "dwc.sidecar.events", "value": events},
                          {"domain": "dwc.sidecar.locks",  "value": locks},
                      ]}}}]}
    sidecar = clip_path.with_suffix(".omc.json")
    sidecar.write_text(json.dumps(doc))
    return sidecar


# ── Happy path ──────────────────────────────────────────────────────────


def test_happy_path_writes_all_six_custom_slots(tmp_path):
    clip = tmp_path / "A001_C042_0420AB.ari"
    clip.write_text("")  # placeholder — the script only reads the sidecar
    _write_sidecar(clip)

    result = _run_harness(tmp_path, str(clip))
    assert result["Custom1"] == "true"                 # DWC_Signed
    assert result["Custom2"] == "dwc-post-01"          # DWC_Kid (latest event)
    assert result["Custom3"] == "2"                    # DWC_Events
    assert result["Custom4"] == "dwc-post-01"          # DWC_LockedBy
    # Custom5 is a timestamp generated inside the Lua script — just check shape
    assert "T" in result["Custom5"] and result["Custom5"].endswith("Z")
    assert result["Custom6"] == "60aadd4ffa6e"         # DWC_ChainHead


def test_chain_head_strips_alg_prefix(tmp_path):
    """sha256:<hex> → bare <hex>[:12]. Same rule as the ALE emitter."""
    clip = tmp_path / "X.ari"; clip.write_text("")
    _write_sidecar(clip, events=[
        {"seq": 1, "action": "create", "ts": "2026-04-23T10:00:00Z",
         "hash": "sha256:3f0b9e41cc07deadbeef",
         "sig": {"kid": "dwc-dit-01"}},
    ], locks=[])
    result = _run_harness(tmp_path, str(clip))
    assert result["Custom6"] == "3f0b9e41cc07"


def test_locked_by_empty_when_no_lock_event(tmp_path):
    clip = tmp_path / "NoLock.ari"; clip.write_text("")
    _write_sidecar(clip, events=[
        {"seq": 1, "action": "create", "ts": "2026-04-23T10:00:00Z",
         "hash": "sha256:abc", "sig": {"kid": "dwc-dit-01"}},
    ], locks=[])
    result = _run_harness(tmp_path, str(clip))
    assert result["Custom4"] == ""


# ── Graceful degradation ────────────────────────────────────────────────


def test_missing_sidecar_is_silent_noop(tmp_path):
    """A clip ingested without a sidecar must not write any Custom fields."""
    clip = tmp_path / "NoSidecar.ari"; clip.write_text("")
    result = _run_harness(tmp_path, str(clip))
    for slot in ("Custom1", "Custom2", "Custom3",
                 "Custom4", "Custom5", "Custom6"):
        assert slot not in result


def test_malformed_json_is_logged_not_crashed(tmp_path):
    clip = tmp_path / "BadJSON.ari"; clip.write_text("")
    (clip.with_suffix(".omc.json")).write_text("{not json at all")
    result = _run_harness(tmp_path, str(clip))
    # No setCustomN calls — but no ERROR from pcall either
    assert "ERROR" not in result
    for slot in ("Custom1", "Custom2", "Custom3",
                 "Custom4", "Custom5", "Custom6"):
        assert slot not in result


def test_resource_without_path_method_is_silent(tmp_path):
    """If a future Silverstack changes the resource API, the script must
    log-and-skip rather than crash the ingest."""
    clip = tmp_path / "X.ari"; clip.write_text("")
    _write_sidecar(clip)
    result = _run_harness(tmp_path, str(clip), path_mode="missing")
    for slot in ("Custom1", "Custom2", "Custom3",
                 "Custom4", "Custom5", "Custom6"):
        assert slot not in result


# ── Unicode + escape handling in the inline JSON parser ─────────────────


def test_unicode_clipname_round_trip(tmp_path):
    clip = tmp_path / "A002_Café_0420EF.ari"; clip.write_text("")
    _write_sidecar(clip, events=[
        {"seq": 1, "action": "create", "ts": "2026-04-23T10:00:00Z",
         "hash": "sha256:deadbeef", "sig": {"kid": "dwc-café-01"}},
    ], locks=[])
    result = _run_harness(tmp_path, str(clip))
    # "dwc-café-01" must survive the inline parser's string handling
    assert result["Custom2"] == "dwc-café-01"
