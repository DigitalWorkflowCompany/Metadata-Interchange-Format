"""`dwc doctor` — pre-flight audit of a production signing host.

Twelve checks cover the trust-surface misconfigurations that today only
surface 400 sidecars into a `dwc watch` run: missing packages, expired
keys, signer backend/keyring divergence, hosted-schema drift. Doctor
gives the DIT a <2s yes/no answer at call time.

Each check is a pure function taking explicit paths — never reading from
``os.getcwd()`` — so the full matrix is testable under ``tmp_path``
without ``monkeypatch.chdir``. External I/O (signer construction, the
hosted-schema fetch) is behind injectable seams: ``signer_factory`` and
``fetch_url`` accept callables that tests swap in.

``--quick`` skips the two network/hardware checks (7 signer self-test
and 9 hosted-schema drift) so the menu-bar app can poll every 60s
without either talking to a cloud backend or making HTTP requests.
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FuturesTimeout
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable


# ── Data model ───────────────────────────────────────────────────────────


@dataclass
class CheckResult:
    status: str           # "pass" | "warn" | "fail"
    title: str
    detail: str      = ""
    remedy: str      = ""

    def as_dict(self) -> dict:
        return {"status": self.status, "title": self.title,
                "detail": self.detail, "remedy": self.remedy}


def _pass(title: str, detail: str = "") -> CheckResult:
    return CheckResult("pass", title, detail)


def _warn(title: str, detail: str, remedy: str = "") -> CheckResult:
    return CheckResult("warn", title, detail, remedy)


def _fail(title: str, detail: str, remedy: str = "") -> CheckResult:
    return CheckResult("fail", title, detail, remedy)


# ── Small helpers ────────────────────────────────────────────────────────


def _load_keyring(keyring_path: Path) -> dict:
    """Return ``{kid: entry}`` (entries expanded to dict form). Raises on
    parse errors — callers decide whether to surface as FAIL or WARN."""
    raw = json.loads(keyring_path.read_text())
    keys = raw.get("keys", {})

    def expand(e):
        if isinstance(e, str):
            return {"publicKey": e, "validFrom": None, "validUntil": None,
                    "revokedAt": None, "revocationReason": None}
        return e
    return {k: expand(v) for k, v in keys.items()}


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _iter_sidecars(cwd: Path) -> list[Path]:
    """Top-level `*.omc.json` files in ``cwd``. Non-recursive by design —
    doctor is a call-time audit of the host directory, not a tree walk."""
    return sorted(cwd.glob("*.omc.json"))


def _find_custom_data(node, trail=()):
    """Yield every `customData` array in the doc. Local duplicate of
    validate.find_custom_data so doctor doesn't pull in the whole
    validator module at --quick startup."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "customData" and isinstance(v, list):
                yield v
            yield from _find_custom_data(v, trail + (k,))
    elif isinstance(node, list):
        for v in node:
            yield from _find_custom_data(v, trail)


def _sidecar_algs_and_kids(doc: dict) -> tuple[set[str], set[str]]:
    """Return (referenced hash algs, referenced kids) for a sidecar doc."""
    algs: set[str] = set()
    kids: set[str] = set()
    for cd in _find_custom_data(doc):
        for entry in cd:
            if not isinstance(entry, dict):
                continue
            dom = entry.get("domain")
            if dom == "dwc.sidecar.artifacts":
                for a in entry.get("value") or []:
                    h = (a or {}).get("hash") or {}
                    alg = h.get("alg")
                    if isinstance(alg, str):
                        algs.add(alg)
            elif dom == "dwc.sidecar.events":
                for ev in entry.get("value") or []:
                    kid = (ev.get("sig") or {}).get("kid")
                    if isinstance(kid, str):
                        kids.add(kid)
    return algs, kids


# ── Individual checks ────────────────────────────────────────────────────


def check_python_version(*, min_major: int = 3, min_minor: int = 11) -> CheckResult:
    v = sys.version_info
    actual = f"{v.major}.{v.minor}.{v.micro}"
    needed = f"{min_major}.{min_minor}"
    if (v.major, v.minor) < (min_major, min_minor):
        return _fail("Python version",
                     f"{actual} < {needed}",
                     remedy=f"Upgrade to Python ≥ {needed}")
    return _pass("Python version", f"{actual} ≥ {needed}")


def check_required_packages() -> CheckResult:
    required = ("jsonschema", "rfc8785", "cryptography", "xxhash", "blake3")
    missing: list[str] = []
    for name in required:
        try:
            __import__(name)
        except ImportError:
            missing.append(name)
    if missing:
        return _fail("Required packages",
                     f"ImportError: {', '.join(missing)}",
                     remedy="pip install -e .[dev]  (or reinstall dwc-sidecar)")
    return _pass("Required packages",
                 f"{len(required)} packages importable")


def check_hash_algs_available(sidecars: Iterable[Path]) -> CheckResult:
    from .canonical import HASH_ALGS
    referenced: set[str] = set()
    for p in sidecars:
        try:
            doc = json.loads(p.read_text())
        except Exception:
            # Check 11 will flag the parse error; skip here.
            continue
        algs, _ = _sidecar_algs_and_kids(doc)
        referenced |= algs

    if not referenced:
        return _pass("Hash algorithms",
                     "no sidecars in CWD declaring hash algs")

    failures: list[str] = []
    for alg in sorted(referenced):
        if alg not in HASH_ALGS:
            failures.append(f"{alg} not in canonical.HASH_ALGS")
            continue
        try:
            h = HASH_ALGS[alg]()
            h.update(b"doctor-test")
            h.hexdigest()
        except Exception as e:
            failures.append(f"{alg}: {e}")
    if failures:
        return _fail("Hash algorithms",
                     "; ".join(failures),
                     remedy="Install the missing optional backend (see docs/operations/doctor.md)")
    return _pass("Hash algorithms",
                 f"{len(referenced)} alg(s) referenced and available")


def check_keyring_present(keyring_path: Path) -> CheckResult:
    if not keyring_path.exists():
        return _fail("keyring.json",
                     f"{keyring_path} not found",
                     remedy="Run `dwc init` or copy keyring.json from your deployment")
    try:
        raw = json.loads(keyring_path.read_text())
    except Exception as e:
        return _fail("keyring.json",
                     f"parse error: {e}",
                     remedy=f"Inspect {keyring_path} — must be valid JSON with a .keys object")
    if "keys" not in raw or not isinstance(raw["keys"], dict) or not raw["keys"]:
        return _fail("keyring.json",
                     "no .keys entries",
                     remedy="Add at least one kid via `dwc keygen` + paste output into keyring.json")
    return _pass("keyring.json", f"{len(raw['keys'])} kid(s)")


def check_keyring_validity(keyring_path: Path, sidecars: Iterable[Path],
                           now: datetime) -> CheckResult:
    if not keyring_path.exists():
        return _warn("Keyring validity windows",
                     "keyring.json not found (skipping — covered by check 4)")
    try:
        keyring = _load_keyring(keyring_path)
    except Exception as e:
        return _warn("Keyring validity windows",
                     f"keyring parse error (skipping — covered by check 4): {e}")

    # Collect kids actually referenced by events in CWD sidecars.
    referenced_kids: set[str] = set()
    for p in sidecars:
        try:
            doc = json.loads(p.read_text())
        except Exception:
            continue
        _, kids = _sidecar_algs_and_kids(doc)
        referenced_kids |= kids

    expired: list[str] = []
    for kid, entry in keyring.items():
        if kid not in referenced_kids:
            continue  # §2.3 check 5: only flag if referenced
        vto = _parse_iso(entry.get("validUntil"))
        if vto is not None and vto < now:
            expired.append(f"{kid} (validUntil={entry.get('validUntil')})")

    if expired:
        return _fail("Keyring validity windows",
                     f"expired-but-in-use: {', '.join(expired)}",
                     remedy="Rotate to a fresh kid; old events remain verifiable against the keyring's pre-expiry entry")
    return _pass("Keyring validity windows",
                 f"all {len(referenced_kids)} referenced kid(s) within window")


def check_signer_config(keyring_path: Path) -> CheckResult:
    env = os.environ.get("DWC_SIGNERS")
    if not env:
        return _pass("Signer config (DWC_SIGNERS)",
                     "unset — will use JsonFileSigner against keys.priv.json")
    cfg_path = Path(env)
    if not cfg_path.exists():
        return _fail("Signer config (DWC_SIGNERS)",
                     f"{env} points at a missing file",
                     remedy=f"Write a signers.json at {env} or unset DWC_SIGNERS")
    try:
        cfg = json.loads(cfg_path.read_text())
    except Exception as e:
        return _fail("Signer config (DWC_SIGNERS)",
                     f"{env}: parse error: {e}",
                     remedy=f"Fix the JSON in {env}")
    if not isinstance(cfg, dict):
        return _fail("Signer config (DWC_SIGNERS)",
                     f"{env}: expected a top-level object mapping kid → config",
                     remedy="Rewrite as {\"kid\": {\"type\": \"...\", ...}}")

    if not keyring_path.exists():
        return _warn("Signer config (DWC_SIGNERS)",
                     f"keyring.json missing — can't cross-check kids against {env}")
    try:
        keyring = _load_keyring(keyring_path)
    except Exception as e:
        return _warn("Signer config (DWC_SIGNERS)",
                     f"keyring parse error (skipping cross-check): {e}")
    missing = sorted(set(keyring) - set(cfg))
    if missing:
        return _fail("Signer config (DWC_SIGNERS)",
                     f"kid(s) without a backend: {', '.join(missing)}",
                     remedy=f"Add entries in {env} for each kid")
    return _pass("Signer config (DWC_SIGNERS)",
                 f"{len(keyring)} kid(s) mapped at {env}")


def check_signer_selftest(keyring_path: Path, *,
                          signer_factory: Callable | None = None,
                          timeout: float = 0.5) -> CheckResult:
    from .canonical import load_pubkey_b64
    if signer_factory is None:
        from .signers import get_signer
        signer_factory = get_signer

    if not keyring_path.exists():
        return _warn("Signer self-test",
                     "keyring.json missing — can't exercise signers")
    try:
        keyring = _load_keyring(keyring_path)
    except Exception as e:
        return _warn("Signer self-test",
                     f"keyring parse error (skipping): {e}")
    if not keyring:
        return _warn("Signer self-test", "keyring has no kids")

    msg      = b"\x00" * 32
    failures: list[str] = []
    ok_count = 0
    for kid, entry in keyring.items():
        if entry.get("revokedAt"):
            continue  # revoked kids shouldn't sign

        try:
            signer = signer_factory(kid)
        except Exception as e:
            failures.append(f"{kid}: construct failed — {e}")
            continue

        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                sig = ex.submit(signer.sign, msg).result(timeout=timeout)
        except _FuturesTimeout:
            failures.append(f"{kid}: timeout after {timeout}s")
            continue
        except Exception as e:
            failures.append(f"{kid}: sign() raised — {e}")
            continue

        # §2.4: verify against the KEYRING's pubkey, not signer.public_key_bytes().
        # This catches backend/keyring divergence (rotated in backend, not in keyring).
        try:
            pub = load_pubkey_b64(entry["publicKey"])
            pub.verify(sig, msg)
        except Exception as e:
            failures.append(
                f"{kid}: signature does not verify against keyring pubkey — {e}"
            )
            continue

        ok_count += 1

    if failures:
        return _fail("Signer self-test",
                     "; ".join(failures),
                     remedy="Check DWC_SIGNERS config and backend availability; rotate if the backend key has diverged from keyring.json")
    return _pass("Signer self-test",
                 f"{ok_count} signer(s) signed and verified against keyring")


def check_keys_priv_absent(cwd: Path) -> CheckResult:
    """WARN-only: flag plaintext private keys on disk when the signer
    backend is anything other than `local`."""
    env = os.environ.get("DWC_SIGNERS")
    if not env:
        # Default dev path uses keys.priv.json — not a warning in that mode.
        return _pass("Plaintext private keys",
                     "using keys.priv.json default (dev mode)")
    priv = cwd / "keys.priv.json"
    if priv.exists():
        return _warn("Plaintext private keys",
                     f"{priv} present alongside non-local backend",
                     remedy="Run `rm keys.priv.json` — this file contains plaintext private keys and is no longer needed")
    return _pass("Plaintext private keys", "no plaintext key file")


def check_hosted_schemas(fetch_url: Callable[[str], bytes]) -> CheckResult:
    """Byte-compare every local DWC schema against its hosted copy.
    Network errors are WARN (common behind a corporate proxy); actual
    fetched-but-diverged bytes are FAIL."""
    import hashlib
    from .validate import DWC_SCHEMAS, HOSTED_SCHEMA_BASE

    drifts: list[str] = []
    net_errs: list[str] = []
    for path in DWC_SCHEMAS.values():
        local = path.read_bytes()
        lh    = hashlib.sha256(local).hexdigest()
        url   = f"{HOSTED_SCHEMA_BASE}/{path.name}"
        try:
            remote = fetch_url(url)
        except Exception as e:
            net_errs.append(f"{path.name}: {e}")
            continue
        rh = hashlib.sha256(remote).hexdigest()
        if lh != rh:
            drifts.append(
                f"{path.name} (local={lh[:12]} hosted={rh[:12]})"
            )

    if drifts:
        return _fail("Hosted schema drift",
                     "; ".join(drifts),
                     remedy="Pull the hosted version or publish your local schema change (schemas are immutable once published)")
    if net_errs:
        return _warn("Hosted schema drift",
                     "could not verify — network unavailable: " + net_errs[0],
                     remedy="If behind a proxy, check egress to ns.the-dwc.com; otherwise rerun online")
    return _pass("Hosted schema drift",
                 f"{len(DWC_SCHEMAS)} schema(s) byte-match ns.the-dwc.com")


def check_watch_state(cwd: Path) -> CheckResult:
    """§2.3b: field name is `processed_mhl_sha256` (a list). The `emitted`
    field is read if present and must default to [] when absent."""
    path = cwd / ".watch-state.json"
    if not path.exists():
        return _pass(".watch-state.json", "not present (no watcher running here)")
    try:
        state = json.loads(path.read_text())
    except Exception as e:
        return _fail(".watch-state.json",
                     f"parse error: {e}",
                     remedy="Delete the file to reset; the watcher will rebuild it")
    processed = state.get("processed_mhl_sha256")
    if processed is None:
        return _fail(".watch-state.json",
                     "missing `processed_mhl_sha256` field",
                     remedy="Delete the file to reset (old format or corrupted)")
    if not isinstance(processed, list):
        return _fail(".watch-state.json",
                     f"`processed_mhl_sha256` is {type(processed).__name__}, expected list",
                     remedy="Delete the file to reset")
    # emitted is optional — default to [] if absent (for backward compat)
    emitted = state.get("emitted", [])
    if not isinstance(emitted, list):
        return _fail(".watch-state.json",
                     f"`emitted` is {type(emitted).__name__}, expected list",
                     remedy="Delete the file to reset")
    return _pass(".watch-state.json",
                 f"{len(processed)} processed MHL(s), {len(emitted)} recent emission(s)")


def check_sidecars_parse(cwd: Path, *,
                         retries: int = 2,
                         retry_delay: float = 0.05) -> CheckResult:
    """§2.3c: retry on partial write (doctor may race with dwc watch)."""
    sidecars = _iter_sidecars(cwd)
    if not sidecars:
        return _pass("Sidecar parse", "no *.omc.json in CWD")

    failed: list[str] = []
    for p in sidecars:
        doc = None
        last_err = None
        for attempt in range(retries + 1):
            try:
                doc = json.loads(p.read_text())
                last_err = None
                break
            except Exception as e:
                last_err = str(e)
                if attempt < retries:
                    time.sleep(retry_delay)
        if doc is None:
            failed.append(f"{p.name}: {last_err}")
            continue
        # Must contain at least one dwc.sidecar.* customData block
        has_dwc = False
        for cd in _find_custom_data(doc):
            for entry in cd:
                dom = (entry or {}).get("domain") if isinstance(entry, dict) else None
                if isinstance(dom, str) and dom.startswith("dwc.sidecar."):
                    has_dwc = True
                    break
            if has_dwc:
                break
        if not has_dwc:
            failed.append(f"{p.name}: no customData[dwc.sidecar.*] block")

    if failed:
        return _fail("Sidecar parse",
                     "; ".join(failed),
                     remedy="Regenerate or repair the flagged sidecars")
    return _pass("Sidecar parse",
                 f"{len(sidecars)} sidecar(s) parsed with a dwc.sidecar.* block")


def check_key_expiry_window(keyring_path: Path, now: datetime, *,
                            warn_days: int = 30) -> CheckResult:
    if not keyring_path.exists():
        return _warn("Key expiry window",
                     "keyring.json missing (covered by check 4)")
    try:
        keyring = _load_keyring(keyring_path)
    except Exception as e:
        return _warn("Key expiry window",
                     f"keyring parse error (skipping): {e}")

    expired: list[str] = []
    soon:    list[str] = []
    horizon = timedelta(days=warn_days)
    for kid, entry in keyring.items():
        if entry.get("revokedAt"):
            continue
        vto = _parse_iso(entry.get("validUntil"))
        if vto is None:
            continue
        remaining = vto - now
        if remaining < timedelta(0):
            expired.append(f"{kid} (expired {(-remaining).days}d ago)")
        elif remaining < horizon:
            soon.append(f"{kid} ({remaining.days}d remaining)")

    if expired:
        return _fail("Key expiry window",
                     f"expired: {', '.join(expired)}",
                     remedy="Rotate via `dwc keygen` + keyring.json update")
    if soon:
        return _warn("Key expiry window",
                     f"expiring within {warn_days}d: {', '.join(soon)}",
                     remedy="Plan a rotation — keys are immutable once published; new kid, keep old in keyring for event verification")
    return _pass("Key expiry window",
                 f"no kids expire within {warn_days}d")


# ── Orchestrator + formatters ───────────────────────────────────────────


def _default_fetch_url(url: str) -> bytes:
    import urllib.request
    with urllib.request.urlopen(url, timeout=15) as r:
        return r.read()


def run_all_checks(*,
                   cwd: Path | None = None,
                   quick: bool = False,
                   now: datetime | None = None,
                   signer_factory: Callable | None = None,
                   fetch_url: Callable[[str], bytes] | None = None) -> list[CheckResult]:
    cwd       = cwd or Path.cwd()
    now       = now or datetime.now(timezone.utc)
    fetch_url = fetch_url or _default_fetch_url

    keyring_path = cwd / "keyring.json"
    sidecars     = _iter_sidecars(cwd)

    results: list[CheckResult] = [
        check_python_version(),
        check_required_packages(),
        check_hash_algs_available(sidecars),
        check_keyring_present(keyring_path),
        check_keyring_validity(keyring_path, sidecars, now),
        check_signer_config(keyring_path),
    ]
    if not quick:
        results.append(
            check_signer_selftest(keyring_path, signer_factory=signer_factory)
        )
    results.append(check_keys_priv_absent(cwd))
    if not quick:
        results.append(check_hosted_schemas(fetch_url=fetch_url))
    results.extend([
        check_watch_state(cwd),
        check_sidecars_parse(cwd),
        check_key_expiry_window(keyring_path, now),
    ])
    return results


def _top_status(results: Iterable[CheckResult]) -> str:
    statuses = [r.status for r in results]
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "pass"


def format_table(results: list[CheckResult]) -> str:
    tag = {"pass": "[PASS]", "warn": "[WARN]", "fail": "[FAIL]"}
    width = max(len(r.title) for r in results) if results else 0
    lines: list[str] = []
    for r in results:
        lines.append(f"{tag[r.status]}  {r.title:<{width}}  {r.detail}")
    non_pass = [r for r in results if r.status != "pass" and r.remedy]
    if non_pass:
        lines.append("")
        lines.append("Remedies:")
        for r in non_pass:
            lines.append(f"  {r.title}:")
            lines.append(f"    {r.remedy}")
    return "\n".join(lines)


def format_json(results: list[CheckResult]) -> dict:
    return {
        "status": _top_status(results),
        "checks": [r.as_dict() for r in results],
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Pre-flight audit of a DWC signing host.")
    ap.add_argument("--quick", action="store_true",
                    help="Skip signer self-test and hosted-schema drift (no network). Budget <200ms.")
    ap.add_argument("--json", action="store_true",
                    help="Emit JSON instead of the compact table (for CI / menu-bar consumption).")
    args = ap.parse_args(argv)

    results = run_all_checks(quick=args.quick)
    if args.json:
        print(json.dumps(format_json(results), indent=2))
    else:
        print(format_table(results))

    return 0 if all(r.status != "fail" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
