#!/usr/bin/env python3
"""DWC sidecar watch-folder service.

Monitors a directory tree for new/modified ASC MHL files. When an MHL's size
has been stable for N seconds, parses it and emits OMC v2.8 + DWC sidecars for
every clip it references. Uses the MHL's declared hashes directly — no clip
bytes re-read.

Tool-agnostic: handles any writer that produces a conformant MHL v1 XML or
v2 YAML (Silverstack, YoYotta, Hedge, ShotPut Pro, DaVinci Resolve, etc.).

Runs in the foreground. Ctrl-C to stop.

Usage:
  python3 watch.py <production-root> [--out-dir sidecars-watched]
                                      [--interval 2] [--stable 3]
                                      [--signing-kid dwc-dit-01]
"""
import argparse, base64, hashlib, json, signal, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .mhl_walker import (
    build_sidecar_from_mhl_entry, _pick_hash_from_mhl_entry, CLIP_EXTS,
)
from .mhl       import parse_mhl
from .canonical import HASH_ALGS

PRIV_KEYS = Path("keys.priv.json")
STATE     = Path(".watch-state.json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _log(kind: str, msg: str) -> None:
    print(f"{_now_iso()}  {kind:<8} {msg}", flush=True)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Watcher:
    def __init__(self, root: Path, out_dir: Path, amf_dir, cdl_dir, fdl,
                 priv, kid: str,
                 poll_interval: float, stable_seconds: float,
                 validate_each: bool, quarantine_dir: Path):
        self.root           = root
        self.out_dir        = out_dir
        self.amf_dir        = amf_dir
        self.cdl_dir        = cdl_dir
        self.fdl            = fdl
        self.priv           = priv
        self.kid            = kid
        self.poll_interval  = poll_interval
        self.stable_seconds = stable_seconds
        self.validate_each  = validate_each
        self.quarantine_dir = quarantine_dir

        # path -> {"size": int, "mtime": float, "last_changed": float}
        self._seen: dict[str, dict] = {}
        # sha256 hashes of MHLs we've already emitted sidecars for
        self._processed: set[str] = set()
        self._stats = {"mhls_processed": 0, "sidecars_written": 0,
                        "refreshed": 0, "conflicts": 0,
                        "validated_ok": 0, "quarantined": 0, "errors": 0}
        self._load_state()

    # ---------- state persistence ----------

    def _load_state(self):
        if STATE.exists():
            try:
                data = json.loads(STATE.read_text())
                self._processed = set(data.get("processed_mhl_sha256", []))
                _log("RESUME", f"{len(self._processed)} MHL(s) previously processed")
            except Exception:
                pass

    def _save_state(self):
        STATE.write_text(json.dumps({
            "processed_mhl_sha256": sorted(self._processed),
            "savedAt": _now_iso(),
        }, indent=2) + "\n")

    # ---------- scan loop ----------

    def run(self):
        _log("WATCH",   f"root={self.root}")
        _log("WATCH",   f"out={self.out_dir} interval={self.poll_interval}s stable={self.stable_seconds}s")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self._scan_once()
            except Exception as e:
                _log("ERROR", f"scan: {e}")
            time.sleep(self.poll_interval)

    def _scan_once(self):
        now = time.time()
        mhls = list(self.root.rglob("*.mhl")) + list(self.root.rglob("*.ascmhl"))
        for mhl in mhls:
            try:
                st = mhl.stat()
            except FileNotFoundError:
                continue

            key  = str(mhl)
            prev = self._seen.get(key)
            if prev is None:
                self._seen[key] = {"size": st.st_size, "mtime": st.st_mtime,
                                    "last_changed": now}
                _log("DETECT", f"{mhl.relative_to(self.root)}  ({st.st_size} bytes)")
                continue

            if st.st_size != prev["size"] or st.st_mtime != prev["mtime"]:
                # still being written
                prev.update({"size": st.st_size, "mtime": st.st_mtime, "last_changed": now})
                continue

            # unchanged — has it been stable long enough?
            if now - prev["last_changed"] < self.stable_seconds:
                continue

            # stable → hash and decide
            try:
                sha = _sha256(mhl)
            except Exception as e:
                _log("ERROR", f"hash {mhl.name}: {e}")
                self._stats["errors"] += 1
                continue

            if sha in self._processed:
                # mark so we don't log it again next loop
                prev["processed_sha"] = sha
                continue

            _log("STABLE", f"{mhl.relative_to(self.root)}  sha256:{sha[:16]}…")
            self._process(mhl, sha)

    # ---------- processing ----------

    def _process(self, mhl: Path, mhl_sha: str):
        try:
            parsed = parse_mhl(mhl)
        except Exception as e:
            _log("ERROR", f"parse {mhl.name}: {e}")
            self._stats["errors"] += 1
            return

        written = 0
        for entry in parsed.get("Hashes") or []:
            f = entry.get("File")
            if not f:
                continue
            if Path(f).suffix.lower() not in CLIP_EXTS:
                continue
            clip_abs = (mhl.parent / f).resolve()
            if not clip_abs.exists():
                continue
            picked = _pick_hash_from_mhl_entry(entry)
            if picked is None:
                continue
            alg, val = picked
            if alg not in HASH_ALGS:
                continue
            try:
                doc = build_sidecar_from_mhl_entry(
                    mhl, f, clip_abs, alg, val,
                    self.root, self.amf_dir, self.cdl_dir, self.fdl,
                    self.priv, self.kid,
                )
            except Exception as e:
                _log("ERROR", f"build {clip_abs.name}: {e}")
                self._stats["errors"] += 1
                continue
            target = self.out_dir / f"{clip_abs.stem}.omc.json"
            out, action = self._resolve_collision(target, doc, mhl_sha)
            if out is None:
                continue  # REFRESH: existing sidecar is identical, do nothing

            out.write_text(json.dumps(doc, indent=2) + "\n")
            written += 1
            if action == "conflict":
                _log("CONFLICT", f"{clip_abs.stem}: wrote {out.name} alongside existing "
                                   f"({self._stats['conflicts']} total)")

            if self.validate_each:
                ok, log = self._validate(out)
                if ok:
                    self._stats["validated_ok"] += 1
                    _log("VALIDATE", f"{out.name}: OK")
                else:
                    self._quarantine(out, log)
                    self._stats["quarantined"] += 1

        self._processed.add(mhl_sha)
        self._save_state()
        self._stats["mhls_processed"]  += 1
        self._stats["sidecars_written"] += written
        _log("EMIT", f"{mhl.name}: {written} sidecar(s) in {self.out_dir.name}/")

    @staticmethod
    def _clip_integrity_hash(doc) -> tuple[str, str] | None:
        """(alg, value) of the clip-integrity artifact in a sidecar doc, or None."""
        for asset in doc.get("Asset", []):
            cd = (asset.get("assetFC") or {}).get("functionalProperties", {}).get("customData", [])
            for group in cd:
                if group.get("domain") != "dwc.sidecar.artifacts":
                    continue
                for a in group.get("value") or []:
                    if a.get("role") == "clip-integrity":
                        h = a.get("hash") or {}
                        if h.get("alg") and h.get("value"):
                            return h["alg"], h["value"]
        return None

    def _resolve_collision(self, target: Path, new_doc, mhl_sha: str):
        """Decide where to write. Returns (path | None, action):
             ('write')    — first sidecar for this clip
             ('refresh')  — existing sidecar declares the same clip-integrity hash; keep it
             ('conflict') — two MHLs disagree on the clip's hash; suffix both
        """
        if not target.exists():
            return target, "write"
        try:
            existing = json.loads(target.read_text())
        except Exception:
            return target, "write"  # unreadable → overwrite

        new_ci = self._clip_integrity_hash(new_doc)
        old_ci = self._clip_integrity_hash(existing)
        if new_ci is None or old_ci is None:
            return target, "write"  # can't compare → overwrite

        if new_ci == old_ci:
            _log("REFRESH", f"{target.name}: identical clip hash, existing sidecar retained")
            self._stats["refreshed"] += 1
            return None, "refresh"

        # CONFLICT: two MHLs disagree. Preserve the existing one by renaming, then
        # write the new one under its own suffix. Neither gets the "clean" filename —
        # that's visible evidence of disagreement.
        stem = target.stem.replace(".omc", "")
        existing_suffix  = old_ci[1][:8]
        new_suffix       = new_ci[1][:8]
        preserved = target.with_name(f"{stem}.{existing_suffix}.omc.json")
        new_path  = target.with_name(f"{stem}.{new_suffix}.omc.json")
        if target.exists() and not preserved.exists():
            target.rename(preserved)
            _log("CONFLICT", f"{stem}: preserved existing as {preserved.name} "
                              f"(clip hash {old_ci[0]}={old_ci[1][:16]}…)")
        self._stats["conflicts"] += 1
        return new_path, "conflict"

    def _validate(self, sidecar: Path) -> tuple[bool, str]:
        r = subprocess.run(
            ["python3", "-m", "dwc_sidecar.validate",
             str(sidecar), "--base-dir", str(self.root), "--trust-mhl"],
            capture_output=True, text=True,
        )
        return r.returncode == 0, r.stdout + r.stderr

    def _quarantine(self, sidecar: Path, log: str) -> None:
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        target = self.quarantine_dir / sidecar.name
        sidecar.rename(target)
        target.with_suffix(".log.txt").write_text(log)
        # Extract the last non-empty line for a compact failure reason
        tail = [ln for ln in log.strip().splitlines() if ln.strip()][-1:] or ["(no log)"]
        _log("QUARANTINE", f"{target.name}: {tail[0]}")

    def summary(self):
        s = self._stats
        _log("SUMMARY",
             f"mhls={s['mhls_processed']} sidecars={s['sidecars_written']} "
             f"refreshed={s['refreshed']} conflicts={s['conflicts']} "
             f"valid={s['validated_ok']} quarantined={s['quarantined']} errors={s['errors']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--out-dir",  type=Path, default=Path("sidecars-watched"))
    ap.add_argument("--amf-dir",  type=Path, default=None)
    ap.add_argument("--cdl-dir",  type=Path, default=None)
    ap.add_argument("--fdl",      type=Path, default=None)
    ap.add_argument("--interval", type=float, default=2.0, help="Poll interval (seconds)")
    ap.add_argument("--stable",   type=float, default=3.0,
                     help="Require the MHL size/mtime to be unchanged for this long before processing")
    ap.add_argument("--signing-kid", default="dwc-dit-01")
    ap.add_argument("--no-validate", action="store_true",
                     help="Skip post-emit validation (faster, but silently admits broken sidecars)")
    ap.add_argument("--quarantine-dir", type=Path, default=None,
                     help="Where failed sidecars go (default: <out-dir>/../quarantine)")
    args = ap.parse_args()

    root = args.root.resolve()
    amf  = args.amf_dir or (root / "Colour-Information/AMF")
    cdl  = args.cdl_dir or (root / "Colour-Information/CDLs/CDL_Output")
    fdl  = args.fdl
    if fdl is None:
        fdl_dir = root / "Colour-Information/FDL"
        if fdl_dir.exists():
            fdls = list(fdl_dir.glob("*.fdl"))
            fdl = fdls[0] if fdls else None
    amf  = amf if amf and amf.exists() else None
    cdl  = cdl if cdl and cdl.exists() else None

    priv_bundle = json.loads(PRIV_KEYS.read_text())
    priv = Ed25519PrivateKey.from_private_bytes(base64.b64decode(priv_bundle[args.signing_kid]))

    quarantine = args.quarantine_dir or (args.out_dir.parent / "quarantine")
    w = Watcher(root, args.out_dir, amf, cdl, fdl, priv, args.signing_kid,
                 args.interval, args.stable,
                 validate_each=not args.no_validate,
                 quarantine_dir=quarantine)

    def _stop(signum, frame):
        print()
        w.summary()
        sys.exit(0)
    signal.signal(signal.SIGINT,  _stop)
    signal.signal(signal.SIGTERM, _stop)

    try:
        w.run()
    except KeyboardInterrupt:
        _stop(None, None)


if __name__ == "__main__":
    main()
