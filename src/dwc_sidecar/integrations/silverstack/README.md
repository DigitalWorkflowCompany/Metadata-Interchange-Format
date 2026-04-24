# Silverstack integration

`apply_dwc_metadata.lua` is a Pomfort Silverstack script (9.2.0+, Lua 5.5) that reads DWC sidecars at ingest time and writes six provenance fields onto each asset's custom metadata slots. **Validated against Silverstack XT 9.2.1 on 2026-04-24 (plan §7.1 dry-run).**

## What it writes

Silverstack exposes six custom-metadata setters per asset (`setCustom1`..`setCustom6`). The script maps the six highest-signal DWC fields into those slots:

| Slot      | Field              | Example                   |
|-----------|--------------------|---------------------------|
| `Custom1` | `DWC_Signed`       | `true` / `false`          |
| `Custom2` | `DWC_Kid`          | `dwc-dit-01`              |
| `Custom3` | `DWC_Events`       | `4`                       |
| `Custom4` | `DWC_LockedBy`     | `dwc-post-01` (or empty)  |
| `Custom5` | `DWC_LastVerified` | `2026-04-23T14:02:11Z`    |
| `Custom6` | `DWC_ChainHead`    | `60aadd4ffa6e`            |

The two DWC columns not written here — `DWC_Locks` (count) and `DWC_SidecarPath` — are available in the matching per-day ALE (`dwc ale-export`) for tools that support arbitrary columns.

Rename the Custom column labels in Silverstack → Preferences → Custom Metadata so they display as `DWC_Signed`, `DWC_Kid`, etc. Silverstack 8+ remembers these labels across project sessions, so this is a one-time setup per installation.

## How it runs

- `onStampVideo(videoClip, clipIndex, resource)` fires per clip during ingest. The script resolves the sidecar path by convention: `<clip-basename>.omc.json` next to the clip file.
- `onFinish(assets, resources, workingPath, success)` is registered but currently a no-op — a reconciliation pass for clips imported before the script was installed is future work.

If a clip has no adjacent sidecar, the script silently skips it — Silverstack ingests untracked clips all the time. Malformed JSON logs a warning via `print()` and skips.

## Installation

Silverstack 9.2+ scripts are managed through a dedicated editor (**not** under Preferences), and ingest scripts must be attached to a workflow's **Register in Library** activity before their hooks fire. Pasting into the Shared scope alone is insufficient.

1. Open Silverstack's top-level **Script** menu → open the script editor.
2. Switch scope to **Shared** (top-left dropdown) — Shared scripts persist across projects.
3. Create a new script named `DWC_ApplyMetadata`. Paste the entire contents of `apply_dwc_metadata.lua`. Save.
4. Open (or create) an Offload Workflow. In the **Register in Library** activity, scroll to the **Metadata Adjustment Scripts** panel, click **+ Add Lua Script → Shared → DWC_ApplyMetadata**. Save.
5. The hook now fires on every clip ingested through that workflow.

A future `dwc init` may install the script file automatically once Silverstack's on-disk scripts directory is documented; for now it's a manual paste.

## Assumptions (confirmed against Silverstack XT 9.2.1)

- **Context tag.** The first line of the file must be `-- sst: ingest`. Without it, Silverstack classifies the script as a working-copy/template and it does not appear in the **Metadata Adjustment Scripts** dropdown.
- **Sandboxed `_ENV`.** The sandbox disposes or re-chains script-level globals between load and hook-fire, so the script's `dwc` helper table is declared **local** and captured as an upvalue by `onStampVideo`. A script-level global (`dwc = {}`) causes `'__index' chain too long; possible loop` when the hook fires — also the symptom of any access to a nonexistent method or field on Silverstack's userdata objects.
- **`resource:getPath()`** is the documented getter (SDK reference § FileResource → `:getPath() -> String?`). The earlier assumption of `:path()` in this README was wrong and produced the same `__index` chain error. Call via `pcall` so future API drift degrades to log-and-skip rather than crashing ingest.
- **`asset:metadata():setCustomN(string)`** is the write surface. Per the SDK reference these setters accept a single String argument and return `String?`; the return is ignored here.
- **JSON library.** Tries `require "dkjson"` first and falls back to a minimal inlined recursive-descent parser covering the subset DWC sidecars use (objects, arrays, strings with escapes including `\uXXXX`, numbers, booleans, `null`). The fallback path is the one we rely on in testing; whether Silverstack 9.2.x ships `dkjson` hasn't been observed directly because the fallback works transparently.
- **`io.open`.** Works as plain Lua 5.5 `io.open` in the Silverstack sandbox — the script reads the adjacent sidecar via standard file I/O, matching the SDK's § 2.5 "Processing external data" statement.

## Testing

`tests/test_silverstack_script.py` spawns a `lua` binary via subprocess, stubs `videoClip` / `resource`, and invokes `onStampVideo` against a fixture sidecar. The test is skipped automatically if `lua` isn't on `PATH` — install Lua 5.1+ (any version, the script is portable) to run it.

For a live-run sanity check outside Silverstack, `/tmp/dwc-silverstack-dryrun/drive.lua` (created during the §7.1 dry-run) is a throwaway harness that reproduces the same setCustom1..6 calls from the command line against a real fixture clip + sidecar pair.
