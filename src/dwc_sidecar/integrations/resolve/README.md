# DaVinci Resolve integration

Two scripts for DaVinci Resolve 20 / 21. **The `MediaPoolItem` surface this integration uses is byte-identical across 20 and 21** — the two additions in 21 (`GetFairlightPresets`, `ApplyFairlightPresetToCurrentTimeline`, plus an optional second argument on `CreateProject`) don't touch any code path here. See the vendor READMEs committed at `resources/documentation/`. Validating against either version covers both.

- `apply_dwc_metadata.py` — walks the active project's Media Pool, matches each clip to a DWC sidecar by filename, and writes the eight `DWC_*` provenance fields via `MediaPoolItem.SetMetadata`.
- `ensure_custom_fields.py` — one-shot that prints the exact UI walk-through Resolve requires before any `SetMetadata` call will persist.

**Validated** against DaVinci Resolve Studio 20.3.2 on macOS 15.7.5 (arm64), 2026-04-24 (plan §7.1 dry-run).

## The pre-existing-field gotcha

Resolve's `MediaPoolItem.SetMetadata(key, value)` silently returns `False` for any field not pre-registered as a custom metadata field in the project. There is **no scripting API to create custom fields** — confirmed against both vendor READMEs.

**Resolve 20.2+** (current path): in the Media or Edit page, select a clip, open the **Metadata** tab, click its **three-dot options menu**, and choose **Create Custom Metadata**. Enter the field name, pick type **Text Input**, and tick **Show in all projects** to avoid redoing setup per project. To review/edit/reorder later, use **Manage Custom Metadata** from the same menu.

**Resolve < 20.2** (legacy): Project Settings → General Options → Metadata & Scene. That panel was removed in 20.2.

The eight fields `apply_dwc_metadata.py` writes are `DWC_Signed`, `DWC_Kid`, `DWC_Events`, `DWC_Locks`, `DWC_LockedBy`, `DWC_LastVerified`, `DWC_SidecarPath`, `DWC_ChainHead`. Run `python3 -m dwc_sidecar.integrations.resolve.ensure_custom_fields` to print the full setup walk-through for a DIT.

## Installation

Copy `apply_dwc_metadata.py` into Resolve's user-scope Utility scripts folder so it shows up in Workspace → Scripts → Utility:

```bash
cp src/dwc_sidecar/integrations/resolve/apply_dwc_metadata.py \
   ~/Library/Application\ Support/Blackmagic\ Design/DaVinci\ Resolve/Fusion/Scripts/Utility/
```

Windows / Linux paths differ — see the vendor README at `resources/documentation/`.

Inside Resolve, open the project, then run **Workspace → Scripts → Utility → apply_dwc_metadata**. Resolve populates a global `resolve` object for scripts launched from the menu; the script picks that up without a `RESOLVE_SCRIPT_LIB` round-trip. The menu-invocation path does **not** yet take a sidecar directory argument — the headless path below is the supported surface for passing a sidecar dir.

## Headless invocation

For dailies pipelines and CI that drive Resolve externally:

```bash
export RESOLVE_SCRIPT_API="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
export RESOLVE_SCRIPT_LIB="/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
export PYTHONPATH="$RESOLVE_SCRIPT_API/Modules/:$PYTHONPATH"
python3 -m dwc_sidecar.integrations.resolve.apply_dwc_metadata /path/to/sidecar-dir
```

All three env vars are required: `RESOLVE_SCRIPT_API` points at the Scripting folder, `RESOLVE_SCRIPT_LIB` at `fusionscript.{so,dll,dylib}`, and `PYTHONPATH` must include `$RESOLVE_SCRIPT_API/Modules/` so `import DaVinciResolveScript` works. Vendor paths for Windows and Linux are in the committed vendor README.

Resolve must be running with the target project open. Resolve's Preferences may also need **External scripting using** set to **Local** (see Preferences → System → General) if the Console-only default is in effect.

## How matching works

Sidecars are matched to clips by filename via a substring scorer ported from the prior art in `~/Documents/Resolve-Tools/Import-AMF/Import_AMF.py:950–1020`. Scores:

| Score | Condition                                     |
|-------|-----------------------------------------------|
| 100   | Sidecar stem == clip name                     |
| 90    | Sidecar stem ⊂ clip name                      |
| 85    | Clip name ⊂ sidecar stem                      |
| 80    | Sidecar filename (without ext) ⊂ clip name    |
| 75    | Clip name ⊂ sidecar filename                  |
| 70    | File-path basename ⊂ clip name                |
| 65    | Clip name ⊂ file-path basename                |

Threshold 65; one clip matches at most one sidecar (reruns are stable).

`SetThirdPartyMetadata` is intentionally not used. The vendor README exposes it as a separate namespace, but it doesn't appear in the substantial prior art at `~/Documents/Resolve-Tools/` — suggesting it's either broken or not surfaced in Resolve's UI. `SetMetadata` with the pre-existing-field constraint is the reliable path.

## Field-name convention

Underscores (`DWC_Signed`), never hyphens (`DWC-Signed`) or dots (`DWC.Signed`). Deliberately diverges from the sibling Resolve-Tools scripts (`AMF-Name`, `FDL-Name`) — see `CLAUDE.md` § "Conventions a future instance should follow" #7 for the rationale and plan §8 open question #7 for the decision record.

## Testing

`tests/test_resolve_script.py` covers:

- Substring scorer tier boundaries (100/90/85/80/75/70/65/0).
- One-clip-per-sidecar enforcement across reruns.
- Full `run()` flow with a mock Resolve (fake `GetProjectManager`, `GetMediaPool`, `MediaPoolItem.SetMetadata`).
- `SetMetadata` returning `False` for some fields → logged as missing, other fields applied.
- `extract_dwc_fields` parity with the ALE emitter's field derivation.
- `ensure_custom_fields` walk-through lists all eight fields and points at both the 20.2+ and legacy UI paths.
