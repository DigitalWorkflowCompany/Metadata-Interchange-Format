"""Append-to-chain machinery (Phase 1.1).

append_event must: extend the chain with a correct seq/prevHash, re-sign the
head, keep the sidecar validating end-to-end — and refuse to extend a chain
that is already broken (otherwise the CLI launders a tampered sidecar by
appending a fresh signed tip on top of a broken prefix)."""
import json
import shutil
from pathlib import Path

import pytest

from dwc_sidecar.append import (
    append_event, load_sidecar_asset, save_sidecar, verify_asset_chain,
)
from dwc_sidecar.signers.jsonfile import JsonFileSigner
from dwc_sidecar.validate import validate_as_json

REPO_ROOT = Path(__file__).resolve().parent.parent
CLIP      = REPO_ROOT / "example-clip.omc.json"
REEL      = REPO_ROOT / "example-reel.omc.json"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A repo-rooted CWD with the demo keys/keyring available."""
    monkeypatch.chdir(REPO_ROOT)
    return tmp_path


def _signer(kid="dwc-dit-01"):
    return JsonFileSigner(kid, REPO_ROOT / "keys.priv.json")


def test_append_event_extends_chain_and_revalidates(workspace):
    dst = workspace / "clip.omc.json"
    shutil.copyfile(CLIP, dst)
    doc, asset = load_sidecar_asset(dst, clip_uuid="0190a5e2-7c3f-7b4a-a1b2-3c4d5e6f7890")

    ev = append_event(asset, {
        "actor": {"id": "urn:email:dit@the-dwc.com", "role": "DIT"},
        "tool":  {"name": "pytest", "version": "0"},
        "action": "verify",
        "target": "urn:uuid:0190a5e2-7c3f-7b4a-a1b2-3c4d5e6f7890",
    }, _signer("dwc-dit-01"))
    save_sidecar(dst, doc)

    # The stub OCF chain now ends at seq 4 (2-of-2 threshold lock), so the
    # appended event is seq 5.
    assert ev["seq"] == 5
    assert ev["prevHash"] is not None
    report = validate_as_json(dst, base_dir=REPO_ROOT)
    assert report["errors"] == 0, report["summary"]

    # Head was rewritten to the new tip.
    head = next(e["value"] for e in
                asset["assetFC"]["functionalProperties"]["customData"]
                if e["domain"] == "dwc.sidecar.head")
    assert head["seq"] == 5
    assert head["tipHash"] == ev["hash"]


def test_append_refuses_broken_chain(workspace):
    dst = workspace / "clip.omc.json"
    doc = json.loads(CLIP.read_text())
    # Corrupt the first event's hash so the chain no longer links.
    for asset in doc["Asset"]:
        for cd in asset["assetFC"]["functionalProperties"]["customData"]:
            if cd["domain"] == "dwc.sidecar.events" and cd["value"]:
                cd["value"][0]["hash"] = "sha256:" + "0" * 64
                break
        break
    dst.write_text(json.dumps(doc))
    _, asset = load_sidecar_asset(dst, clip_uuid="0190a5e2-7c3f-7b4a-a1b2-3c4d5e6f7890")

    with pytest.raises(ValueError, match="refusing to append"):
        append_event(asset, {
            "actor": {"id": "urn:email:dit@the-dwc.com", "role": "DIT"},
            "tool":  {"name": "pytest", "version": "0"},
            "action": "verify",
            "target": "urn:uuid:0190a5e2-7c3f-7b4a-a1b2-3c4d5e6f7890",
        }, _signer())


def test_load_multi_asset_requires_clip_uuid(workspace):
    with pytest.raises(ValueError, match="clip-uuid"):
        load_sidecar_asset(REEL)


def test_load_multi_asset_selects_by_clip_uuid(workspace):
    _, asset = load_sidecar_asset(REEL, clip_uuid="0190a5e2-7c3f-7b4a-a1b2-3c4d5e6f7891")
    ids = {i["identifierValue"] for i in asset["identifier"]
           if i["identifierScope"] == "dwc:clip-uuid"}
    assert "0190a5e2-7c3f-7b4a-a1b2-3c4d5e6f7891" in ids


def test_verify_asset_chain_passes_on_clean_example(workspace):
    _, asset = load_sidecar_asset(CLIP, clip_uuid="0190a5e2-7c3f-7b4a-a1b2-3c4d5e6f7890")
    verify_asset_chain(asset)  # must not raise
