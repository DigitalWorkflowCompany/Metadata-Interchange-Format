# DaVinci Resolve integration

Two scripts for DaVinci Resolve 20 / 21:

- `apply_dwc_metadata.py` — walks the active project's Media Pool, matches each clip to a DWC sidecar by filename, and writes the eight `DWC_*` provenance fields via `MediaPoolItem.SetMetadata`.
- `ensure_custom_fields.py` — one-shot that prints the exact Project Settings walk-through Resolve requires before any `SetMetadata` call will persist.

## The pre-existing-field gotcha

Resolve's `MediaPoolItem.SetMetadata(key, value)` silently returns `False` for any field not pre-registered in the project's Metadata & Scene settings. There is **no scripting API** to create custom fields (confirmed against the vendor READMEs committed at `resources/documentation/DaVinciResolve20_Scripting_README.txt` and `DaVinciResolve21_Scripting_README.txt`; flagged UNVERIFIED in plan §1.10 risks and still not discovered).

Before running `apply_dwc_metadata.py` for the first time in a project:

1. Open Project Settings (Cmd/Ctrl-`,`).
2. Select **General Options**.
3. Scroll to **Metadata & Scene**.
4. Click **+** and add these eight fields (type: Text):
   - `DWC_Signed`
   - `DWC_Kid`
   - `DWC_Events`
   - `DWC_Locks`
   - `DWC_LockedBy`
   - `DWC_LastVerified`
   - `DWC_SidecarPath`
   - `DWC_ChainHead`
5. Save the project.

`python3 -m dwc_sidecar.integrations.resolve.ensure_custom_fields` prints these instructions for a DIT who can't remember. Field definitions don't sync across projects — repeat per project.

## Installation

Copy `apply_dwc_metadata.py` to Resolve's Utility scripts folder so it shows in Workspace → Scripts → Utility:

```bash
cp src/dwc_sidecar/integrations/resolve/apply_dwc_metadata.py \
   ~/Library/Application\ Support/Blackmagic\ Design/DaVinci\ Resolve/Fusion/Scripts/Utility/
```

Windows / Linux paths differ — see the vendor READMEs at `resources/documentation/`.

Inside Resolve, open the project, then run **Workspace → Scripts → Utility → apply_dwc_metadata**. Resolve populates a global `resolve` object for scripts launched from the menu; the script picks that up without a `RESOLVE_SCRIPT_LIB` round-trip.

## Headless invocation

For dailies pipelines and CI that drive Resolve externally:

```bash
export RESOLVE_SCRIPT_LIB=/Applications/DaVinci\ Resolve/DaVinci\ Resolve.app/Contents/Libraries/Fusion/fusionscript.so
python3 -m dwc_sidecar.integrations.resolve.apply_dwc_metadata /path/to/sidecar-dir
```

The default Mac path is the one above; Windows is `C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll`; Linux is `/opt/resolve/libs/Fusion/fusionscript.so`. See the vendor READMEs for authoritative paths.

Resolve must be running with the target project open for the headless path to work.

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

`SetThirdPartyMetadata` is intentionally not used. The vendor README lists it as a separate namespace, but it doesn't appear in the substantial prior art at `~/Documents/Resolve-Tools/` — suggesting it's either broken or not surfaced in Resolve's UI. `SetMetadata` with the pre-existing-field constraint is the reliable path.

## Field-name convention

Underscores (`DWC_Signed`), never hyphens (`DWC-Signed`) or dots (`DWC.Signed`). Deliberately diverges from the sibling Resolve-Tools scripts (`AMF-Name`, `FDL-Name`) — see `CLAUDE.md` § "Conventions a future instance should follow" #7 for the rationale and plan §8 open question #7 for the decision record.

## Real-app validation (UNVERIFIED)

Exit criteria per plan §7.1: a trial run against Resolve Studio 20 **and** 21 with the stub corpus imported, all eight `DWC_*` fields visible in the Metadata inspector, screenshots committed to `docs/integration/resolve.md`. Resolve 20.3.2 is available locally; Resolve 21 needs a separate install (per user memory). Not yet validated.

## Testing

`tests/test_resolve_script.py` covers:

- Substring scorer tier boundaries (100/90/85/80/75/70/65/0).
- One-clip-per-sidecar enforcement across reruns.
- Full `run()` flow with a mock Resolve (fake `GetProjectManager`, `GetMediaPool`, `MediaPoolItem.SetMetadata`).
- `SetMetadata` returning `False` for some fields → logged as missing, other fields applied.
- `extract_dwc_fields` parity with the ALE emitter's field derivation.
