"""Tests for `dwc doctor`.

Every check is exercised with a ``tmp_path``-based synthetic filesystem.
Real signer construction, real keychain access, and real HTTP are all
avoided via the ``signer_factory`` / ``fetch_url`` injection seams —
doctor's promise is to be a call-time audit, so the tests must not depend
on the caller's production environment.
"""
import base64
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from dwc_sidecar import doctor


NOW = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)


# ── Helpers ──────────────────────────────────────────────────────────────


def _pub_b64(priv: Ed25519PrivateKey) -> str:
    raw = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def _make_keyring(tmp_path: Path, *, kids: list[str],
                  valid_from: str = "2020-01-01T00:00:00Z",
                  valid_until: str = "2030-01-01T00:00:00Z",
                  revoked: dict[str, str] | None = None) -> tuple[Path, dict[str, Ed25519PrivateKey]]:
    revoked = revoked or {}
    privs: dict[str, Ed25519PrivateKey] = {}
    entries = {}
    for kid in kids:
        priv = Ed25519PrivateKey.generate()
        privs[kid] = priv
        entries[kid] = {
            "publicKey":        _pub_b64(priv),
            "validFrom":        valid_from,
            "validUntil":       valid_until,
            "revokedAt":        revoked.get(kid),
            "revocationReason": None,
        }
    path = tmp_path / "keyring.json"
    path.write_text(json.dumps({"keys": entries}, indent=2))
    return path, privs


def _write_sidecar(tmp_path: Path, name: str, *,
                   events_kids: list[str] | None = None,
                   artifacts_algs: list[str] | None = None,
                   valid: bool = True) -> Path:
    events = [{"seq": i + 1, "sig": {"kid": k}, "ts": "2026-04-23T12:00:00Z"}
              for i, k in enumerate(events_kids or [])]
    artifacts = [{"kind": "generic", "path": "x",
                  "hash": {"alg": alg, "value": "0" * 16}}
                 for alg in (artifacts_algs or [])]
    custom: list[dict] = []
    if events:
        custom.append({"domain": "dwc.sidecar.events", "value": events})
    if artifacts:
        custom.append({"domain": "dwc.sidecar.artifacts", "value": artifacts})
    doc = {"Asset": [{"assetFC": {"functionalProperties":
                                  {"customData": custom}}}]}
    path = tmp_path / name
    path.write_text(json.dumps(doc) if valid else '{"broken')
    return path


class _FakeSigner:
    """Minimal Signer-shaped object — just enough to exercise the
    check_signer_selftest contract (sign + verify against keyring pubkey)."""
    def __init__(self, priv: Ed25519PrivateKey, *, delay: float = 0.0):
        self._priv  = priv
        self._delay = delay

    def sign(self, msg: bytes) -> bytes:
        if self._delay:
            time.sleep(self._delay)
        return self._priv.sign(msg)


# ── Individual check tests ───────────────────────────────────────────────


def test_python_version_pass():
    r = doctor.check_python_version()
    assert r.status == "pass"


def test_python_version_fail_when_threshold_unreachable():
    r = doctor.check_python_version(min_minor=99)
    assert r.status == "fail"
    assert "Upgrade" in r.remedy


def test_required_packages_pass():
    r = doctor.check_required_packages()
    assert r.status == "pass"


def test_hash_algs_pass_on_known_alg(tmp_path):
    _write_sidecar(tmp_path, "a.omc.json", artifacts_algs=["sha256"])
    r = doctor.check_hash_algs_available([tmp_path / "a.omc.json"])
    assert r.status == "pass"


def test_hash_algs_fail_on_unknown_alg(tmp_path):
    _write_sidecar(tmp_path, "a.omc.json", artifacts_algs=["not-a-real-alg"])
    r = doctor.check_hash_algs_available([tmp_path / "a.omc.json"])
    assert r.status == "fail"
    assert "not-a-real-alg" in r.detail


def test_hash_algs_pass_when_no_sidecars_present():
    r = doctor.check_hash_algs_available([])
    assert r.status == "pass"


def test_keyring_present_fail_on_missing(tmp_path):
    r = doctor.check_keyring_present(tmp_path / "nope.json")
    assert r.status == "fail"


def test_keyring_present_fail_on_malformed_json(tmp_path):
    p = tmp_path / "keyring.json"; p.write_text("{not json")
    r = doctor.check_keyring_present(p)
    assert r.status == "fail"


def test_keyring_present_fail_on_empty_keys(tmp_path):
    p = tmp_path / "keyring.json"; p.write_text(json.dumps({"keys": {}}))
    r = doctor.check_keyring_present(p)
    assert r.status == "fail"


def test_keyring_present_pass(tmp_path):
    kp, _ = _make_keyring(tmp_path, kids=["dwc-dit-01"])
    r = doctor.check_keyring_present(kp)
    assert r.status == "pass"


def test_keyring_validity_fail_on_expired_in_use(tmp_path):
    kp, _ = _make_keyring(tmp_path, kids=["dwc-dit-01"],
                          valid_until="2020-01-01T00:00:00Z")
    sc = _write_sidecar(tmp_path, "a.omc.json", events_kids=["dwc-dit-01"])
    r = doctor.check_keyring_validity(kp, [sc], NOW)
    assert r.status == "fail"
    assert "dwc-dit-01" in r.detail


def test_keyring_validity_pass_when_expired_but_unused(tmp_path):
    kp, _ = _make_keyring(tmp_path, kids=["dwc-dit-01"],
                          valid_until="2020-01-01T00:00:00Z")
    # No sidecar references dwc-dit-01 → not flagged
    r = doctor.check_keyring_validity(kp, [], NOW)
    assert r.status == "pass"


def test_signer_config_pass_when_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("DWC_SIGNERS", raising=False)
    r = doctor.check_signer_config(tmp_path / "keyring.json")
    assert r.status == "pass"


def test_signer_config_fail_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("DWC_SIGNERS", str(tmp_path / "nope.json"))
    r = doctor.check_signer_config(tmp_path / "keyring.json")
    assert r.status == "fail"


def test_signer_config_fail_when_kid_unmapped(monkeypatch, tmp_path):
    kp, _ = _make_keyring(tmp_path, kids=["dwc-dit-01", "dwc-post-02"])
    sp = tmp_path / "signers.json"
    sp.write_text(json.dumps({"dwc-dit-01": {"type": "local"}}))  # missing dwc-post-02
    monkeypatch.setenv("DWC_SIGNERS", str(sp))
    r = doctor.check_signer_config(kp)
    assert r.status == "fail"
    assert "dwc-post-02" in r.detail


def test_signer_config_pass_when_all_mapped(monkeypatch, tmp_path):
    kp, _ = _make_keyring(tmp_path, kids=["dwc-dit-01"])
    sp = tmp_path / "signers.json"
    sp.write_text(json.dumps({"dwc-dit-01": {"type": "local"}}))
    monkeypatch.setenv("DWC_SIGNERS", str(sp))
    r = doctor.check_signer_config(kp)
    assert r.status == "pass"


# ── Signer self-test: the three paths the plan calls out ────────────────


def test_signer_selftest_pass(tmp_path):
    kp, privs = _make_keyring(tmp_path, kids=["dwc-dit-01"])
    r = doctor.check_signer_selftest(
        kp, signer_factory=lambda kid: _FakeSigner(privs[kid]))
    assert r.status == "pass"


def test_signer_selftest_fail_on_timeout(tmp_path):
    kp, privs = _make_keyring(tmp_path, kids=["dwc-dit-01"])
    r = doctor.check_signer_selftest(
        kp,
        signer_factory=lambda kid: _FakeSigner(privs[kid], delay=1.0),
        timeout=0.3)
    assert r.status == "fail"
    assert "timeout" in r.detail


def test_signer_selftest_fail_on_keyring_divergence(tmp_path):
    """Signer signs with a different key than the keyring records —
    catches the rotated-in-backend-not-keyring misconfiguration."""
    kp, _ = _make_keyring(tmp_path, kids=["dwc-dit-01"])
    wrong_priv = Ed25519PrivateKey.generate()  # NOT the one in keyring
    r = doctor.check_signer_selftest(
        kp, signer_factory=lambda kid: _FakeSigner(wrong_priv))
    assert r.status == "fail"
    assert "does not verify against keyring pubkey" in r.detail


def test_signer_selftest_skips_revoked_kids(tmp_path):
    kp, privs = _make_keyring(
        tmp_path, kids=["live", "revoked"],
        revoked={"revoked": "2020-01-01T00:00:00Z"})
    # Factory only returns for "live" — if self-test tries to construct
    # "revoked", it'd crash. Passing = we skipped "revoked" as intended.
    def factory(kid: str):
        if kid == "revoked":
            raise AssertionError("should not try to construct revoked kid")
        return _FakeSigner(privs[kid])
    r = doctor.check_signer_selftest(kp, signer_factory=factory)
    assert r.status == "pass"


# ── Keys.priv.json hygiene ──────────────────────────────────────────────


def test_keys_priv_warn_when_present_alongside_non_local_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("DWC_SIGNERS", str(tmp_path / "signers.json"))
    (tmp_path / "keys.priv.json").write_text("{}")
    r = doctor.check_keys_priv_absent(tmp_path)
    assert r.status == "warn"
    assert "plaintext" in r.remedy.lower()


def test_keys_priv_pass_when_dev_default(monkeypatch, tmp_path):
    monkeypatch.delenv("DWC_SIGNERS", raising=False)
    (tmp_path / "keys.priv.json").write_text("{}")
    r = doctor.check_keys_priv_absent(tmp_path)
    assert r.status == "pass"


# ── Hosted schema drift (check 9) ───────────────────────────────────────


def test_hosted_schemas_network_fail_is_warn():
    def raising_fetch(url: str) -> bytes:
        raise ConnectionError("dns lookup failed")
    r = doctor.check_hosted_schemas(fetch_url=raising_fetch)
    assert r.status == "warn"
    assert "network unavailable" in r.detail


def test_hosted_schemas_drift_is_fail():
    def divergent_fetch(url: str) -> bytes:
        return b"completely different bytes"
    r = doctor.check_hosted_schemas(fetch_url=divergent_fetch)
    assert r.status == "fail"
    assert "drift" not in r.detail.lower() or "local=" in r.detail
    # It does report the diff shape
    assert "hosted=" in r.detail


def test_hosted_schemas_pass_when_bytes_match():
    from dwc_sidecar.validate import DWC_SCHEMAS
    def matching_fetch(url: str) -> bytes:
        name = url.rsplit("/", 1)[-1]
        for path in DWC_SCHEMAS.values():
            if path.name == name:
                return path.read_bytes()
        raise KeyError(name)
    r = doctor.check_hosted_schemas(fetch_url=matching_fetch)
    assert r.status == "pass"


# ── .watch-state.json (check 10) ────────────────────────────────────────


def test_watch_state_missing_is_pass(tmp_path):
    r = doctor.check_watch_state(tmp_path)
    assert r.status == "pass"


def test_watch_state_emitted_default_when_absent(tmp_path):
    # Old-format state file without `emitted` — must not crash
    (tmp_path / ".watch-state.json").write_text(json.dumps({
        "processed_mhl_sha256": ["abc123"],
    }))
    r = doctor.check_watch_state(tmp_path)
    assert r.status == "pass"


def test_watch_state_malformed_is_fail(tmp_path):
    (tmp_path / ".watch-state.json").write_text("{broken")
    r = doctor.check_watch_state(tmp_path)
    assert r.status == "fail"


def test_watch_state_missing_processed_field_is_fail(tmp_path):
    (tmp_path / ".watch-state.json").write_text(json.dumps({"last_mhl": "x"}))
    r = doctor.check_watch_state(tmp_path)
    assert r.status == "fail"


# ── Sidecar parse (check 11) ────────────────────────────────────────────


def test_sidecars_parse_pass_on_valid(tmp_path):
    _write_sidecar(tmp_path, "ok.omc.json", events_kids=["dwc-dit-01"])
    r = doctor.check_sidecars_parse(tmp_path)
    assert r.status == "pass"


def test_sidecars_parse_no_files_is_pass(tmp_path):
    r = doctor.check_sidecars_parse(tmp_path)
    assert r.status == "pass"


def test_sidecars_parse_fail_on_broken_json(tmp_path):
    p = tmp_path / "bad.omc.json"; p.write_text("{not json")
    r = doctor.check_sidecars_parse(tmp_path, retries=1, retry_delay=0.0)
    assert r.status == "fail"


def test_sidecars_parse_retries_on_partial_write(tmp_path, monkeypatch):
    """Simulate a watcher writing a sidecar that's initially partial and
    complete by the time we retry. The retry is §2.3c's whole point."""
    sidecar = tmp_path / "racey.omc.json"
    sidecar.write_text("")  # exists but empty

    good = json.dumps({"Asset": [{"assetFC": {"functionalProperties":
                       {"customData": [
                         {"domain": "dwc.sidecar.events", "value": []}]}}}]})
    calls = [0]
    original_read = Path.read_text

    def flaky(self, *a, **kw):
        if self == sidecar:
            calls[0] += 1
            if calls[0] == 1:
                return "{partial"
            return good
        return original_read(self, *a, **kw)
    monkeypatch.setattr(Path, "read_text", flaky)

    r = doctor.check_sidecars_parse(tmp_path, retries=2, retry_delay=0.01)
    assert r.status == "pass"
    assert calls[0] >= 2


def test_sidecars_parse_fail_when_no_dwc_block(tmp_path):
    p = tmp_path / "bare.omc.json"
    p.write_text(json.dumps({"Asset": [{"assetFC": {"functionalProperties":
                                                    {"customData": []}}}]}))
    r = doctor.check_sidecars_parse(tmp_path)
    assert r.status == "fail"
    assert "no customData" in r.detail


# ── Key expiry window (check 12) ────────────────────────────────────────


def test_key_expiry_window_pass_far_future(tmp_path):
    kp, _ = _make_keyring(tmp_path, kids=["k1"],
                          valid_until="2030-01-01T00:00:00Z")
    r = doctor.check_key_expiry_window(kp, NOW, warn_days=30)
    assert r.status == "pass"


def test_key_expiry_window_warn_within_threshold(tmp_path):
    soon = (NOW + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    kp, _ = _make_keyring(tmp_path, kids=["k1"], valid_until=soon)
    r = doctor.check_key_expiry_window(kp, NOW, warn_days=30)
    assert r.status == "warn"
    assert "k1" in r.detail


def test_key_expiry_window_fail_when_expired(tmp_path):
    past = (NOW - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    kp, _ = _make_keyring(tmp_path, kids=["k1"], valid_until=past)
    r = doctor.check_key_expiry_window(kp, NOW, warn_days=30)
    assert r.status == "fail"


# ── Orchestrator + output format ────────────────────────────────────────


def test_run_all_checks_quick_skips_selftest_and_hosted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fetch_calls: list[str] = []

    def counting_fetch(url: str) -> bytes:
        fetch_calls.append(url)
        return b"<should-not-be-called>"

    sentinel: list[str] = []
    def sentinel_factory(kid: str):
        sentinel.append(kid)
        raise AssertionError("self-test must not run in --quick")

    results = doctor.run_all_checks(
        cwd=tmp_path, quick=True, now=NOW,
        signer_factory=sentinel_factory, fetch_url=counting_fetch)
    titles = [r.title for r in results]
    assert "Signer self-test"     not in titles
    assert "Hosted schema drift"  not in titles
    assert fetch_calls == []
    assert sentinel == []


def test_format_json_shape_and_top_status(tmp_path):
    kp, privs = _make_keyring(tmp_path, kids=["k1"])
    results = [
        doctor.check_python_version(),
        doctor.check_keyring_present(kp),
    ]
    report = doctor.format_json(results)
    assert set(report) == {"status", "checks"}
    assert report["status"] == "pass"
    for c in report["checks"]:
        assert set(c) == {"status", "title", "detail", "remedy"}


def test_top_status_fail_beats_warn_and_pass(tmp_path):
    results = [
        doctor.CheckResult("pass", "p"),
        doctor.CheckResult("warn", "w"),
        doctor.CheckResult("fail", "f"),
    ]
    assert doctor.format_json(results)["status"] == "fail"


def test_main_returns_zero_when_only_warn(tmp_path, monkeypatch, capsys):
    """Top-level status may be 'warn' but exit code is 0 — WARN never fails
    the check (plan §2.3). Doctor process tooling relies on this."""
    monkeypatch.chdir(tmp_path)
    kp, _ = _make_keyring(tmp_path, kids=["k1"],
                          valid_until=(NOW + timedelta(days=7))
                              .strftime("%Y-%m-%dT%H:%M:%SZ"))
    rc = doctor.main(["--quick"])
    assert rc == 0
    out = capsys.readouterr().out
    # At least one WARN line expected (short expiry)
    assert "[WARN]" in out


def test_main_returns_one_on_any_fail(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # No keyring.json → check 4 FAIL
    rc = doctor.main(["--quick"])
    assert rc == 1
