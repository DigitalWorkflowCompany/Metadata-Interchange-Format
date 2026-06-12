"""sign-example must cover every example file and every Asset — the
key-rotation procedure broke twice because example-reel (and example-clip's
second Asset) kept stale signatures after a rotation.
"""
import json
import shutil
import stat
from pathlib import Path

import pytest

from dwc_sidecar import sign_example
from dwc_sidecar.canonical import canonical_bytes, verify_event, load_pubkey_b64

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def demo_dir(tmp_path, monkeypatch):
    for name in ("example-clip.omc.json", "example-reel.omc.json"):
        shutil.copyfile(REPO_ROOT / name, tmp_path / name)
    monkeypatch.chdir(tmp_path)   # sign_example resolves its files via CWD
    return tmp_path


def _assets_with_events(doc):
    out = []
    for asset in sign_example._iter_assets(doc):
        cd = ((asset.get("assetFC") or {}).get("functionalProperties") or {}).get("customData") or []
        if any(e.get("domain") == "dwc.sidecar.events" for e in cd):
            out.append((asset, {e["domain"]: e for e in cd}))
    return out


def test_signs_every_example_and_every_asset(demo_dir):
    sign_example.main()

    keyring = json.loads((demo_dir / "keyring.json").read_text())["keys"]
    pubs = {kid: load_pubkey_b64(v["publicKey"]) for kid, v in keyring.items()}

    total_assets = 0
    for name in ("example-clip.omc.json", "example-reel.omc.json"):
        doc = json.loads((demo_dir / name).read_text())
        assets = _assets_with_events(doc)
        assert assets, f"{name}: no signed assets found"
        total_assets += len(assets)
        for asset, by_domain in assets:
            events = by_domain["dwc.sidecar.events"]["value"]
            prev = None
            for ev in events:
                ok, reason = verify_event(ev, pubs[ev["sig"]["kid"]])
                assert ok, f"{name}: seq={ev['seq']}: {reason}"
                assert ev["prevHash"] == prev
                prev = ev["hash"]
            # v0.2 contract: head present and pointing at the tip
            head = by_domain["dwc.sidecar.head"]["value"]
            assert head["seq"] == events[-1]["seq"]
            assert head["tipHash"] == events[-1]["hash"]
            # create event commits to every artifact
            arts = by_domain["dwc.sidecar.artifacts"]["value"]
            assert {c["id"] for c in events[0]["artifacts"]} == {a["id"] for a in arts}
            # locks carry real signatures — single-sig (sig) or m-of-n (sigs),
            # both over JCS(lock minus sig/sigs).
            for lk in by_domain["dwc.sidecar.locks"]["value"]:
                import base64
                sigs = lk["sigs"] if "sigs" in lk else [lk["sig"]]
                for s in sigs:
                    pubs[s["kid"]].verify(
                        base64.b64decode(s["value"]), canonical_bytes(lk))
    # clip has 2 assets, reel has 2 nested assets
    assert total_assets == 4


def test_privkeys_written_0600(demo_dir):
    sign_example.main()
    mode = stat.S_IMODE((demo_dir / "keys.priv.json").stat().st_mode)
    assert mode == 0o600
