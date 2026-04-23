# Silverstack integration

`apply_dwc_metadata.lua` is a Pomfort Silverstack script (9.2.0+, Lua 5.5) that reads DWC sidecars at ingest time and writes six provenance fields onto each asset's custom metadata slots.

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
- `onFinish(assets, resources, workingPath, success)` is registered but currently a no-op — a reconciliation pass for clips imported before the script was installed is future work tied to real-app validation (plan §7.1).

If a clip has no adjacent sidecar, the script silently skips it — Silverstack ingests untracked clips all the time. Malformed JSON logs a warning via `print()` and skips.

## Installation

1. Open Silverstack → Preferences → Scripts. Click the pencil icon to open the script editor.
2. Switch to the **Shared** scope in the top-left dropdown. Scripts in Shared persist across projects.
3. Create a new script named `DWC_ApplyMetadata`. Paste the entire contents of `apply_dwc_metadata.lua`.
4. Save. The hook now fires on every clip ingested into any project on this host.

A future `dwc init` may install this automatically once Silverstack's scripts directory is stable across releases — for now it's a manual paste.

## Assumptions

- **JSON library**: tries `require "dkjson"` first and falls back to a minimal inlined recursive-descent parser covering the subset DWC sidecars use (objects, arrays, strings with escapes including `\uXXXX`, numbers, booleans, `null`). If `dkjson` is available in the Silverstack runtime it's used automatically.
- **`resource:path()`** is the getter for the clip's on-disk path. This follows the idiom in Pomfort's own examples (e.g. `Extract Camera from Clip Name.lua`). Wrapped in `pcall` — if the API differs in a future Silverstack version, the script logs and skips instead of crashing the ingest.
- **`asset:metadata():setCustomN(string)`** is the write surface. Per the Pomfort Lua reference, these setters accept a single String argument and return `String?`; the return is ignored here.

## Real-app validation (UNVERIFIED)

Silverstack 9.2.0 is eight days old at time of writing (SDK v1.0 released 2026-04-15). The following need confirmation against a live Silverstack install before marking this track's §7.1 exit criteria met:

- `dkjson` is or isn't bundled — if absent, the inlined fallback handles it.
- `resource:path()` returns an absolute path, not a Silverstack-internal URI.
- `setCustom1`..`setCustom6` actually surface in the clip grid when the corresponding labels are customised in Preferences.
- `onFinish` is a reasonable place to reconcile skipped clips (or if a different hook should be used).

Findings from a trial Silverstack 9.2 run go into `docs/integration/silverstack.md` alongside screenshots of the clip grid showing all six DWC columns.

## Testing

`tests/test_silverstack_script.py` spawns a `lua` binary via subprocess, stubs `videoClip` / `resource`, and invokes `onStampVideo` against a fixture sidecar. The test is skipped automatically if `lua` isn't on `PATH` — install Lua 5.1+ (any version, the script is portable) to run it.
