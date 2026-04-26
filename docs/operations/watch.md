# `dwc watch`

A long-running watch-folder service that emits one signed `<clip>.omc.json` per clip and a per-day `dwc-columns-YYYY-MM-DD.ale` as DIT tools land MHLs in the production tree. Tool-agnostic — works with any writer that produces a conformant ASC MHL v1 XML or v2 YAML (Silverstack, YoYotta, Hedge, ShotPut Pro, DaVinci Resolve, etc.).

```
dwc watch <production-root> [options]
```

It wraps `dwc mhl-walk` with polling, stability detection, dedup, collision handling, post-emit validation with a quarantine for failures, and `.watch-state.json` for restart-safe resumption.

## Start it

### Foreground (interactive)

```bash
dwc watch /Volumes/Mag_A001/WAR_Day01 --interval 2 --stable 5
```

Prints a timestamped log line per event. Ctrl-C stops cleanly — state is persisted between every cycle so a restart picks up where it left off.

### LaunchAgent (background, starts at login)

`dwc init` installs `~/Library/LaunchAgents/com.the-dwc.sidecar.watch.plist` by default; load it with:

```bash
launchctl load ~/Library/LaunchAgents/com.the-dwc.sidecar.watch.plist
```

Stop and remove with `launchctl unload`. The watch folder is whatever you answered during `dwc init`; edit the plist directly if it changes.

On Linux, `dwc init` writes a systemd user unit (`~/.config/systemd/user/dwc-sidecar-watch.service`) instead. Activate with `systemctl --user enable --now dwc-sidecar-watch.service`.

## Configuration

| Flag                  | Default              | Notes                                                   |
|-----------------------|----------------------|---------------------------------------------------------|
| `<root>`              | (required)           | Production tree to scan                                  |
| `--out-dir DIR`       | `<root>` itself      | Where sidecars are written                              |
| `--interval N`        | 2 seconds            | Poll cadence                                            |
| `--stable N`          | 3 seconds            | Required size/mtime stability before reading an MHL     |
| `--signing-kid KID`   | first kid in keyring | Override which kid signs new sidecars                   |
| `--no-validate`       | off                  | Skip post-emit validation (faster, admits broken sidecars) |
| `--no-emit-ale`       | off                  | Disable per-day ALE emission                            |
| `--quarantine-dir DIR` | `<out-dir>/quarantine` | Where validation-failed sidecars go                   |
| `--amf-dir DIR`       | (auto)               | Override AMF artifact-resolution root                   |
| `--cdl-dir DIR`       | (auto)               | Override CDL artifact-resolution root                   |
| `--fdl PATH`          | (auto)               | Override FDL artifact-resolution                        |

Production defaults (`--interval 2 --stable 5`) are conservative; tighten for testing only.

## How it processes an MHL

1. **Detect**. The poll loop notices a new or modified `.mhl` under `<root>`.
2. **Stabilise**. Wait until size + mtime are unchanged for `--stable` seconds. This avoids reading an MHL the DIT tool is still writing.
3. **Dedup**. Hash the MHL with SHA-256. If we've already processed an MHL with this content hash, skip — `dwc watch` is idempotent.
4. **Walk**. For each clip referenced by the MHL, build a sidecar from the MHL's declared hashes (no clip bytes re-read).
5. **Sign**. The configured signer backend signs every event (canonicalised via RFC 8785 JCS). See [`signer-backends.md`](signer-backends.md).
6. **Emit**. Write `<clip>.omc.json` next to the clip — or under `--out-dir` if specified. Update the per-day ALE.
7. **Validate** (unless `--no-validate`). Run the 9-stage validator on the freshly-written sidecar. On failure, move the sidecar to the quarantine dir and log a `QUARANTINE` line.
8. **Persist state**. Append the MHL hash to `.watch-state.json`. Add the per-clip emission to a bounded ring of recent emissions (cap 100 entries) consumed by the menu-bar status app.

Throughput: ~900 sidecars/sec, dominated by signing and disk writes. The MHL walk is constant-time per clip — adding more clips per MHL doesn't change per-clip cost.

## Collision handling

Two clips with the same name (C-numbers reset per reel on multi-roll shoots) won't overwrite each other. The watcher logs:

- **`REFRESH`** — incoming sidecar bytes are identical to the existing one. Do nothing.
- **`CONFLICT`** — incoming bytes differ from the existing sidecar. Write the new one alongside the old with a hash-prefix suffix (e.g. `A001_C042_0420AB.60aadd4f.omc.json`). Both files keep their hash-addressed identity; downstream consumers can disambiguate.

A timestamp-suffix collision resolver would silently mask a genuine hash disagreement — see [`CLAUDE.md`](../../CLAUDE.md) → "Conventions a future instance should follow" #3 for why the hash-prefix convention is preserved deliberately.

## State file: `.watch-state.json`

Lives at the **CWD** of `dwc watch`, not the production root. Contents:

```json
{
  "processed_mhl_sha256": ["<sha256-hex>", "<sha256-hex>", ...],
  "emitted": [
    {"clip": "A001_C042_0420AB", "sidecar": "...", "ts": "2026-04-26T12:34:56Z", "status": "ok"},
    ...
  ],
  "savedAt": "2026-04-26T12:34:56Z"
}
```

`processed_mhl_sha256` lets the watcher dedup across restarts. The `emitted` list is a bounded ring (cap 100 entries) — only used by the menu-bar status app for "what happened in the last hour" display; not load-bearing for correctness.

If the file gets corrupted, stop the watcher, delete it, and restart. Every MHL gets reprocessed but resulting sidecars are content-addressed so duplicates collapse via the REFRESH path above.

## Quarantine

Sidecars that fail post-emit validation get moved to `<out-dir>/quarantine/<clip>.omc.json` (configurable via `--quarantine-dir`). The validator's stage results are also written to `<clip>.validation.json` next to the quarantined sidecar so you can see exactly which stages failed.

Common quarantine causes:
- The MHL declared a hash for a clip whose bytes have since changed → Stage 6 (artifact integrity) fails.
- A signing kid expired between sidecar emission and validation → Stage 4 fails.
- An AMF/FDL/CDL the sidecar references is missing from disk → Stage 6 fails on the artifact check.

`--no-validate` skips this entirely; only use it during development. The watch service shouldn't run unvalidated in production.

## Foreground log

Each line is `<ISO-UTC>  <KIND>  <message>`. Kinds you'll see during normal operation:

- `POLL` — every cycle (debug-level; suppressed by default)
- `STABLE` — an MHL met the stability gate
- `EMIT` — `<mhl-name>: N sidecar(s) in <out-dir>/`
- `REFRESH` — already-processed MHL re-detected; skipping
- `CONFLICT` — same-name clip with different hash; wrote with hash-suffix
- `QUARANTINE` — sidecar failed validation; moved to quarantine
- `ERROR` — unrecoverable issue; the watcher continues to the next MHL

A run with no output for >5 minutes is fine — it means no new MHLs landed.

## Stop

Foreground: Ctrl-C. The watcher persists state, prints a summary (`mhls_processed`, `sidecars_written`, `validated_ok`, `quarantined`, `errors`), and exits 0.

LaunchAgent: `launchctl unload ~/Library/LaunchAgents/com.the-dwc.sidecar.watch.plist`.

Force-kill (SIGKILL) is safe — the next start re-reads `.watch-state.json` and resumes. The only cost is potentially reprocessing MHLs that were mid-stabilise at kill time.

## Related

- [`doctor.md`](doctor.md) — pre-flight audit; run before kicking off a long watch session.
- [`signer-backends.md`](signer-backends.md) — what the watcher uses to sign every event.
- [`CLAUDE.md`](../../CLAUDE.md) → "The two ingestion paths" — `dwc watch` vs `dwc batch` (audit mode).
