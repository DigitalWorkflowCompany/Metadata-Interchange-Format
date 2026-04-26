# ShotPut Pro integration

**Position:** ShotPut Pro is a workflow neighbor, not a DWC consumer. There is no in-app integration to perform.

**Confirmed** against ShotPut Pro 2025-vintage demo on macOS, 2026-04-26 (plan §7.1 dry-run).

## What we found

ShotPut Pro is an offload/copy tool. Its workflow surface generates reports describing what was copied (PDF, TXT, CSV, MHL), but it has no metadata-import path: no menu option, no button, and no documented feature for loading external metadata onto already-offloaded clips. Verified by:

- Scanning the user-facing manuals for ShotPut Pro Mac 2020.2.2 and ShotPut Pro Windows 2024.2 — zero references to ALE / Avid Log Exchange / `.ale`.
- Walking the menus of the ShotPut Pro 2025 demo — no Import / Load Metadata / Read ALE option present.
- Imagine Products' 2025 release notes describe expanded *report* customisation, but no metadata *import* surface.

This is structurally different from YoYotta (where ALE import exists, but a fixed allowlist of recognised columns silently drops `DWC_*`). ShotPut Pro doesn't have the feature category at all.

## Why this is OK

The DWC ALE retains its value alongside ShotPut Pro without ShotPut Pro itself being involved:

```
┌──────────────┐         ┌──────────────────┐
│ ShotPut Pro  │ offload │ destination disk │
│  (offload)   ├────────▶│  + ShotPut MHL   │
└──────────────┘         │  + ShotPut PDF   │
                         │                  │
┌──────────────┐         │  + DWC sidecars  │
│  dwc watch   │ sign &  │  + DWC ALE       │
│  (parallel)  ├────────▶│  (per-day batch) │
└──────────────┘         └─────┬────────────┘
                               │
                               ▼
                       ┌─────────────────┐
                       │ Avid / Resolve  │
                       │  (DWC visible)  │
                       └─────────────────┘
```

A DIT cart that runs ShotPut Pro for offload also runs `dwc watch` against the same destination, which:

1. Produces a `<clip>.omc.json` sidecar next to each clip ShotPut Pro writes.
2. Emits a per-day `dwc-columns-YYYY-MM-DD.ale` carrying the eight provenance columns.

The DWC artifacts ride alongside the ShotPut Pro deliverables and travel downstream to editorial. Avid Media Composer (the canonical ALE consumer) and DaVinci Resolve both display custom ALE columns natively, so the eight `DWC_*` fields surface where they actually need to: in the editor's clip browser, not on the DIT cart that produced them.

## Setup on a ShotPut Pro cart

No ShotPut Pro configuration is required. The DWC side is identical to any other shoot:

1. Configure ShotPut Pro to offload as usual.
2. Run `dwc watch <destination-root> --interval 2 --stable 5` against the same destination root in parallel. See `dwc init` to bootstrap a DIT-on-Mac configuration end-to-end.
3. The destination ends up with ShotPut Pro's normal artifacts plus a per-clip `.omc.json` and a per-day ALE.

For headless / CI production, swap `dwc watch` for `dwc batch` after each ShotPut Pro job completes.

## When this would change

If Imagine Engineering ships an ALE-import surface in a future ShotPut Pro release — feasible given the 2025–2026 roadmap leans into "smarter reporting" and "deeper automation" — this position should be revisited and the integration could become first-class. Until then, treat ShotPut Pro as part of the producer side of the diagram above.

If a ShotPut Pro feature request is the right next step, the path is the same one used for YoYotta (`docs/integration/yoyotta-vendor-request.md`): a focused vendor brief with concrete column names and a sample ALE.

## Plan §7.1 status

The original §7.1 criterion required ShotPut Pro to "import `dwc-columns-YYYY-MM-DD.ale` from the stub corpus and display the eight `DWC_*` columns correctly". That criterion as written cannot be met by the current product, and isn't a vendor-allowlist or configuration issue.

Per plan §7.1's own escape hatch — *"If any one track fails its exit criteria, that track can be descoped to a follow-up phase without blocking the others"* — the ShotPut Pro track is descoped from §7.1 with this document standing in place of the screenshots it originally specified.
