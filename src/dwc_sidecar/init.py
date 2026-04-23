"""`dwc init` — one-command onboarding for a new DWC signing host.

Does three things in one pass:

  1. Generates a new Ed25519 signing key in the chosen backend (macOS
     Keychain by default on Darwin; a plain-file backend elsewhere).
  2. Writes ``keyring.json`` (public key + validity window) and
     ``signers.json`` (backend config for ``DWC_SIGNERS``) into CWD.
  3. Installs a launch unit (``com.dwc.sidecar.watch.plist`` on macOS,
     ``dwc-sidecar-watch.service`` on Linux) and attempts to activate it.

All external side effects are behind injectable seams so tests can drive
the full happy path without touching the real keychain, filesystem root,
or systemd bus:

  - ``platform`` arg overrides platform detection (macos|linux|windows|docker).
  - ``keychain_sign_test`` replaces the dummy-sign call that triggers the
    macOS permission prompt during setup.
  - ``systemctl_runner`` replaces the ``systemctl --user`` invocations
    used to register the Linux unit.
  - ``now`` injects a fixed ``datetime`` for the keyring validity window
    so golden-file tests are deterministic.

Exit codes (callers rely on these — don't reshuffle):

  0   success
  2   CLI arg error (argparse default)
  3   keychain backend unusable (raises in ``--yes``; friendlier exit
      than a traceback for CI detection)
  4   required arg missing when ``--yes`` bypasses the prompt
  5   refusing to overwrite ``keyring.json``/``signers.json`` without ``--force``
  6   backend not supported on detected platform

``dwc init`` never writes ``keys.priv.json``. The file-backed path writes
to the explicit ``--file-path`` location; the local-backend fallback (which
would land a plaintext key in CWD) is intentionally not a default choice.
"""
import argparse
import base64
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import keygen


EXIT_OK                   = 0
EXIT_CLI_ARG              = 2
EXIT_KEYCHAIN_UNAVAILABLE = 3
EXIT_MISSING_REQUIRED_ARG = 4
EXIT_REFUSE_OVERWRITE     = 5
EXIT_BACKEND_UNSUPPORTED  = 6


TEMPLATES_DIR = Path(__file__).parent / "data" / "templates"


def _detect_platform() -> str:
    """Return one of ``macos``, ``linux``, ``windows``, ``docker``.

    Docker detection (``/.dockerenv`` marker or ``container=docker`` env)
    precedes OS detection — running inside a Linux container is behaviourally
    closer to ``docker`` than to ``linux`` (no systemd, no LaunchAgent)."""
    if Path("/.dockerenv").exists() or os.environ.get("container") == "docker":
        return "docker"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def _default_backend(platform: str) -> str:
    return {
        "macos":   "keychain",
        "linux":   "file",
        "windows": "file",
        "docker":  "file",
    }.get(platform, "file")


def _render(template: str, **values: object) -> str:
    """Replace ``{{name}}`` tokens with ``str(values[name])``. Any other
    ``$``-prefixed or brace-less text (e.g., launchd's literal ``$HOME``)
    is preserved unchanged — ``string.Template`` would eat ``$HOME``."""
    out = template
    for k, v in values.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def _load_template(name: str) -> str:
    return (TEMPLATES_DIR / name).read_text()


def _find_dwc_binary() -> str:
    """Best-effort resolution of the ``dwc`` entry point for launch unit
    invocation. Falls back to a sensible default Homebrew path; users can
    post-edit the unit file if their binary lives elsewhere."""
    found = shutil.which("dwc")
    if found:
        return found
    if sys.platform == "darwin":
        return "/opt/homebrew/bin/dwc"
    return "/usr/local/bin/dwc"


def _default_keychain_sign_test(kid: str, service: str) -> None:
    """Sign a throwaway 32-byte payload to trigger the macOS Keychain
    permission dialog during setup, not at 2am during offload."""
    from .signers.keychain import KeychainSigner
    signer = KeychainSigner(kid=kid, service=service)
    signer.sign(b"\x00" * 32)


def _default_systemctl_runner(cmd: list[str]) -> int:
    """Run a systemctl command; return its exit code, or 127 if systemctl
    is absent (mirrors shell 'command not found' convention)."""
    import subprocess
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return r.returncode
    except FileNotFoundError:
        return 127


def _write_keyring(path: Path, kid: str, pub_raw: bytes,
                   valid_days: int, now: datetime) -> None:
    entry = {
        "keys": {
            kid: {
                "publicKey":        base64.b64encode(pub_raw).decode("ascii"),
                "validFrom":        keygen._iso_days(0, now=now),
                "validUntil":       keygen._iso_days(valid_days, now=now),
                "revokedAt":        None,
                "revocationReason": None,
            }
        }
    }
    path.write_text(json.dumps(entry, indent=2) + "\n")


def _write_signers(path: Path, backend: str, kid: str, *,
                   service: str, file_path: Path | None) -> None:
    if backend == "keychain":
        rendered = _render(_load_template("signers.keychain.json.tmpl"),
                           kid=kid, service=service)
    elif backend == "file":
        assert file_path is not None  # validated upstream in run_init
        rendered = _render(_load_template("signers.file.json.tmpl"),
                           kid=kid, path=str(file_path.resolve()))
    elif backend == "local":
        rendered = _render(_load_template("signers.local.json.tmpl"), kid=kid)
    else:
        raise ValueError(f"unknown backend {backend!r}")
    path.write_text(rendered)


def _write_launch_unit(platform: str, dwc_binary: str, watch_root: Path,
                       signers_path: Path, home: Path) -> Path:
    """Write the launchd plist (macOS) or systemd user unit (Linux) and
    return its path. Does not activate it — caller handles that."""
    if platform == "macos":
        rendered = _render(_load_template("launchagent.plist.tmpl"),
                           dwc_binary=dwc_binary,
                           watch_root=str(watch_root.resolve()),
                           signers_path=str(signers_path.resolve()))
        unit = home / "Library" / "LaunchAgents" / "com.dwc.sidecar.watch.plist"
    elif platform == "linux":
        rendered = _render(_load_template("systemd.service.tmpl"),
                           dwc_binary=dwc_binary,
                           watch_root=str(watch_root.resolve()),
                           signers_path=str(signers_path.resolve()))
        unit = home / ".config" / "systemd" / "user" / "dwc-sidecar-watch.service"
    else:
        raise ValueError(f"no launch unit for platform {platform!r}")
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(rendered)
    return unit


def run_init(
    *,
    backend: str,
    kid: str = "dwc-dit-01",
    valid_days: int = 90,
    keychain_service: str = "dwc-sidecar",
    file_path: Path | None = None,
    watch_root: Path | None = None,
    install_launch_unit: bool = True,
    platform: str | None = None,
    home: Path | None = None,
    cwd: Path | None = None,
    force: bool = False,
    yes: bool = False,
    now: datetime | None = None,
    # Injection seams (tests override; production uses the defaults)
    keychain_sign_test: Callable[[str, str], None] | None = None,
    systemctl_runner: Callable[[list[str]], int] | None = None,
    keygen_keychain: Callable[[str, str], bytes] | None = None,
    keygen_file: Callable[[str, Path], bytes] | None = None,
) -> int:
    """Execute the full init sequence. Returns an exit code."""
    platform   = platform   or _detect_platform()
    home       = home       or Path.home()
    cwd        = cwd        or Path.cwd()
    now        = now        or datetime.now(timezone.utc)
    watch_root = watch_root or cwd

    # ── Validate platform/backend combination ────────────────────────────
    if backend == "keychain" and platform != "macos":
        print(f"ERROR: keychain backend requires macOS (detected: {platform})",
              file=sys.stderr)
        return EXIT_BACKEND_UNSUPPORTED
    if backend == "file" and file_path is None:
        print("ERROR: --backend file requires --file-path", file=sys.stderr)
        return EXIT_MISSING_REQUIRED_ARG
    if backend not in ("keychain", "file"):
        # 'local' is intentionally not an init default: it lands a plaintext
        # private key in CWD. Users who want it run 'dwc keygen --backend local'
        # directly and write signers.json by hand.
        print(f"ERROR: unsupported backend for init: {backend!r} "
              f"(choose keychain or file)", file=sys.stderr)
        return EXIT_BACKEND_UNSUPPORTED

    # ── Refuse to overwrite without --force ──────────────────────────────
    keyring_path = cwd / "keyring.json"
    signers_path = cwd / "signers.json"
    for p in (keyring_path, signers_path):
        if p.exists() and not force:
            print(f"ERROR: {p.name} already exists in {cwd}. "
                  f"Pass --force to overwrite.", file=sys.stderr)
            return EXIT_REFUSE_OVERWRITE

    # ── Generate key in chosen backend ───────────────────────────────────
    if backend == "keychain":
        fn = keygen_keychain or keygen._keygen_keychain
        pub_raw = fn(kid, keychain_service)
    else:  # file
        assert file_path is not None
        fn = keygen_file or keygen._keygen_local
        pub_raw = fn(kid, file_path)

    # ── Write keyring.json ───────────────────────────────────────────────
    _write_keyring(keyring_path, kid, pub_raw, valid_days, now)
    print(f"Wrote keyring.json → {keyring_path}")

    # ── Write signers.json ───────────────────────────────────────────────
    _write_signers(signers_path, backend, kid,
                   service=keychain_service, file_path=file_path)
    print(f"Wrote signers.json → {signers_path}")

    # ── Install launch unit ──────────────────────────────────────────────
    unit_path: Path | None = None
    if install_launch_unit and platform in ("macos", "linux"):
        dwc_binary = _find_dwc_binary()
        unit_path = _write_launch_unit(platform, dwc_binary, watch_root,
                                       signers_path, home)
        print(f"Wrote launch unit → {unit_path}")

        if platform == "linux":
            runner = systemctl_runner or _default_systemctl_runner
            rc1 = runner(["systemctl", "--user", "daemon-reload"])
            rc2 = runner(["systemctl", "--user", "enable",
                          "dwc-sidecar-watch.service"])
            if rc1 != 0 or rc2 != 0:
                print("WARNING: systemd --user not available. The unit file "
                      f"has been written to {unit_path} but could not be "
                      "enabled automatically. On a systemd system, run:",
                      file=sys.stderr)
                print("  systemctl --user daemon-reload",          file=sys.stderr)
                print("  systemctl --user enable dwc-sidecar-watch.service",
                      file=sys.stderr)
                print("On non-systemd systems, start the watcher manually.",
                      file=sys.stderr)

    # ── Keychain dummy sign (triggers macOS prompt during setup) ─────────
    if backend == "keychain":
        sign_fn = keychain_sign_test or _default_keychain_sign_test
        try:
            sign_fn(kid, keychain_service)
        except RuntimeError as e:
            if yes:
                print("ERROR: Keychain backend requires an interactive "
                      "session.", file=sys.stderr)
                print("Use `--backend file` for CI environments.",
                      file=sys.stderr)
                print(f"  (underlying error: {e})", file=sys.stderr)
                return EXIT_KEYCHAIN_UNAVAILABLE
            print(f"WARNING: dummy sign failed: {e}. Re-run or sign manually "
                  "to trigger the macOS permission prompt.", file=sys.stderr)

    # ── Next steps ───────────────────────────────────────────────────────
    print()
    print("Next steps:")
    print("  1) Add to your shell profile:")
    print(f'       export DWC_SIGNERS="{signers_path.resolve()}"')
    print("  2) Verify your rig:")
    print("       dwc doctor")
    if install_launch_unit and unit_path is not None:
        print("  3) Start watching:")
        if platform == "macos":
            print(f"       launchctl load {unit_path}")
        elif platform == "linux":
            print("       systemctl --user start dwc-sidecar-watch.service")
    print()
    print("Done.")
    return EXIT_OK


def _prompt(question: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    resp = input(f"{question}{suffix}: ").strip()
    return resp or (default or "")


def _interactive_gather(platform: str, cwd: Path) -> dict:
    """Walk the user through the decisions run_init() needs. Returns a
    kwargs dict ready to splat into run_init()."""
    default_be = _default_backend(platform)
    print(f"\nDWC sidecar setup")
    print(f"\nHost: {platform}, Python {sys.version.split()[0]}")
    print(f"Working directory: {cwd}\n")

    print("Where should your signing key live?")
    if platform == "macos":
        print("  1) macOS Keychain (recommended on this host)")
    else:
        print("  1) macOS Keychain (not available — requires macOS)")
    print("  2) File on disk (portable, for Docker/CI)")
    print("  3) I'll configure a cloud / HSM backend myself")
    default_choice = "1" if default_be == "keychain" else "2"
    choice = _prompt("Choice", default_choice)

    if choice == "1":
        if platform != "macos":
            print("Keychain requires macOS; falling back to file backend.")
            backend = "file"
        else:
            backend = "keychain"
    elif choice == "2":
        backend = "file"
    else:
        print("Fine — run `dwc keygen` for your chosen backend and write "
              "signers.json by hand. See the backend modules' docstrings.")
        raise SystemExit(EXIT_OK)

    kid        = _prompt("Signing kid", "dwc-dit-01")
    valid_days = int(_prompt("Keyring entry valid for (days)", "90"))

    file_path: Path | None = None
    if backend == "file":
        default_path = str(cwd / "keys.priv.json")
        file_path    = Path(_prompt("Private-key file path", default_path))

    install = True
    if platform in ("macos", "linux"):
        launch_name = "LaunchAgent" if platform == "macos" else "systemd user unit"
        resp = _prompt(f"Install a {launch_name} so `dwc watch` starts at "
                       "login? (Y/n)", "Y").lower()
        install = resp not in ("n", "no")

    default_watch = str(cwd)
    watch_root    = Path(_prompt("Watch folder", default_watch))

    return {
        "backend":            backend,
        "kid":                kid,
        "valid_days":         valid_days,
        "file_path":          file_path,
        "install_launch_unit": install,
        "watch_root":         watch_root,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="One-command onboarding: generate a key, write keyring.json + "
                    "signers.json, install a launch unit.")
    ap.add_argument("--backend", choices=["keychain", "file"],
                    help="Private-key storage. Default: keychain on macOS, file elsewhere.")
    ap.add_argument("--kid", default="dwc-dit-01",
                    help="Key identifier (default: dwc-dit-01)")
    ap.add_argument("--valid-days", type=int, default=90,
                    help="Keyring validity window in days (default: 90)")
    ap.add_argument("--service", default="dwc-sidecar",
                    help="Keychain service name (default: dwc-sidecar)")
    ap.add_argument("--file-path", type=Path,
                    help="Private-key file path (required for --backend file)")
    ap.add_argument("--watch-root", type=Path,
                    help="Directory the launch unit will watch (default: CWD)")
    ap.add_argument("--no-launch-agent", dest="install_launch_unit",
                    action="store_false",
                    help="Skip writing the LaunchAgent / systemd unit")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite existing keyring.json / signers.json")
    ap.add_argument("--yes", action="store_true",
                    help="Non-interactive; fail rather than prompt for missing args")
    args = ap.parse_args(argv)

    platform = _detect_platform()
    cwd      = Path.cwd()

    if args.yes:
        backend = args.backend or _default_backend(platform)
        if backend == "file" and args.file_path is None:
            print("ERROR: --yes with --backend file requires --file-path",
                  file=sys.stderr)
            return EXIT_MISSING_REQUIRED_ARG
        kwargs = {
            "backend":             backend,
            "kid":                 args.kid,
            "valid_days":          args.valid_days,
            "keychain_service":    args.service,
            "file_path":           args.file_path,
            "watch_root":          args.watch_root,
            "install_launch_unit": args.install_launch_unit,
            "force":               args.force,
            "yes":                 True,
        }
    else:
        gathered = _interactive_gather(platform, cwd)
        kwargs = {
            **gathered,
            "keychain_service": args.service,
            "force":            args.force,
            "yes":              False,
        }

    return run_init(**kwargs)


if __name__ == "__main__":
    sys.exit(main())
