#!/usr/bin/env python3
"""Sealed bundles: `dwc bundle` (create) and `dwc verify` (verify).

A bundle is a single zip carrying the sidecar, the keyring, the referenced
artifact files, and a manifest — everything a recipient needs to verify with
nothing but the file in hand.

The trust problem stated plainly: a bundle that carries its own keyring proves
only *internal* consistency. An attacker can build a perfectly self-consistent
bundle around their own keys. So `dwc verify` needs trust from OUTSIDE the
bundle, and makes the verifier choose, in descending strength:

  --keyring <path>             external keyring the verifier already trusts;
  --keyring-fingerprint <sha>  bundled keyring accepted only if its bytes match
                               a fingerprint received out-of-band;
  --trust-bundled-keyring      explicitly trust the keyring inside the bundle.

With none of these, verify still runs, prints an unmissable banner with the
bundled keyring's fingerprint, and exits NON-ZERO — never a silent pass.

Hard rules in verify (not flags): files are resolved only inside the extracted
bundle, never from the caller's CWD (keyring.json / revocations.json /
keys.priv.json are never picked up from the working directory); a missing
keyring is always FAIL; missing artifact files are FAIL unless the manifest's
declared `--lite` omission and the verifier's `--allow-missing-clips` both agree.
"""
import argparse
import hashlib
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .validate import _safe_path, validate_as_json

BUNDLE_VERSION = 1
TOOL = {"name": "dwc bundle", "version": "0.6.0"}
INTEGRITY_ROLES = {"clip-integrity", "integrity"}


# --------------------------------------------------------------------------- create

def _iter_artifacts(doc):
    """Yield every artifact dict across the doc's dwc.sidecar.artifacts groups."""
    def walk(node):
        if isinstance(node, dict):
            if node.get("domain") == "dwc.sidecar.artifacts" and isinstance(node.get("value"), list):
                for a in node["value"]:
                    if isinstance(a, dict):
                        yield a
            for v in node.values():
                yield from walk(v)
        elif isinstance(node, list):
            for v in node:
                yield from walk(v)
    yield from walk(doc)


def _arcname(art_path: str) -> str:
    """Normalise a sidecar artifact path to a zip-relative arcname."""
    p = art_path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def create_bundle(sidecar: Path, base_dir: Path, out: Path, *, lite: bool = False) -> dict:
    """Build the bundle zip and return its manifest. Pure-ish: writes `out`."""
    sidecar = Path(sidecar).resolve()
    base_dir = Path(base_dir).resolve()
    doc = json.loads(sidecar.read_text())

    keyring = Path("keyring.json")
    if not keyring.exists():
        raise SystemExit("ERROR: keyring.json not found in CWD — a bundle without a keyring "
                         "cannot be verified. Run from the directory holding keyring.json.")
    keyring_bytes = keyring.read_bytes()
    keyring_sha = hashlib.sha256(keyring_bytes).hexdigest()

    members: list[tuple[str, bytes]] = []
    omitted: list[str] = []
    seen: set[str] = set()
    for art in _iter_artifacts(doc):
        raw = art.get("path")
        if not isinstance(raw, str) or not raw:
            continue
        arc = _arcname(raw)
        if arc in seen:
            continue
        seen.add(arc)
        if lite and art.get("role") in INTEGRITY_ROLES:
            omitted.append(arc)
            continue
        src = _safe_path(base_dir, raw)
        if src is None:
            raise SystemExit(f"ERROR: artifact path {raw!r} escapes base-dir {base_dir} — "
                             f"refusing to bundle (containment check).")
        if not src.exists():
            raise SystemExit(f"ERROR: artifact file not found: {src} (declared {raw!r})")
        members.append((arc, src.read_bytes()))

    manifest = {
        "bundleVersion": BUNDLE_VERSION,
        "created": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "tool": TOOL,
        "keyringSha256": keyring_sha,
        "sidecar": sidecar.name,
        "lite": lite,
        "packed": len(members),
        "omitted": omitted,
    }

    out = Path(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(sidecar.name, sidecar.read_bytes())
        z.writestr("keyring.json", keyring_bytes)
        rev = Path("revocations.json")
        if rev.exists():
            z.writestr("revocations.json", rev.read_bytes())
        z.writestr("dwc-bundle.json", json.dumps(manifest, indent=2))
        for arc, data in members:
            z.writestr(arc, data)
    return manifest


def create_main(argv) -> int:
    ap = argparse.ArgumentParser(prog="dwc bundle",
                                  description="Pack a sidecar + keyring + files into a sealed zip")
    ap.add_argument("sidecar", type=Path)
    ap.add_argument("--base-dir", type=Path, required=True,
                     help="Root the artifact paths resolve against")
    ap.add_argument("--out", type=Path, required=True, help="Output .dwcbundle.zip")
    ap.add_argument("--lite", action="store_true",
                     help="Exclude clip-integrity originals (camera RAW is tens of GB). "
                          "Recorded in the manifest so verify demands --allow-missing-clips.")
    args = ap.parse_args(argv)

    manifest = create_bundle(args.sidecar, args.base_dir, args.out, lite=args.lite)
    print(f"✓ wrote {args.out}")
    print(f"  sidecar: {manifest['sidecar']}  artifacts: {manifest['packed']} "
          f"packed, {len(manifest['omitted'])} omitted{' (lite)' if args.lite else ''}")
    print(f"  keyring fingerprint (send this out-of-band to the receiver):")
    print(f"      sha256:{manifest['keyringSha256']}")
    return 0


# --------------------------------------------------------------------------- verify

def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """Zip-slip-safe extraction: refuse any entry whose resolved path escapes
    `dest` (absolute paths, `../` traversal, symlink-style names)."""
    dest = dest.resolve()
    for member in zf.namelist():
        target = (dest / member).resolve()
        if target != dest and not target.is_relative_to(dest):
            raise ValueError(f"unsafe zip entry escapes extraction root: {member!r}")
    zf.extractall(dest)


class BundleTrustError(Exception):
    """Raised before any stage runs when the out-of-band trust check fails."""


def verify_bundle(path, *, keyring: Path | None = None,
                  keyring_fingerprint: str | None = None,
                  trust_bundled: bool = False,
                  allow_missing_clips: bool = False,
                  strict: bool = False) -> dict:
    """Verify a sealed bundle. Returns a report dict:
        {target, trust_mode, keyring_fingerprint, validation, errors, warnings,
         exit_code, verdict}
    Never resolves any file from the caller's CWD. Raises BundleTrustError when
    the fingerprint pin fails (so no stage runs against an unpinned keyring)."""
    import tempfile
    path = Path(path)
    with tempfile.TemporaryDirectory(prefix="dwc-verify-") as td:
        root = Path(td)
        with zipfile.ZipFile(path) as zf:
            _safe_extract(zf, root)

        manifest = {}
        mpath = root / "dwc-bundle.json"
        if mpath.exists():
            manifest = json.loads(mpath.read_text())
        sidecar_name = manifest.get("sidecar")
        sidecar = (root / sidecar_name) if sidecar_name else _guess_sidecar(root)
        if sidecar is None or not sidecar.exists():
            raise BundleTrustError("bundle has no sidecar (manifest.sidecar missing / not found)")

        bundled_keyring = root / "keyring.json"
        bundled_fp = (hashlib.sha256(bundled_keyring.read_bytes()).hexdigest()
                      if bundled_keyring.exists() else None)

        # --- resolve the trust decision (outside the bundle) ---
        trust_mode, keyring_path, banner = _resolve_trust(
            keyring, keyring_fingerprint, trust_bundled, bundled_keyring, bundled_fp)

        revocations = root / "revocations.json"
        lite = bool(manifest.get("lite"))
        report = validate_as_json(
            sidecar, base_dir=root,
            keyring_path=keyring_path,
            revocations_path=revocations if revocations.exists() else (root / "revocations.json"),
            require_keyring=True,
            missing_is_skip=(lite and allow_missing_clips),
        )

        errors = report["errors"]
        warnings = report["warnings"]
        # Lite bundle whose clips are missing but the verifier did NOT opt in →
        # surface it as an error (missing_is_skip was off, so Stage 6 already FAILED;
        # this branch only adds an explicit reason when lite and not allowed).
        exit_code = 1 if errors else 0
        if strict and warnings:
            exit_code = 1
        # No external trust supplied → force an explicit decision: non-zero.
        if trust_mode == "bundled-untrusted":
            exit_code = exit_code or 2

        verdict = _verdict(trust_mode, errors, warnings, bundled_fp, keyring_fingerprint)
        return {
            "target": str(path),
            "trust_mode": trust_mode,
            "keyring_fingerprint": bundled_fp,
            "banner": banner,
            "manifest": manifest,
            "validation": report,
            "errors": errors,
            "warnings": warnings,
            "exit_code": exit_code,
            "verdict": verdict,
        }


def _guess_sidecar(root: Path) -> Path | None:
    cands = sorted(root.glob("*.omc.json"))
    return cands[0] if cands else None


def _resolve_trust(keyring, keyring_fingerprint, trust_bundled, bundled_keyring, bundled_fp):
    """Return (trust_mode, keyring_path_to_use, banner_or_None). May raise
    BundleTrustError for a fingerprint mismatch (fail before any stage runs)."""
    if keyring is not None:
        ext = Path(keyring)
        if not ext.exists():
            raise BundleTrustError(f"--keyring {ext} does not exist")
        return "external", ext, None
    if keyring_fingerprint is not None:
        want = keyring_fingerprint.removeprefix("sha256:")
        if bundled_fp is None:
            raise BundleTrustError("no keyring in bundle to match against --keyring-fingerprint")
        if want != bundled_fp:
            raise BundleTrustError(
                f"keyring fingerprint mismatch: pinned sha256:{want} but bundle carries "
                f"sha256:{bundled_fp} — refusing before any signature is checked")
        return "fingerprint-pinned", bundled_keyring, None
    if bundled_fp is None:
        raise BundleTrustError("bundle carries no keyring and no --keyring was supplied")
    banner = (
        "┌─ TRUST IS SELF-CONTAINED ─────────────────────────────────────────────\n"
        "│ Signatures were verified against the keyring *inside* the bundle.\n"
        "│ Confirm its fingerprint out-of-band (e-mail, transfer manifest, call):\n"
        f"│     sha256:{bundled_fp}\n"
        "│ Re-run with --keyring <trusted> or --keyring-fingerprint <sha> to pin,\n"
        "│ or --trust-bundled-keyring to accept it explicitly.\n"
        "└───────────────────────────────────────────────────────────────────────")
    if trust_bundled:
        return "bundled-trusted", bundled_keyring, banner
    return "bundled-untrusted", bundled_keyring, banner


def _verdict(trust_mode, errors, warnings, bundled_fp, pinned) -> str:
    pinned_hex = (pinned or "").removeprefix("sha256:")
    how = {
        "external": "external keyring",
        "fingerprint-pinned": f"bundled keyring pinned to sha256:{pinned_hex}",
        "bundled-trusted": f"bundled keyring (trusted by flag) sha256:{bundled_fp}",
        "bundled-untrusted": f"bundled keyring (UNCONFIRMED) sha256:{bundled_fp}",
    }[trust_mode]
    if errors:
        state = f"FAIL ({errors} error(s))"
    elif trust_mode == "bundled-untrusted":
        state = "signatures OK but trust UNCONFIRMED"
    elif warnings:
        state = f"OK ({warnings} warning(s))"
    else:
        state = "OK"
    return f"{state} — trusted via {how}"


def verify_main(argv) -> int:
    ap = argparse.ArgumentParser(prog="dwc verify",
                                  description="Verify a sealed bundle end-to-end")
    ap.add_argument("bundle", type=Path)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--keyring", type=Path, help="External keyring you already trust (strongest)")
    g.add_argument("--keyring-fingerprint", help="Accept the bundled keyring only if it matches this sha256")
    g.add_argument("--trust-bundled-keyring", action="store_true",
                    help="Explicitly trust the keyring inside the bundle")
    ap.add_argument("--allow-missing-clips", action="store_true",
                     help="Permit a --lite bundle's omitted clip-integrity originals to be absent")
    ap.add_argument("--strict", action="store_true",
                     help="Promote warnings (weak algs, pending transfers) to a non-zero exit")
    args = ap.parse_args(argv)

    try:
        report = verify_bundle(
            args.bundle, keyring=args.keyring,
            keyring_fingerprint=args.keyring_fingerprint,
            trust_bundled=args.trust_bundled_keyring,
            allow_missing_clips=args.allow_missing_clips,
            strict=args.strict)
    except BundleTrustError as e:
        print(f"✗ TRUST CHECK FAILED — {e}", file=sys.stderr)
        return 1
    except (zipfile.BadZipFile, ValueError) as e:
        print(f"✗ bundle error — {e}", file=sys.stderr)
        return 1

    for s in report["validation"]["stages"]:
        for ln in s["lines"]:
            print(ln)
    if report["banner"]:
        print()
        print(report["banner"])
    print()
    print(f"VERDICT: {report['verdict']}")
    return report["exit_code"]


def main() -> int:
    # cli.py routes both `dwc bundle` and `dwc verify` here and rewrites argv[0].
    prog = sys.argv[0]
    if "verify" in prog:
        return verify_main(sys.argv[1:])
    return create_main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
