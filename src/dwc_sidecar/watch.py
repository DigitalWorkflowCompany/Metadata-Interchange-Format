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
import argparse, base64, hashlib, json, os, signal, subprocess, sys, tempfile, time
from datetime import datetime, timezone
from pathlib import Path
from ._io        import atomic_write_text
from .mhl_walker import (
    build_sidecar_from_mhl_entry, _pick_hash_from_mhl_entry, CLIP_EXTS,
)
from .mhl         import parse_mhl
from .canonical   import HASH_ALGS
from .signers     import get_signer
from .ale_emitter import ale_path_for_day, extract_row_from_sidecar, update_ale

STATE = Path(".watch-state.json")

# Keep the recent-emissions ring bounded so the state file stays O(1) in size
# across a multi-day shoot (plan §1.8). The menu-bar app (§3) reads this list.
EMITTED_CAP = 100


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


# Shared crash-safe writer (moved to _io so append/lock/transfer/bundle reuse it).
# Kept as a module-level name so existing callers and tests stay unchanged.
_atomic_write_text = atomic_write_text


class Watcher:
    def __init__(self, root: Path, out_dir: Path, amf_dir, cdl_dir, fdl,
                 signer,
                 poll_interval: float, stable_seconds: float,
                 validate_each: bool, quarantine_dir: Path,
                 emit_ale: bool = True):
        self.root           = root
        self.out_dir        = out_dir
        self.amf_dir        = amf_dir
        self.cdl_dir        = cdl_dir
        self.fdl            = fdl
        self.signer         = signer
        self.poll_interval  = poll_interval
        self.stable_seconds = stable_seconds
        self.validate_each  = validate_each
        self.quarantine_dir = quarantine_dir
        self.emit_ale       = emit_ale

        # path -> {"size": int, "mtime": float, "last_changed": float}
        self._seen: dict[str, dict] = {}
        # sha256 hashes of MHLs we've already emitted sidecars for
        self._processed: set[str] = set()
        # Recent-emissions ring for the menu-bar app — {clipName, omcPath, signedAt, status}
        self._emitted: list[dict] = []
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
                # `emitted` was added in v0.3.0; state files from older watchers
                # default to [] so a rollback stays readable (plan §1.8 contract).
                loaded_emitted = data.get("emitted") or []
                if isinstance(loaded_emitted, list):
                    self._emitted = loaded_emitted[-EMITTED_CAP:]
                _log("RESUME", f"{len(self._processed)} MHL(s) previously processed, "
                               f"{len(self._emitted)} recent emission(s)")
            except Exception as e:
                # A reset processed-set means every MHL re-emits — never do
                # that silently. (Writes are atomic, so this is rare.)
                _log("WARN", f"state file {STATE} unreadable ({e}) — starting with an "
                             f"empty processed set; existing sidecars will be re-evaluated")

    def _save_state(self):
        _atomic_write_text(STATE, json.dumps({
            "processed_mhl_sha256": sorted(self._processed),
            "emitted":              self._emitted[-EMITTED_CAP:],
            "savedAt":              _now_iso(),
        }, indent=2) + "\n")

    def _record_emission(self, clip_name: str, omc_path: Path, status: str):
        self._emitted.append({
            "clipName": clip_name,
            "omcPath":  str(omc_path),
            "signedAt": _now_iso(),
            "status":   status,
        })
        if len(self._emitted) > EMITTED_CAP:
            del self._emitted[:-EMITTED_CAP]

    def _emit_ale_row(self, sidecar_path: Path, signed: bool) -> None:
        """ALE is a derived view of the sidecar; failures here must never
        block sidecar emission. Logged WARN, swallowed."""
        if not self.emit_ale:
            return
        try:
            now = datetime.now(timezone.utc)
            ale_path = ale_path_for_day(self.out_dir, now)
            row = extract_row_from_sidecar(sidecar_path, now=now,
                                           signed=signed, ale_dir=ale_path.parent)
            update_ale(ale_path, row, now=now)
        except Exception as e:
            _log("ALE",  f"update failed for {sidecar_path.name}: {e}")

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
                # still being written (or rewritten — drop the processed marker
                # so the new content gets hashed once it stabilises)
                prev.update({"size": st.st_size, "mtime": st.st_mtime, "last_changed": now})
                prev.pop("processed_sha", None)
                continue

            # already processed at this size/mtime — skip without re-hashing.
            # Without this short-circuit every stable MHL gets a full SHA-256
            # per poll cycle: real recurring I/O on a multi-thousand-MHL tree.
            if "processed_sha" in prev:
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
                # mark so we don't hash or log it again next loop
                prev["processed_sha"] = sha
                continue

            _log("STABLE", f"{mhl.relative_to(self.root)}  sha256:{sha[:16]}…")
            self._process(mhl, sha)
            if sha in self._processed:   # only short-circuit successes — parse
                prev["processed_sha"] = sha   # failures keep retrying each cycle

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
                    self.signer,
                )
            except Exception as e:
                _log("ERROR", f"build {clip_abs.name}: {e}")
                self._stats["errors"] += 1
                continue
            target = self.out_dir / f"{clip_abs.stem}.omc.json"
            out, action = self._resolve_collision(target, doc, mhl_sha)
            if out is None:
                continue  # REFRESH: existing sidecar is identical, do nothing

            _atomic_write_text(out, json.dumps(doc, indent=2) + "\n")
            written += 1
            if action == "conflict":
                _log("CONFLICT", f"{clip_abs.stem}: wrote {out.name} alongside existing "
                                   f"({self._stats['conflicts']} total)")

            signed = True  # watcher just signed; presume good unless validator disagrees
            emission_status = "signed"
            if self.validate_each:
                ok, log = self._validate(out)
                if ok:
                    self._stats["validated_ok"] += 1
                    _log("VALIDATE", f"{out.name}: OK")
                else:
                    self._quarantine(out, log)
                    self._stats["quarantined"] += 1
                    signed = False
                    emission_status = "quarantined"

            # ALE + recent-emissions ring happen after validation so we record
            # the true signed state. Failures inside either path are logged
            # but must not block the next clip (plan §1.6).
            if emission_status != "quarantined":
                self._emit_ale_row(out, signed=signed)
            self._record_emission(clip_abs.stem, out, emission_status)

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

    def _suffixed_path(self, target: Path, stem: str, ci: tuple[str, str]) -> Path:
        """Filename for a disputed sidecar, suffixed by its clip-integrity hash
        (CLAUDE.md convention #3: the suffix IS the disambiguator). A prefix
        collision between two *different* full hashes lengthens the prefix
        instead of silently masking a genuine hash disagreement."""
        val = ci[1]
        for n in (8, 16, 32):
            if n >= len(val):
                break
            p = target.with_name(f"{stem}.{val[:n]}.omc.json")
            if not p.exists():
                return p
            try:
                if self._clip_integrity_hash(json.loads(p.read_text())) == ci:
                    return p  # same claim → same file
            except Exception:
                pass  # unreadable occupant → lengthen rather than overwrite
        return target.with_name(f"{stem}.{val}.omc.json")

    def _resolve_collision(self, target: Path, new_doc, mhl_sha: str):
        """Decide where to write. Returns (path | None, action):
             ('write')    — first sidecar for this clip
             ('refresh')  — a sidecar with the same clip-integrity hash exists; keep it
             ('conflict') — MHLs disagree on the clip's hash; every version is suffixed

        Once a clip is disputed (suffixed siblings exist), nothing reclaims the
        clean filename — a third MHL writing back to `stem.omc.json` would look
        like an undisputed sidecar while two contested versions sit next to it."""
        stem = target.stem.replace(".omc", "")
        siblings = [p for p in target.parent.glob(f"{stem}.*.omc.json") if p != target]
        new_ci = self._clip_integrity_hash(new_doc)

        if not target.exists() and not siblings:
            return target, "write"

        # Existing claims: the clean file (if present) plus any suffixed versions
        claims: list[tuple[Path, tuple[str, str] | None]] = []
        for p in ([target] if target.exists() else []) + siblings:
            try:
                claims.append((p, self._clip_integrity_hash(json.loads(p.read_text()))))
            except Exception:
                claims.append((p, None))

        if new_ci is None or all(ci is None for _, ci in claims):
            return target, "write"  # can't compare → overwrite clean name

        match = next((p for p, ci in claims if ci == new_ci), None)
        if match is not None:
            _log("REFRESH", f"{match.name}: identical clip hash, existing sidecar retained")
            self._stats["refreshed"] += 1
            return None, "refresh"

        # CONFLICT: disagreement. Preserve the clean-named version under its own
        # suffix; the new one gets its own suffix. Nobody keeps the "clean"
        # filename — that's visible evidence of disagreement.
        if target.exists():
            old_ci = next(ci for p, ci in claims if p == target)
            if old_ci is not None:
                preserved = self._suffixed_path(target, stem, old_ci)
                if not preserved.exists():
                    target.rename(preserved)
                    _log("CONFLICT", f"{stem}: preserved existing as {preserved.name} "
                                      f"(clip hash {old_ci[0]}={old_ci[1][:16]}…)")
        new_path = self._suffixed_path(target, stem, new_ci)
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
    ap.add_argument("--no-emit-ale", dest="emit_ale", action="store_false",
                     help="Disable per-day ALE emission (default: on — dwc-columns-YYYY-MM-DD.ale in out-dir)")
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

    try:
        signer = get_signer(args.signing_kid)
    except (FileNotFoundError, KeyError) as e:
        print(f"ERROR: {e}", file=sys.stderr); return 2

    quarantine = args.quarantine_dir or (args.out_dir.parent / "quarantine")
    w = Watcher(root, args.out_dir, amf, cdl, fdl, signer,
                 args.interval, args.stable,
                 validate_each=not args.no_validate,
                 quarantine_dir=quarantine,
                 emit_ale=args.emit_ale)

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
