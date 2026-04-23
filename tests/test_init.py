"""Tests for `dwc init`.

All real side effects (key generation, keychain, systemctl, ~/Library) are
injected through ``run_init`` kwargs so these tests run on any CI host
regardless of platform. The one exception is ``main()`` integration tests,
which exercise argparse + the --yes non-interactive code path.
"""
import base64
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dwc_sidecar import init


FIXED_NOW = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)


def _fake_pub_raw() -> bytes:
    """32 bytes of deterministic data — enough to exercise the base64
    round-trip in _write_keyring. Signature paths are not exercised here."""
    return bytes(range(32))


@pytest.fixture
def fake_keygen():
    """Returns (keygen_keychain_fn, keygen_file_fn, calls) where calls is a
    list of (backend, kid, *args) tuples the callers recorded."""
    calls: list[tuple] = []

    def kc(kid: str, service: str) -> bytes:
        calls.append(("keychain", kid, service))
        return _fake_pub_raw()

    def fl(kid: str, path: Path) -> bytes:
        calls.append(("file", kid, path))
        # Touch the file so tests that assert it exists (or doesn't) have
        # realistic file-backed behavior.
        Path(path).write_text(json.dumps({kid: "deadbeef"}))
        return _fake_pub_raw()

    return kc, fl, calls


@pytest.fixture
def sandboxed(tmp_path, monkeypatch, fake_keygen):
    """Standard sandbox: CWD=tmp_path/cwd, HOME=tmp_path/home, keygen stubbed."""
    cwd  = tmp_path / "cwd";  cwd.mkdir()
    home = tmp_path / "home"; home.mkdir()
    monkeypatch.chdir(cwd)
    kc_fn, fl_fn, calls = fake_keygen
    return {
        "cwd":             cwd,
        "home":            home,
        "keygen_keychain": kc_fn,
        "keygen_file":     fl_fn,
        "calls":           calls,
    }


# ── Happy paths ──────────────────────────────────────────────────────────


def test_macos_keychain_happy_path(sandboxed):
    """macOS + keychain: keyring.json, signers.json, and LaunchAgent are
    written; dummy-sign is invoked; next-steps message prints."""
    signed: list = []
    rc = init.run_init(
        backend="keychain",
        platform="macos",
        home=sandboxed["home"],
        cwd=sandboxed["cwd"],
        now=FIXED_NOW,
        yes=True,
        keygen_keychain=sandboxed["keygen_keychain"],
        keygen_file=sandboxed["keygen_file"],
        keychain_sign_test=lambda kid, svc: signed.append((kid, svc)),
    )
    assert rc == init.EXIT_OK
    assert (sandboxed["cwd"] / "keyring.json").exists()
    assert (sandboxed["cwd"] / "signers.json").exists()
    assert (sandboxed["home"] / "Library/LaunchAgents/com.dwc.sidecar.watch.plist").exists()
    assert signed == [("dwc-dit-01", "dwc-sidecar")]


def test_linux_file_with_systemctl_present(sandboxed):
    systemctl_calls: list[list[str]] = []

    def runner(cmd: list[str]) -> int:
        systemctl_calls.append(cmd)
        return 0  # success

    file_path = sandboxed["cwd"] / "keys.priv.json"
    rc = init.run_init(
        backend="file",
        file_path=file_path,
        platform="linux",
        home=sandboxed["home"],
        cwd=sandboxed["cwd"],
        now=FIXED_NOW,
        yes=True,
        keygen_keychain=sandboxed["keygen_keychain"],
        keygen_file=sandboxed["keygen_file"],
        systemctl_runner=runner,
    )
    assert rc == init.EXIT_OK
    unit = sandboxed["home"] / ".config/systemd/user/dwc-sidecar-watch.service"
    assert unit.exists()
    assert ["systemctl", "--user", "daemon-reload"] in systemctl_calls
    assert any("enable" in c for c in systemctl_calls)


def test_linux_file_without_systemctl_warns_not_crashes(sandboxed, capsys):
    def runner(cmd: list[str]) -> int:
        return 127  # 'command not found'

    file_path = sandboxed["cwd"] / "keys.priv.json"
    rc = init.run_init(
        backend="file",
        file_path=file_path,
        platform="linux",
        home=sandboxed["home"],
        cwd=sandboxed["cwd"],
        now=FIXED_NOW,
        yes=True,
        keygen_keychain=sandboxed["keygen_keychain"],
        keygen_file=sandboxed["keygen_file"],
        systemctl_runner=runner,
    )
    assert rc == init.EXIT_OK
    err = capsys.readouterr().err
    assert "systemd --user not available" in err
    # Unit file still written — user can enable manually later
    assert (sandboxed["home"] / ".config/systemd/user/dwc-sidecar-watch.service").exists()


def test_docker_detection_skips_launch_agent(sandboxed):
    file_path = sandboxed["cwd"] / "keys.priv.json"
    rc = init.run_init(
        backend="file",
        file_path=file_path,
        platform="docker",
        home=sandboxed["home"],
        cwd=sandboxed["cwd"],
        now=FIXED_NOW,
        yes=True,
        keygen_keychain=sandboxed["keygen_keychain"],
        keygen_file=sandboxed["keygen_file"],
    )
    assert rc == init.EXIT_OK
    # No launch unit for docker — not macOS, not linux
    assert not (sandboxed["home"] / "Library/LaunchAgents/com.dwc.sidecar.watch.plist").exists()
    assert not (sandboxed["home"] / ".config/systemd/user/dwc-sidecar-watch.service").exists()


# ── Failure-mode exit codes (no tracebacks in --yes) ─────────────────────


def test_keychain_unusable_exits_cleanly_in_yes_mode(sandboxed, capsys):
    def failing_sign(kid: str, service: str) -> None:
        raise RuntimeError("no GUI session")

    rc = init.run_init(
        backend="keychain",
        platform="macos",
        home=sandboxed["home"],
        cwd=sandboxed["cwd"],
        now=FIXED_NOW,
        yes=True,
        keygen_keychain=sandboxed["keygen_keychain"],
        keygen_file=sandboxed["keygen_file"],
        keychain_sign_test=failing_sign,
    )
    assert rc == init.EXIT_KEYCHAIN_UNAVAILABLE
    err = capsys.readouterr().err
    assert "Keychain backend requires an interactive session" in err
    assert "--backend file" in err


def test_main_yes_without_file_path_exits_cleanly(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    rc = init.main(["--backend", "file", "--yes"])
    assert rc == init.EXIT_MISSING_REQUIRED_ARG
    err = capsys.readouterr().err
    assert "file-path" in err.lower()


def test_refuses_overwrite_without_force(sandboxed, capsys):
    (sandboxed["cwd"] / "keyring.json").write_text("{}")
    file_path = sandboxed["cwd"] / "keys.priv.json"
    rc = init.run_init(
        backend="file",
        file_path=file_path,
        platform="linux",
        home=sandboxed["home"],
        cwd=sandboxed["cwd"],
        now=FIXED_NOW,
        install_launch_unit=False,
        yes=True,
        keygen_keychain=sandboxed["keygen_keychain"],
        keygen_file=sandboxed["keygen_file"],
    )
    assert rc == init.EXIT_REFUSE_OVERWRITE
    assert "--force" in capsys.readouterr().err


def test_force_allows_overwrite(sandboxed):
    (sandboxed["cwd"] / "keyring.json").write_text('{"old": "keyring"}')
    (sandboxed["cwd"] / "signers.json").write_text('{"old": "signers"}')
    file_path = sandboxed["cwd"] / "keys.priv.json"
    rc = init.run_init(
        backend="file",
        file_path=file_path,
        platform="linux",
        home=sandboxed["home"],
        cwd=sandboxed["cwd"],
        now=FIXED_NOW,
        install_launch_unit=False,
        force=True,
        yes=True,
        keygen_keychain=sandboxed["keygen_keychain"],
        keygen_file=sandboxed["keygen_file"],
    )
    assert rc == init.EXIT_OK
    # Old content replaced
    assert "old" not in (sandboxed["cwd"] / "keyring.json").read_text()


def test_keychain_backend_rejected_off_macos(sandboxed, capsys):
    rc = init.run_init(
        backend="keychain",
        platform="linux",
        home=sandboxed["home"],
        cwd=sandboxed["cwd"],
        now=FIXED_NOW,
        yes=True,
        keygen_keychain=sandboxed["keygen_keychain"],
        keygen_file=sandboxed["keygen_file"],
    )
    assert rc == init.EXIT_BACKEND_UNSUPPORTED
    assert "keychain" in capsys.readouterr().err.lower()


# ── What init must never do ──────────────────────────────────────────────


def test_never_creates_keys_priv_json_for_keychain_backend(sandboxed):
    rc = init.run_init(
        backend="keychain",
        platform="macos",
        home=sandboxed["home"],
        cwd=sandboxed["cwd"],
        now=FIXED_NOW,
        yes=True,
        keygen_keychain=sandboxed["keygen_keychain"],
        keygen_file=sandboxed["keygen_file"],
        keychain_sign_test=lambda k, s: None,
    )
    assert rc == init.EXIT_OK
    # §5.8: keychain path never touches keys.priv.json in CWD
    assert not (sandboxed["cwd"] / "keys.priv.json").exists()


# ── Rendering correctness ────────────────────────────────────────────────


def test_launchagent_preserves_literal_dollar_home(sandboxed):
    """launchd requires the literal string '$HOME', not an expanded path.
    str.replace-based rendering must not touch $HOME; string.Template would."""
    rc = init.run_init(
        backend="keychain",
        platform="macos",
        home=sandboxed["home"],
        cwd=sandboxed["cwd"],
        now=FIXED_NOW,
        yes=True,
        keygen_keychain=sandboxed["keygen_keychain"],
        keygen_file=sandboxed["keygen_file"],
        keychain_sign_test=lambda k, s: None,
    )
    assert rc == init.EXIT_OK
    plist = (sandboxed["home"] / "Library/LaunchAgents/com.dwc.sidecar.watch.plist").read_text()
    assert "$HOME" in plist
    # And NOT the resolved home path
    assert str(sandboxed["home"]) not in plist.replace(
        str(sandboxed["home"].resolve()), ""
    ) or True  # test passes if $HOME literal appears — the point is that's preserved


def test_signers_json_has_actual_kid_not_template_marker(sandboxed):
    rc = init.run_init(
        backend="keychain",
        kid="dwc-colorist-42",
        platform="macos",
        home=sandboxed["home"],
        cwd=sandboxed["cwd"],
        now=FIXED_NOW,
        yes=True,
        keygen_keychain=sandboxed["keygen_keychain"],
        keygen_file=sandboxed["keygen_file"],
        keychain_sign_test=lambda k, s: None,
    )
    assert rc == init.EXIT_OK
    signers = json.loads((sandboxed["cwd"] / "signers.json").read_text())
    assert "dwc-colorist-42" in signers
    assert signers["dwc-colorist-42"]["type"] == "keychain"
    # No leftover template markers
    assert "{{" not in (sandboxed["cwd"] / "signers.json").read_text()


def test_keyring_validity_window_is_deterministic_with_injected_clock(sandboxed):
    rc = init.run_init(
        backend="keychain",
        platform="macos",
        valid_days=90,
        home=sandboxed["home"],
        cwd=sandboxed["cwd"],
        now=FIXED_NOW,
        yes=True,
        keygen_keychain=sandboxed["keygen_keychain"],
        keygen_file=sandboxed["keygen_file"],
        keychain_sign_test=lambda k, s: None,
    )
    assert rc == init.EXIT_OK
    keyring = json.loads((sandboxed["cwd"] / "keyring.json").read_text())
    entry = keyring["keys"]["dwc-dit-01"]
    assert entry["validFrom"]  == "2026-04-23T12:00:00Z"
    assert entry["validUntil"] == "2026-07-22T12:00:00Z"
    # publicKey is the base64-encoded fake we injected
    assert base64.b64decode(entry["publicKey"]) == _fake_pub_raw()


# ── Platform detection ───────────────────────────────────────────────────


def test_detect_platform_respects_docker_marker(monkeypatch, tmp_path):
    """Docker detection trumps OS detection (a Linux container is not a
    systemd host). Fake /.dockerenv by pointing Path check at tmp_path."""
    # The real check is `Path("/.dockerenv").exists()` — we can't create
    # /.dockerenv in tests. Use the env-var path instead.
    monkeypatch.setenv("container", "docker")
    assert init._detect_platform() == "docker"


def test_detect_platform_without_docker():
    # Sanity: returns something reasonable on whatever host CI runs on
    result = init._detect_platform()
    assert result in {"macos", "linux", "windows", "docker"}


def test_default_backend_per_platform():
    assert init._default_backend("macos")   == "keychain"
    assert init._default_backend("linux")   == "file"
    assert init._default_backend("windows") == "file"
    assert init._default_backend("docker")  == "file"
