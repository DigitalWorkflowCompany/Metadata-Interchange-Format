# YoYotta — request to add DWC columns to the ALE import allowlist

A brief intended for sharing with Martin (YoYotta CTO). Drafted 2026-04-25 after a dry-run confirmed YoYotta currently consumes ALEs containing DWC columns silently, with no surface in the UI.

---

## The ask

Add eight column names to YoYotta's ALE-import allowlist so that DWC provenance metadata becomes visible alongside imported clips:

`DWC_Signed`, `DWC_Kid`, `DWC_Events`, `DWC_Locks`, `DWC_LockedBy`, `DWC_LastVerified`, `DWC_SidecarPath`, `DWC_ChainHead`

All eight are prefixed `DWC_` so they cannot collide with existing recognised metadata fields (Production, Vendor, Season, Episode, ShootDay, ShootDate, TransferDate, Batch, Scene, Take, Shot, Name, MD5, xxHash) or with future YoYotta additions in another namespace.

## Background

DWC (Digital Workflow Company) publishes a per-clip film-industry metadata sidecar format that composes with MovieLabs OMC v2.8. Each clip gets a small JSON sidecar that references the canonical DIT artifacts already on disk (AMF, ASC MHL, ASC FDL, ASC CDL, Resolve exports) and carries an Ed25519-signed, hash-chained provenance log above them. The sidecar never duplicates the artifacts — it only references and signs them.

For tools with no Lua / Python script surface, the eight `DWC_*` columns above are the canonical interchange via ALE. A DIT cart running YoYotta + DWC would emit a per-day `dwc-columns-YYYY-MM-DD.ale` automatically alongside the camera-roll ALE YoYotta already creates, and a downstream consumer (Avid, Resolve) would see the provenance metadata for every clip.

DWC has already shipped native script integrations for Pomfort Silverstack 9.2+ (Lua) and DaVinci Resolve 20/21 (Python). YoYotta is the next-largest macOS DIT tool in the same workflow and currently the only one in that triplet without first-class DWC visibility.

## Column semantics

| Column             | Type                | Example value                  | Meaning                                                                  |
|--------------------|---------------------|--------------------------------|--------------------------------------------------------------------------|
| `DWC_Signed`       | `"true"` / `"false"` | `true`                         | The sidecar's event chain has at least one valid Ed25519 signature.      |
| `DWC_Kid`          | string              | `dwc-post-01`                  | Key ID (`kid`) of the most recent signed event.                          |
| `DWC_Events`       | integer-as-string   | `4`                            | Total signed events on the chain.                                        |
| `DWC_Locks`        | integer-as-string   | `1`                            | Number of `lock` actions on the chain.                                   |
| `DWC_LockedBy`     | string (or empty)   | `dwc-post-01`                  | `kid` of the most recent lock event; empty when the clip is unlocked.    |
| `DWC_LastVerified` | ISO-8601 UTC        | `2026-04-25T07:05:22Z`         | When the ALE was emitted (i.e., when DWC last verified the chain).       |
| `DWC_SidecarPath`  | filename            | `A001_C042_0420AB.omc.json`    | The sidecar file alongside the clip on disk; useful for round-trip.      |
| `DWC_ChainHead`    | 12-hex-char string  | `60aadd4ffa6e`                 | First 12 hex of the tip-event hash; serves as a short chain identifier.  |

All values are plain strings in the ALE (Avid Log Exchange is text-only), so YoYotta can treat them as Text columns — no type-aware parsing required.

## Sample ALE (current emitter output, one clip)

```
Heading
FIELD_DELIM	TABS
VIDEO_FORMAT	1080
AUDIO_FORMAT	48khz
FPS	24

Column
Name	Tape	Start	End	DWC_Signed	DWC_Kid	DWC_Events	DWC_Locks	DWC_LockedBy	DWC_LastVerified	DWC_SidecarPath	DWC_ChainHead

Data
A001_C042_0420AB	A001	01:00:00:00	01:00:00:00	true	dwc-post-01	4	1	dwc-post-01	2026-04-25T07:05:22Z	A001_C042_0420AB.omc.json	60aadd4ffa6e
```

## What "supported in YoYotta" would mean

- The eight `DWC_*` columns appear in YoYotta's source-browser metadata view alongside the existing recognised columns when an ALE containing them is loaded.
- Matching against indexed clips uses the existing ALE-import logic — no new matching rules required.
- No write-back is needed: the columns are read-only provenance and YoYotta would only ever display, never modify, them.
- Any field type tagged `Text` is fine; no need for type-aware parsing or validation.

If the column names need namespacing differently for YoYotta's internal storage (e.g. `dwc.signed`, `dwc:signed`), that's negotiable — what matters is round-trip visibility from an ALE that uses the underscore form.

## References

- DWC sidecar JSON Schema (v0.1): <https://ns.the-dwc.com/sidecar/v0.1/>
- GitHub: <https://github.com/DigitalWorkflowCompany/Metadata-Interchange-Format>
- The eight columns and their derivation are defined in `src/dwc_sidecar/ale_emitter.py` and exercised by `tests/test_ale_emitter.py` in that repo.
- Reference corpus (Sony VENICE, 40 clips, MHL v1 + AMF v2.0 + FDL v2.0 + ASC CDL v1.2) available on request.

## Contact

Adam Shell — adam@the-dwc.com
