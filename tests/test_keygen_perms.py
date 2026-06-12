"""keys.priv.json holds plaintext Ed25519 private keys — it must never be
group/world-readable (post houses run shared NAS / render nodes)."""
import json
import stat

from dwc_sidecar.keygen import _keygen_local


def test_keygen_local_writes_0600(tmp_path):
    path = tmp_path / "keys.priv.json"
    pub = _keygen_local("dwc-test-01", path)
    assert len(pub) == 32
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_keygen_local_appends_and_keeps_0600(tmp_path):
    path = tmp_path / "keys.priv.json"
    path.write_text(json.dumps({"dwc-old-01": "AAAA"}))
    path.chmod(0o644)
    _keygen_local("dwc-test-02", path)
    bundle = json.loads(path.read_text())
    assert set(bundle) == {"dwc-old-01", "dwc-test-02"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
