# Phase 02 — Adoption & Ergonomics

## Review history

**Date:** 2026-04-22
**Reviewer verdicts:**
- Architecture: Sound with concerns
- Edge cases: Significant gaps
- Testability: Testable with minor changes
- Sequencing: Minor reordering suggested

**One-line summaries:**
- Architecture: Seven findings (2 high, 4 medium, 1 low) around field-name divergence, `validate_as_json` refactor safety, ALE file growth, signer self-test verification gap, systemd activation, and two low-severity doc/code inaccuracies.
- Edge cases: Eleven findings (2 critical, 6 high, 3 medium, 2 low) covering ALE crash-safety, tab/CRLF injection, dedup key collision, watch-state field inconsistency, signer timeout enforcement, `string.Template` mismatch, Pyodide blake3 status, and web validator path mapping.
- Testability: Eight findings (3 high, 5 medium, 2 low) around dependency injection seams for doctor checks, signer mock, validate_as_json contract, ALE I/O seam, platform injection for init, clock injection, and curl interception.
- Sequencing: Four findings (1 high, 2 medium, 1 low) recommending the `validate_as_json` refactor be pulled to the front of the phase, the watch-state schema change be moved into item 1, an ALE format spike before coding, and explicit doc-release assignment.

### Post-planning findings (2026-04-23)

Two discoveries during pre-spike reconnaissance materially change item 1. Both
are captured in `CLAUDE.md` → External references and revised inline in §1.

1. **Silverstack has a public scripting API.** Silverstack 9.2.0 shipped a
   Lua 5.5.0 API (Pomfort SDK v1.0 released 2026-04-15, eight days before this
   revision). See <https://github.com/pomfort/silverstack-scripting>. Relevant
   hooks: `onStampVideo` / `onStampAudio` fire per-clip at ingest, `onFinish`
   fires after job completion. Scripts can read and **write** custom metadata
   via the `asset.Metadata` API. This invalidates the original §1.1 framing
   ("Silverstack has no public SDK") for Silverstack 9.2+.
2. **Resolve scripting for metadata import is prior-art-backed.** The user
   maintains `~/Documents/Resolve-Tools/` with working Python + Lua scripts
   for AMF, FDL, CDL, and ALE import into Resolve. Key established patterns:
   `MediaPoolItem:SetMetadata(key, value)` single-field form (not the dict
   form, not `SetThirdPartyMetadata` — the third-party API is not used in
   the prior art and appears unreliable); name-based clip matching with a
   substring scorer; custom metadata fields must pre-exist in Project
   Settings or `SetMetadata` returns `False` silently. `Import_AMF.py:950–1020`
   is the reference implementation for the match scorer.

Consequence: ALE is no longer the sole integration path. For Silverstack 9.2+
and Resolve, native scripting gives direct per-clip writes. ALE remains the
integration path for **YoYotta ID, ShotPut Pro, Avid Media Composer, and
Silverstack ≤ 9.1**. See §1.1b for the revised design.

### Post-real-app-validation findings (2026-04-26)

The §8.1 dry-runs uncovered five vendor-side facts that the original plan
got wrong in either direction. All affect §8.1's ALE exit criterion; the
scripting-track exit criteria for Silverstack and Resolve were met as
written.

1. **Silverstack XT 9.2.1 sandbox quirks** (resolved). The `apply_dwc_metadata.lua`
   script needed four real-app fixes to run under Silverstack's sandbox: a
   `-- sst: ingest` context tag, a `local dwc` table captured as a closure
   upvalue (script-level globals are unreachable from hook bodies), the
   documented `:getPath()` method (not the assumed `:path()`), and explicit
   workflow-attachment via the Register-in-Library activity. All recorded in
   `src/dwc_sidecar/integrations/silverstack/README.md` and in commit
   `ca50e93`. Silverstack §8.1 closed end-to-end.
2. **Resolve 20.3.2 + 21 API parity** (favourable). The `MediaPoolItem` /
   `MediaPool` / `ProjectManager` surface this integration uses is
   byte-identical between the Resolve 20 and 21 vendor READMEs (only Fairlight
   additions differ). Validating against Resolve 20 covers 21 — the original
   §8.1 ask for two trial runs on two machines is redundant. Recorded in
   commit `b33382d`. Resolve §8.1 closed end-to-end.
3. **YoYotta ALE-import column allowlist.** YoYotta consumes ALE imports
   only against a fixed allowlist of recognised column names (Production,
   Vendor, Season, Episode, ShootDay, ShootDate, TransferDate, Batch, Scene,
   Take, Shot, Name, MD5, xxHash). DWC_* columns import without error but
   silently disappear from the UI. Vendor brief sent to YoYotta CTO Martin
   on 2026-04-25 requesting allowlist extension; track is **blocked-on-vendor**
   pending response. See `docs/integration/yoyotta-vendor-request.md`.
4. **ShotPut Pro has no ALE-import surface.** ShotPut Pro is a producer-side
   offload tool with no metadata-import feature category — manuals confirm,
   2025 demo confirms. The §8.1 criterion as written is impossible by product
   design (not a vendor allowlist or configuration issue). Track **descoped**
   per §8.1's own escape hatch; the ShotPut Pro integration is reframed as a
   producer-side workflow neighbor in `docs/integration/shotput.md`. A future
   ShotPut Pro release that adds ALE import would bring this back into scope
   but does not block the phase.
5. **Avid Media Composer 24.10.0 imports the DWC ALE cleanly via merge**
   (resolved). Eight `DWC_*` columns survive transit and populate the Avid
   bin view after a Tape Name + Start TC merge against existing master clips.
   Avid case-normalises column names (`DWC_Signed` → `Dwc_signed`); the
   14-char-truncation folklore is **not** a hard rule in 2024 — `Dwc_lastverified`
   (16 chars) and `Dwc_sidecarpath` (15 chars) come through in full. Resolves
   §9 open question #1. Documented in `docs/integration/avid.md`. Avid §8.1
   track **closed**.

Two follow-ups carried out of the dry-run sprint, both **resolved 2026-04-26**:

- **Emitter `Start = End = 01:00:00:00` placeholder for tc-less sidecars.**
  Produced ALEs Avid rejected with "out point ≤ in point" before any merge
  could run. Resolved by defaulting `End = 01:00:00:01` (one frame after
  Start at any common FPS). Real production sidecars with timecode metadata
  override these placeholders and are unaffected. Regression-guarded by
  `tests/test_ale_emitter.py::test_extract_row_placeholder_end_is_after_start`.
- **Avid case normalisation affects round-trip identity.** A downstream tool
  that reads metadata back from an Avid export ALE has to match
  case-insensitively on the `dwc_` prefix. Documented in
  `src/dwc_sidecar/ale_emitter.py` module docstring and
  `docs/integration/avid.md`.

Net effect on §8.1: ALE-track exit criterion **met** against Avid (the
canonical ALE consumer), with optional vendor-side display in YoYotta if
Martin's allowlist change ships. ShotPut Pro is documented and descoped.

---

Scope: ship the five highest-leverage usability wins identified in the
product-critique discussion of 2026-04-22.

  1. DIT-tool integration via ALE round-trip + watch-folder (Silverstack /
     YoYotta / ShotPut Pro)
  2. `dwc doctor` — pre-flight audit of a production host
  3. macOS menu-bar status app (read-only)
  4. Web-based validator drop-zone (stateless, public)
  5. `dwc init` — one-command onboarding

Each item is independently shippable. Ship order is 5 → 2 → 1 → 3 → 4
(highest adoption-leverage-per-effort first; see §8).

*Revised per sequencing review: the `validate_as_json()` refactor (§4.5)
is extracted as the first commit of the phase — a half-day internal change
that stabilises `validate.py` before items 2 and 4 both touch it in
parallel. Its half-day cost is already inside item 4's 3-day estimate; move
that work to day 1 of the phase.*

Non-goals in this phase: schema changes (would bump to v0.2/), new
signer backends, new hash algorithms, Windows/Linux menu-bar apps.

---

## 0. Phase-opening prerequisite: `validate_as_json()` refactor

*Added per sequencing review #1 (high) and testability review #3 (high).*

Before any other item begins, extract a `validate_as_json()` entry point
from `validate.py`. This is a shared prerequisite for items 2 and 4 and
is the safest single commit to land first because it has no user-visible
impact.

**Implementation constraint (architecture review #2, high):**
`validate_as_json()` must be implemented as a standalone function that
calls the same stage functions as `main()` but collects results into a dict
rather than printing. `main()` must continue to call the stage functions
directly — do **not** refactor `main()` to delegate to `validate_as_json()`.
This keeps the two paths independent and eliminates regression risk to the
five existing subprocess callers (`watch._validate`, `mhl_walker.main`,
`batch.main`, and the `dwc validate` CLI path) that parse `returncode` and
`stdout`/`stderr` text.

**Implementation constraint (testability review #3, high):**
Refactor each stage function to return a structured result object (e.g.,
`{"status": "pass"|"warn"|"fail", "errors": int, "detail": str}`) instead
of printing directly. `validate_as_json()` assembles these into the dict the
web validator and doctor consume. The CLI `main()` calls the stage functions
directly and formats/prints as today. This separates output from logic and
makes all nine stages individually testable without stdout capture.

**Deliverables:**
- `validate_as_json(sidecar_path: Path, base_dir: Path, ...) -> dict` added
  to `validate.py`
- `tests/test_validate_as_json.py` — 9-stage parity test against CLI output

**Estimate:** 0.5 days (already inside item 4's budget).

---

## 1. DIT-tool integration via ALE round-trip

### 1.1 Problem

*Revised per post-planning finding 2026-04-23 (scripting API discovery).*

YoYotta ID and ShotPut Pro have no scripting surface worth targeting.
YoYotta has no API. Reverse-engineering is fragile and adversarial. But
both **import ALE** and display custom columns in their clip grids. For
those two tools, ALE is the integration.

**Silverstack and Resolve are different.** Both now have first-class scripting
APIs that let us write custom metadata onto clips directly — no import step,
no filename-matching gymnastics on the DIT side. Silverstack's Lua API
(9.2.0+) even fires `onStampVideo` per-clip during ingest, which is
*exactly* the moment our sidecar would be produced. For those two tools,
ALE is redundant at best and lossy at worst (ALE is a snapshot; scripts
write live metadata). Both paths are implemented in item 1 so that DITs
on older Silverstack versions or non-Pomfort/non-BMD workflows still work
off ALE, and DITs on current tooling get the richer native integration.

See §1.1b for the revised two-track design.

### 1.1a ALE format spike (prerequisite)

*Added per sequencing review #3 (medium).*

Before writing `ale_emitter.py`, generate a minimal hand-crafted `.ale`
file with one `DWC_*` column and verify that Silverstack Lab (trial),
YoYotta ID (trial), and Resolve Studio all display it. This zero-code spike
takes half a day and retires the highest-uncertainty risk in item 1. If any
tool rejects the format, the design decision is made before code is written.
Spike result is committed to `docs/integration/ale-spike-results.md`.

**Update (2026-04-23):** the spike generated `dwc-columns.ale` at repo root
and confirmed Silverstack has **no** `File → Import → ALE` path in 9.x. This
drove the §1.1b revision below (scripting as primary Silverstack/Resolve
integration, ALE demoted to YoYotta/ShotPut/Avid).

### 1.1b Native scripting integrations (YoYotta/ShotPut still ALE)

*Added per post-planning finding 2026-04-23.*

Silverstack 9.2+ and Resolve both expose scripting APIs that write custom
metadata directly onto clips. Both are additive to the ALE emitter — ALE
remains in scope for YoYotta, ShotPut Pro, Avid, and Silverstack ≤ 9.1 —
but the scripting paths are the richer integration for current tooling.

**The same eight `DWC_*` fields defined in §1.4 are written by all three
emitters** (ALE, Silverstack Lua, Resolve Python). Field set is authoritative
in §1.4; the emitters differ only in transport.

#### Silverstack Lua (9.2.0+)

- **File**: `src/dwc_sidecar/integrations/silverstack/apply_dwc_metadata.lua`
- **Runtime**: Lua 5.5.0 embedded in Silverstack. JSON parsing via bundled
  `dkjson` (stdlib of choice in Pomfort's SDK examples).
- **Hook**: `onStampVideo` fires per clip during ingest. The hook looks for
  `<clip-basename>.omc.json` alongside the clip on disk, parses it, and
  calls the Asset metadata setters for each `DWC_*` field. Also registers
  `onFinish` for post-ingest reconciliation (missed clips).
- **Distribution**: script file placed in Silverstack's "shared" scripts
  scope (see Pomfort's three-scope convention); installed by `dwc init` on
  macOS when Silverstack 9.2+ is detected.
- **Version detection**: `dwc init` checks the `Silverstack.app` Info.plist
  for `CFBundleShortVersionString ≥ 9.2.0`. Older versions get the ALE
  integration only.

#### Resolve Python

- **File**: `src/dwc_sidecar/integrations/resolve/apply_dwc_metadata.py`
- **Runtime**: Python 3 inside Resolve (both Resolve 20 and 21 supported —
  vendor READMEs committed at
  `resources/documentation/DaVinciResolve{20,21}_Scripting_README.txt`).
- **Entry points**:
  - Menu-invoked: installed to
    `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/`
    so it shows in Workspace → Scripts → Utility.
  - Externally invoked: usable headless via `RESOLVE_SCRIPT_LIB` + env vars
    for CI / dailies pipelines.
- **Flow**: connect to Resolve → iterate current project's Media Pool
  recursively → for each `MediaPoolItem`, locate the adjacent sidecar by
  filename using the substring scorer pattern from
  `~/Documents/Resolve-Tools/Import-AMF/Import_AMF.py:950–1020` → call
  `MediaPoolItem:SetMetadata(key, value)` for each of the 8 `DWC_*` fields.
- **Pre-existing-field gotcha**: Resolve requires custom metadata fields to
  pre-exist in Project Settings → General Options → Metadata & Scene. The
  script first-time-runs a helper that lists the 8 field names and gives
  the user a one-shot "create all" prompt. If fields are missing at
  `SetMetadata` call time, it logs the missing field names to the console
  and continues (same pattern as `Import_AMF.py:1407`).
- **No ingest hook** — Resolve has no `onStampVideo` equivalent; user
  invokes the script manually after importing clips, or a post-dailies
  pipeline invokes it headless.
- **Field-name convention** — open question, see §9.

#### What item 1 does and doesn't produce

| Tool                  | Integration                  | Notes                                  |
|-----------------------|------------------------------|----------------------------------------|
| YoYotta ID            | ALE                          | no scripting surface                   |
| ShotPut Pro           | ALE                          | no scripting surface                   |
| Avid Media Composer   | ALE                          | existing editorial handoff path        |
| Silverstack ≤ 9.1     | ALE (if import exists)       | ALE import path UNVERIFIED in 9.x      |
| Silverstack 9.2+      | Lua script via `onStampVideo`| ALE emitted too (belt-and-braces)      |
| Resolve 20 / 21       | Python script in Utility menu| ALE emitted too                        |

The ALE file is **always** produced by `dwc watch` / `dwc ale-export` —
scripting is additive. A shoot that mixes Silverstack 9.2 on the cart and
Silverstack 8 in post gets both surfaces.

### 1.2 Deliverables

*Revised per post-planning finding 2026-04-23 — scripting integrations added.*

**ALE track**:
- `src/dwc_sidecar/ale_emitter.py` — new module, including the
  `update_ale(ale_path: Path, row: dict, now: datetime | None = None) -> None`
  function consumed by the watcher
- `dwc ale-export <sidecar...>` CLI subcommand
- `dwc watch --emit-ale` flag (default: on)
- `docs/integration/yoyotta.md`, `docs/integration/shotput.md` — per-app
  setup (1 page each); shipped as part of v0.3.0, not before real-app
  validation passes (§8.1)
- `docs/integration/ale-spike-results.md` — spike outcome
- `tests/test_ale_emitter.py`
- A sample `dwc-columns.ale` committed alongside the stub data
  (already written to repo root during 2026-04-23 spike)

**Silverstack Lua track (9.2.0+)**:
- `src/dwc_sidecar/integrations/silverstack/apply_dwc_metadata.lua` —
  the `onStampVideo` hook script
- `src/dwc_sidecar/integrations/silverstack/README.md` — install
  instructions (script scopes, where Silverstack reads from)
- `docs/integration/silverstack.md` — per-app setup, covers both the
  script install and fallback ALE path for ≤ 9.1
- `tests/test_silverstack_script.py` — parse-and-apply harness that
  exercises the Lua script against a fixture sidecar using a Lua
  interpreter (stdlib `subprocess` → `lua` binary, skipped if absent)

**Resolve Python track**:
- `src/dwc_sidecar/integrations/resolve/apply_dwc_metadata.py` — the
  Media Pool walker + metadata writer
- `src/dwc_sidecar/integrations/resolve/ensure_custom_fields.py` — helper
  that adds the 8 `DWC_*` fields to Project Settings (if the API allows;
  otherwise prints the manual steps)
- `src/dwc_sidecar/integrations/resolve/README.md` — install instructions
- `docs/integration/resolve.md` — per-app setup, covers script install +
  the "fields must pre-exist" gotcha
- `tests/test_resolve_script.py` — unit tests for the substring scorer
  (port of `Import_AMF.py` pattern), mocked Resolve API for the rest

### 1.3 ALE format

ALE is tab-separated, with a fixed header block. We emit UTF-8 with CRLF
line endings (Avid convention; Silverstack/YoYotta accept LF too but CRLF
is safest for round-trip through Windows Avid systems). UNVERIFIED: LF
acceptance by Silverstack/YoYotta — confirm during spike (§1.1a).

```
Heading
FIELD_DELIM     TABS
VIDEO_FORMAT    1080
AUDIO_FORMAT    48khz
FPS             24

Column
Name    Tape    Start   End     DWC_Signed      DWC_Kid DWC_Events   DWC_Locks       DWC_LockedBy    DWC_LastVerified        DWC_SidecarPath DWC_ChainHead

Data
A001C001_260115_R1AB    A001    01:00:00:00     01:00:05:00     true    dwc-dit-01      4       1       dwc-post-01     2026-04-22T14:02:11Z    sidecars/A001C001_260115_R1AB.omc.json  3f0b9e...
```

**Column value sanitisation (edge-cases review #3/#4, high):**
All column values must be sanitised before emission. The `ale_emitter.py`
module must strip or replace any tab (`\t`), carriage return (`\r`), and
newline (`\n`) characters in every value before writing — tab would shift
all subsequent columns right, and embedded line endings split the row into
phantom rows in DIT tool parsers. Add a test for tab-in-value and CRLF-in-value.

### 1.4 DWC custom columns (emit)

| Column              | Source                                                   | Example                                |
|---------------------|----------------------------------------------------------|----------------------------------------|
| `DWC_Signed`        | all events have valid Ed25519 sig (Stage 4)              | `true` / `false`                       |
| `DWC_Kid`           | kid of most recent event                                 | `dwc-dit-01`                           |
| `DWC_Events`        | event count                                              | `4`                                    |
| `DWC_Locks`         | count of entries in `dwc.sidecar.locks`                  | `1`                                    |
| `DWC_LockedBy`      | kid of latest lock event, or empty                       | `dwc-post-01`                          |
| `DWC_LastVerified`  | ISO-8601 UTC of the validation run that produced the row | `2026-04-22T14:02:11Z`                 |
| `DWC_SidecarPath`   | path relative to ALE directory                           | `sidecars/A001C001...omc.json`         |
| `DWC_ChainHead`     | hash of the tip event (first 12 hex)                     | `3f0b9e41cc07`                         |

All eight are prefixed `DWC_` to avoid colliding with Avid/Silverstack
reserved columns. Silverstack's "Map columns" dialog will show them verbatim.

### 1.5 `dwc ale-export` CLI

```
dwc ale-export <sidecar.omc.json>... [--out <path.ale>] [--validate]
               [--base-dir <root>] [--tape <id>]
```

- With no `--out`, writes `dwc-columns.ale` next to the first sidecar.
- With `--validate`, runs the 9-stage validator on each input first; a
  failing sidecar produces `DWC_Signed=false` and a WARN log line but does
  not abort the export (partial-day rolls are normal on set).
- `--tape` overrides `Tape` column derivation (default: parsed from
  OMC `clipName` via a regex defined in `ale_emitter.py`).
- **`--base-dir` default (architecture review #3, medium):** When
  `--validate` is specified without `--base-dir`, default `--base-dir` to
  the parent directory of the first sidecar input. Document in the CLI help
  that `keyring.json` must be in CWD or `--base-dir` must point to the
  directory containing it.

### 1.5a Tape column derivation

*Revised per architecture review #7 (low).*

There is no A-cam-reel regex in `mhl_walker.py`; that cross-reference in
the original plan was inaccurate. Define the A-cam-reel extraction regex
directly in `ale_emitter.py`. Convention: `^([A-Z]\d{3})` extracts the
reel prefix (`A001`) from `A001C001_260115_R1AB`. Document the convention
in the module docstring.

### 1.6 `dwc watch --emit-ale`

Default on. After each sidecar emission the watcher calls
`ale_emitter.update_ale(ale_path, row, now=datetime.now(timezone.utc))`.

*Revised per testability review #4 (medium): ALE update logic lives in
`ale_emitter.update_ale()`, not inlined in `Watcher._process()`. The
watcher calls it. This separates the dedup/rewrite logic from the watcher
so it can be tested independently of a fully wired `Watcher`.*

**Dedup key (edge-cases review #5, high):**
Dedup on `DWC_SidecarPath`, not on `Name`. `Name` is not a globally unique
key — two clips can share the same name across different reels on a multi-
roll shoot (e.g., second-unit shoots that reset the C-number). `DWC_SidecarPath`
is path-relative and therefore unique per sidecar file.

**ALE file size (architecture review #4, medium):**
Write a per-day ALE file named with the date: `dwc-columns-YYYY-MM-DD.ale`.
This bounds the file to one day's clips and prevents unbounded growth across
a multi-day shoot. The `dwc ale-export` CLI operates on any sidecar
regardless of date and can regenerate or merge across days.

**Append semantics:**

- If the ALE does not exist: write full header + one data row.
- If it exists: read it, dedupe by `DWC_SidecarPath` (latest row wins),
  rewrite atomically.

**Crash-safety (edge-cases review #1, critical):**
The re-read step must always open the production ALE filename, never the
`.tmp` filename. Before writing the `.tmp`, delete any pre-existing `.tmp`
file unconditionally (stale `.tmp` from a prior crash must not be read as
input data). The sequence is: (1) delete `.tmp` if present; (2) read
production file; (3) write `.tmp`; (4) `os.replace(.tmp → production)`.

**Known trade-off (edge-cases review #2, critical):**
The window between writing `.tmp` and `os.replace` means the last row can
be lost if the process is killed exactly in that window. This is an accepted
trade-off: the sidecar file is the source of truth; the ALE is a derived
view. After a crash, regenerate with `dwc ale-export <out-dir>/*.omc.json`.
Document this in §1.9 Risks.

**ALE deletion recovery (edge-cases review #11, medium):**
If a user deletes `dwc-columns-YYYY-MM-DD.ale` mid-day, the next clip
triggers the first-write branch (header + one row). Regenerate with:
`dwc ale-export <watch-root>/*.omc.json --out dwc-columns-YYYY-MM-DD.ale`.
Document this in the per-app setup docs (§1.7).

**`DWC_LastVerified` clock injection (testability review #7, low):**
`ale_emitter.update_ale()` and the main emission function accept a
`now: datetime | None = None` parameter. If `None`, defaults to
`datetime.now(timezone.utc)`. Tests pass a fixed datetime to produce
deterministic golden files.

Failure mode: if ALE rewrite raises, log WARN and continue. Sidecar
emission must never be blocked by ALE I/O.

### 1.7 Per-app setup docs (outline)

*Revised per post-planning finding 2026-04-23 — split into ALE-based tools
and script-based tools.*

**ALE-based tools (YoYotta ID, ShotPut Pro, Avid Media Composer, Silverstack ≤ 9.1):**

  1. Configure output — point the app's reports folder at the same
     directory as clip offloads (this is already convention).
  2. Run `dwc init` once (see §5).
  3. `launchctl load ~/Library/LaunchAgents/com.dwc.sidecar.watch.plist`.
  4. In the DIT app, import `dwc-columns-YYYY-MM-DD.ale`. The exact menu
     path varies — see each app's per-tool doc. Columns appear in the
     clip grid.
  5. If `dwc-columns-YYYY-MM-DD.ale` is deleted, regenerate with:
     `dwc ale-export <watch-root>/*.omc.json --out dwc-columns-YYYY-MM-DD.ale`

**Silverstack 9.2+ (script path):**

  1. Run `dwc init` once; it detects Silverstack 9.2+ and offers to install
     `apply_dwc_metadata.lua` into Silverstack's shared scripts folder.
  2. Accept. The script is now registered in Silverstack's scripting menu.
  3. `launchctl load` the watcher as above; sidecars are emitted as clips
     arrive.
  4. At next ingest, `onStampVideo` fires per clip and writes the 8 `DWC_*`
     fields into Silverstack's custom metadata. Columns appear in the clip
     grid automatically — no ALE import needed.
  5. An ALE is still emitted in parallel for editorial handoff.

**Resolve 20 / 21 (script path):**

  1. One-time Project setup: in Project Settings → General Options →
     Metadata & Scene, add the 8 `DWC_*` field names as custom metadata.
     (Or run the `ensure_custom_fields.py` helper once per project — see
     `docs/integration/resolve.md`.) Resolve will silently drop
     `SetMetadata` calls for fields that don't pre-exist.
  2. Install `apply_dwc_metadata.py` to
     `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/`
     (done automatically by `dwc init`).
  3. Import clips into a Resolve Media Pool as usual.
  4. Run Workspace → Scripts → Utility → `apply_dwc_metadata`. The script
     walks the Media Pool, matches clips to sidecars by filename, and
     writes the 8 `DWC_*` fields onto each matched `MediaPoolItem`.
  5. For headless / pipeline use, invoke via `RESOLVE_SCRIPT_LIB` — see
     the module docstring.

For Silverstack specifically, note that Silverstack 8+ remembers imported
custom columns across project sessions, so the ALE import is a one-time
action per project. UNVERIFIED: confirm during real-app testing (§8.1).

### 1.8 watch-state `emitted` field — moved into item 1

*Revised per sequencing review #2 (medium): the `emitted` field addition
to `.watch-state.json` is shipped as part of item 1, not item 3. This keeps
the watch-state schema stable before the menu-bar app is built and avoids
item 1's integration test golden becoming stale when item 3 lands.*

`watch.py:_save_state()` gains an `emitted` list, capped at 100, each entry
`{clipName, omcPath, signedAt, status}`. `_load_state()` reads `emitted`
with a default of `[]` so old state files without the field are read
without error. The watch-state schema contract: both old and new watcher
instances produce a file that is readable by both old and new consumers.

### 1.9 Tests

- `tests/test_ale_emitter.py`
  - round-trips a fixture sidecar → ALE → parse-back → asserts all 8
    `DWC_*` columns match source (clock frozen via `now=` parameter)
  - dedup: two sidecars with the same `DWC_SidecarPath` (different `seq`)
    produce one row, and it's the later one
  - dedup does NOT merge two sidecars with the same `Name` but different
    `DWC_SidecarPath` (multi-roll correctness)
  - CRLF line endings
  - tab delimiter survives values containing spaces
  - tab character in a column value is sanitised (not passed through)
  - CRLF in a column value is sanitised
  - unicode clipName (e.g. `A001_Café_260115`) survives round-trip
  - stale `.tmp` file is deleted before the re-read step
  - ALE I/O failure logs WARN and does not propagate exception
- `ale_emitter.update_ale()` tests exercise all branches without
  instantiating a `Watcher` at all
- Integration test: run `dwc watch` over a fixture, assert
  `dwc-columns-YYYY-MM-DD.ale` matches a golden (using fixed `now=`).
  Write this test using `--no-validate` equivalent at the API level to
  avoid the `subprocess.run` validator path.

### 1.10 Risks

- **ALE spec is Avid-proprietary and loosely documented.** Mitigate by
  running the spike (§1.1a) before writing any code. Keep the emitter
  minimal: only the header fields all three tools agree on.
- **Column name bikeshedding.** Lock the names in this plan; any future
  change is a breaking change for consumers who built grids on them.
  Confirm `DWC_` prefix is not reserved by any of the three tools during
  the spike.
- **One ALE row can be lost on watcher process kill.** Accepted trade-off;
  the sidecar is the source of truth. Documented above and in §1.7.
- **Unicode beyond Latin-1 Extended.** The round-trip test covers `Café`.
  Whether Silverstack/YoYotta parse multi-byte characters correctly in ALE
  is UNVERIFIED; confirm during real-app testing (§8.1 exit criteria). Flag
  in integration docs.
- **Silverstack 9.2.0 adoption lag** *(post-planning finding 2026-04-23)*.
  The Lua API is eight days old at time of writing. Most production DITs
  are on 9.1 or 8.x. The fallback ALE path covers them, but whether their
  Silverstack version has any ALE import capability at all remains
  UNVERIFIED; the 2026-04-23 spike established 9.x does **not** have a
  `File → Import → ALE` menu. If older versions also lack it, Silverstack
  pre-9.2 has no DWC integration path beyond the CLI validator.
- **Resolve custom fields must pre-exist** *(post-planning finding
  2026-04-23)*. `MediaPoolItem:SetMetadata` silently returns `False` for
  unknown fields — there's no API to create them, they must be added via
  the Project Settings UI. Mitigation: `ensure_custom_fields.py` helper
  (may or may not be able to add them programmatically — UNVERIFIED), or
  clear one-time-setup docs. Pattern follows `Import_AMF.py:1407` in the
  prior-art repo.
- **Resolve scripting-API version drift.** The vendor READMEs at
  `resources/documentation/` are Resolve 20 (2025-08-18) and 21
  (2025-10-07). The `SetMetadata` surface is stable across both, but
  any newer feature referenced by the script must be gated on a runtime
  check of `resolve.GetVersionString()`.
- **`SetThirdPartyMetadata` not a viable alternative** *(post-planning
  finding 2026-04-23)*. The vendor README exposes it as a separate
  namespace, but it appears nowhere in `~/Documents/Resolve-Tools/`'s
  substantial prior art, suggesting it's either broken or not surfaced
  in Resolve's UI. Stick with `SetMetadata` and accept the
  pre-existing-field constraint.

### 1.11 Estimate

*Revised per post-planning finding 2026-04-23.*

- ALE track: 2 engineer-days (unchanged), spike is the first half-day.
- Silverstack Lua track: 1 engineer-day. Small script, but needs a real
  Silverstack 9.2 install for end-to-end testing (trial license).
- Resolve Python track: 1 engineer-day. Heavily de-risked by
  `~/Documents/Resolve-Tools/Import-AMF/Import_AMF.py` — the clip-matching
  and `SetMetadata` patterns lift cleanly. Main cost is testing across
  Resolve 20 and 21, and figuring out whether `ensure_custom_fields.py`
  can add fields programmatically.
- Per-app docs: 0.5 engineer-days (unchanged).

**Item 1 total: 4.5 engineer-days** (was 2).

Phase 02 total revised to ~15.5 engineer-days (was 13.5). See §8.

---

## 2. `dwc doctor`

### 2.1 Problem

Trust misconfiguration (expired key, missing signer backend, revoked kid,
schema drift) surfaces today only during a full `dwc validate` or `dwc
watch` run — often 400 sidecars in. A DIT wants to know at call-time, in
under 2 seconds, whether their rig is ready for the day.

### 2.2 Deliverables

- `src/dwc_sidecar/doctor.py`
- `dwc doctor` CLI subcommand
- `tests/test_doctor.py`
- `docs/operations/doctor.md` — one page listing every check and its remedy

### 2.3 Checks

Each check is a pure function accepting explicit path/context arguments —
not reading from `os.getcwd()` directly. Signature pattern:

```python
def check_keyring(keyring_path: Path, sidecars: list[Path],
                  now: datetime) -> CheckResult: ...
```

*Revised per testability review #1 (high): explicit path injection makes
each check independently unit-testable with `tmp_path` without
`monkeypatch.chdir`. The `dwc doctor` CLI entry point passes
`Path.cwd() / "keyring.json"` etc.*

Each check returns `CheckResult(status, title, detail, remedy)` where
`status ∈ {PASS, WARN, FAIL}`. Doctor exits `0` if no `FAIL`, `1`
otherwise. `WARN` never fails the check.

| # | Check                                                             | FAIL if…                                                               |
|---|-------------------------------------------------------------------|------------------------------------------------------------------------|
| 1 | Python ≥ 3.11                                                     | older                                                                  |
| 2 | Required packages importable (`jsonschema`, `rfc8785`, `cryptography`, `xxhash`, `blake3`) | any ImportError                                           |
| 3 | All declared hash algs in `canonical.HASH_ALGS` resolve           | optional backend missing *and* referenced by any sidecar in CWD        |
| 4 | `keyring.json` present and parses                                 | missing / malformed                                                    |
| 5 | Every keyring entry has valid `validFrom` ≤ now ≤ `validUntil`    | any key expired **and** referenced by events in CWD sidecars           |
| 6 | Signer config resolves for every kid in the keyring               | `DWC_SIGNERS` points at missing file, or kid without a backend         |
| 7 | Each signer backend self-test passes (see §2.4)                   | backend refuses to sign a throwaway 32-byte payload                    |
| 8 | No plaintext `keys.priv.json` present when backend ≠ `local`      | WARN only; remedy message: "Run `rm keys.priv.json` — this file contains plaintext private keys" |
| 9 | Local schemas byte-match `ns.the-dwc.com` (reuses `--check-hosted` logic) | drift detected (same check CI runs); network failure → WARN not FAIL (see §2.3a) |
|10 | `.watch-state.json` in CWD, if present, is parseable and its `processed_mhl_sha256` list is non-empty | stale or unreadable state (see §2.3b) |
|11 | All `*.omc.json` in CWD parse as JSON and contain a `customData[dwc.sidecar.*]` block | corrupt file in tree (with retry, see §2.3c) |
|12 | Key window expiry > 30 days away                                  | WARN if < 30 days, FAIL if already expired                             |

### 2.3a Check 9 — network failure is WARN, not FAIL

*Revised per edge-cases review #9 (medium).*

Many film production environments block outbound HTTPS via corporate proxy
or firewall. A network failure from `check_hosted_schemas()` (`curl` timeout,
DNS failure, 403 from proxy) must be classified as WARN ("could not verify
— network unavailable"), not FAIL. Only an actual fetched-but-diverged
response is a FAIL. The existing `validate.py` `check_hosted_schemas()`
already has a "FETCH FAIL" path — doctor wraps it and must interpret that
exit code as WARN rather than escalating to FAIL.

Check 9 is also skipped in `--quick` mode.

### 2.3b Check 10 — field name and sequencing

*Revised per architecture review #1 (high) and edge-cases review #5 (high).*

The field name in `.watch-state.json` is `processed_mhl_sha256` (a list),
not `last_mhl_sha256`. The original plan's field name was wrong. Check 10
is defined against the existing `processed_mhl_sha256` list: the check
passes if the list is parseable. The `emitted` field (added in §1.8) is
read if present, with a default of `[]` if absent — doctor must not crash
when reading a state file written by an older watcher.

Check 10 does not assert that any file referenced by the MHL sha256 list
still exists on disk — the hashes are content addresses, not file paths.

### 2.3c Check 11 — retry on concurrent write

*Revised per edge-cases review #8 (medium).*

`dwc doctor` may run concurrently with `dwc watch`. Sidecar writes are not
atomic at the OS level (the file is created before it is fully written).
Wrap the `json.loads` call for each `.omc.json` in a retry: 1–2 attempts,
50 ms apart, before marking the file as FAIL. This eliminates spurious
failures when the watcher is actively writing.

### 2.4 Signer self-test

*Revised per architecture review #5 (medium), edge-cases review #7 (high),
and testability review #2 (high).*

For each kid, call `signer.sign(b"\x00" * 32)` and verify the signature
using the public key from `keyring.json` — specifically
`load_pubkey_b64(keyring[kid]["publicKey"])` from `canonical.py`, **not**
`signer.public_key_bytes()`. Using the keyring's copy is the only form of
this check that catches a backend/keyring divergence (e.g., the private key
was rotated in the backend but the keyring was not updated).

**Timeout enforcement:** Run the self-test using
`concurrent.futures.ThreadPoolExecutor(max_workers=1).submit(signer.sign, b"\x00"*32).result(timeout=0.5)`.
Catch `TimeoutError` → FAIL. This is the only stdlib-safe way to enforce a
500 ms wall-clock budget on a synchronous signer call. Without this, a
hung cloud backend blocks doctor for 15–60 seconds.

**Testability seam:** The check-7 function accepts an optional
`signer_factory: Callable[[str], Signer] | None = None` parameter,
defaulting to `get_signer`. Tests pass a lambda that returns a `Signer`
subclass with a controlled `sign()` method. The timeout path is tested by
passing a signer whose `sign()` sleeps past the 0.5 s budget.

Self-test is skipped in `--quick` mode.

### 2.5 Check 9 network-fetch injection

*Revised per testability review #6 (medium).*

The hosted-schema HTTP fetch is behind an injectable function:

```python
def check_hosted_schemas(fetch_url: Callable[[str], bytes] | None = None,
                         ...) -> CheckResult: ...
```

The `--quick` path passes `fetch_url=None` (skipped). Tests for the FAIL
case inject a callable that returns synthetic bytes. This removes the need
for the `responses` library (which intercepts `requests`-level HTTP, not
`subprocess.run(["curl", ...])`) and removes the undeclared `responses`
dependency from `pyproject.toml`.

`doctor.py` imports `check_hosted_schemas` lazily (inside the check-9
function body, not at module top-level) to avoid loading `validate.py`'s
full dependency graph during `--quick` runs. *Architecture review #8 (low)
noted this import-time concern.*

### 2.6 Output

Default is a compact table. Each row: `[PASS|WARN|FAIL] <title>` +
one-line detail. On any `FAIL` or `WARN`, a "Remedies" section appears
below with the `remedy` field for each non-PASS check.

`--json` emits `{"status": "fail", "checks": [...]}` for CI / menu-bar
consumption.

`--quick` skips signer self-test and hosted-schema check (no network).
Budget: <200ms. Used by the menu-bar app (§3) every 60s.

### 2.7 Tests

- `tests/test_doctor.py`
  - each check function called with a `tmp_path`-based synthetic
    filesystem — no `monkeypatch.chdir` required
  - fabricate each failure mode in turn; assert the named check fails and
    others pass
  - check 7 timeout: inject a signer whose `sign()` sleeps 1 s; assert
    FAIL within 0.6 s wall-clock
  - check 7 keyring-divergence: inject a signer whose `sign()` succeeds
    but signs with a different key than the keyring entry; assert FAIL
  - check 9 network-failure → WARN (inject a `fetch_url` that raises
    `ConnectionError`)
  - check 9 drift → FAIL (inject a `fetch_url` that returns wrong bytes)
  - check 10 missing `emitted` field → PASS (default `[]`, not crash)
  - check 11 retry: simulate partial JSON write via a file opened but not
    closed; assert PASS on retry
  - `--json` output keys present
  - `--quick` does not call the injected `fetch_url` (assert callable never
    invoked)
  - signer self-test uses the local backend against a generated key

### 2.8 Risks

- **Signer self-test can leave artifacts** (Vault creates audit events,
  KMS creates CloudTrail entries). Document that `dwc doctor` is a
  signing operation and will show up in audit logs. This is fine — it's
  the point.
- **Check 12's 30-day threshold** is a guess. Revisit after first real-world
  use; could be per-key via a new `keyring.json` field.
- **Check 5 O(n) scan (edge-cases review #10, medium):** On large shows
  (2,000+ clips, 10 events each), parsing 20,000 event records to find
  expired-key references may exceed the 2 s doctor budget. This is a
  "consider during implementation" item — profile on the reference corpus
  first. The reference corpus (40 clips) is well under budget. If the scan
  exceeds 1 s on a 400-clip production day, cache the scan result in a
  sidecar index file.

### 2.9 Estimate

1.5 engineer-days.

---

## 3. macOS menu-bar status app

### 3.1 Problem

DITs live in GUIs. A terminal window in the corner of a cart monitor is
not professional. But they don't want another *interactive* app — they
want ambient signal: is the watcher running, did the last 10 clips sign
cleanly, is anything in quarantine.

### 3.2 Scope

**Read-only.** Zero authoring. Zero key operations. Zero validation
overrides. The app shells out to `dwc doctor --quick --json` and reads
`.watch-state.json` + quarantine directory contents. That's it.

Any interactive change must be made from the CLI. This is a deliberate
design choice — the app cannot be a vector for silent misconfiguration
if it can't configure anything.

### 3.3 Deliverables

- New subdirectory `macos-statusbar/` in the repo
  - `DwcStatus.xcodeproj`
  - SwiftUI + MenuBarExtra (macOS 13+)
  - `DwcStatus/Models/DoctorReport.swift` — decodes `dwc doctor --json`
  - `DwcStatus/Models/WatchState.swift` — decodes `.watch-state.json`
    including the `emitted` field (guaranteed present from §1.8)
  - `DwcStatus/Views/MenuContent.swift`
  - `DwcStatus/Services/Poller.swift` — 60s timer
- `macos-statusbar/README.md` — build + codesign + notarize instructions
- GitHub Actions workflow `.github/workflows/macos-statusbar.yml` —
  builds, signs with a repo secret (Developer ID), notarizes, uploads
  `DwcStatus.dmg` as a release artifact

### 3.4 UI

Menu-bar icon: filled circle, tinted by state.

- **Green** — watcher running, doctor all-pass, no quarantine entries
- **Amber** — doctor WARN, or ≥1 validation failure in last 24h
- **Red** — doctor FAIL, watcher not running, or unrecovered quarantine
- **Grey** — `dwc` CLI not found on `PATH`

Click menu:

```
DWC Sidecar — Running
Last sidecar: A001C007_260115_R1AB (12s ago)

Today
  Signed        37
  Quarantined   1
  Recent signatures (last 5)
    A001C007  12s ago
    A001C006  01m ago
    A001C005  03m ago
    A001C004  05m ago
    A001C003  08m ago   <- click opens quarantine entry

Health (dwc doctor)
  11 checks passed
  1 warning — Key dwc-dit-01 expires in 14 days

─────────────────────────
Open watch folder…
Open quarantine…
Reveal sidecar in Finder…
─────────────────────────
Quit DwcStatus
```

No "Start watching" button. No "Re-sign". Those are CLI operations.

### 3.5 Data sources

- `dwc doctor --quick --json` every 60s (cheap, no network)
- `.watch-state.json` every 5s (just reads a file)
- `<watch-root>/quarantine/` directory listing every 5s
- Last-emitted sidecars: `emitted` array in `.watch-state.json`
  (added in §1.8; guaranteed present from that item)

### 3.6 Configuration

`~/Library/Application Support/DwcStatus/config.json`:

```json
{
  "watchRoot": "/Volumes/Mag_A001/WAR_Day01",
  "dwcBinary": "/opt/homebrew/bin/dwc",
  "pollDoctorSeconds": 60,
  "pollWatchStateSeconds": 5
}
```

First launch opens a chooser for `watchRoot`. `dwcBinary` is
auto-detected via `which dwc`; only prompted if not found.

### 3.7 Distribution

`DwcStatus.dmg` on GitHub releases. Signed with Developer ID, notarized.
Install: drag to Applications, launch. On launch it prompts the user to
install a LaunchAgent plist that starts the app at login (optional).

Homebrew cask once we have a 0.1.0 tag:
`brew install --cask dwc-sidecar-status`

### 3.8 Tests

- Swift unit tests for JSON decoders (doctor and watch-state fixtures
  sourced from `tests/fixtures/` — see §3.8a).
- One UI smoke test per state (green/amber/red/grey) via SwiftUI previews
  committed to the repo. Note: SwiftUI previews are not executable from
  CLI CI; they require Xcode Preview rendering. Use XCTest unit tests for
  the decoder logic; previews are for visual review only.

### 3.8a Fixture synchronisation

*Revised per testability review #8 (low).*

Swift test fixture files for `DoctorReport` and `WatchState` decoders are
generated from the Python test suite. A CI step diffs
`macos-statusbar/Tests/Fixtures/` against `tests/fixtures/` and fails if
they diverge. Swift fixture files must not be edited independently; the
canonical source is the Python tests.

### 3.9 Risks

- **Codesigning and notarization require a paid Apple Developer ID.** Budget
  $99/year; the key lives in a GitHub secret. Without this, users get a
  "damaged, move to Trash" dialog on first launch. Non-negotiable for a
  production DIT-facing app.
- **SwiftUI MenuBarExtra is macOS 13+.** Don't support older. DITs on
  Intel Macs running Monterey are edge cases; ship only for the majority.
  UNVERIFIED: whether GitHub Actions macOS runners support `xcodebuild`
  for SwiftUI apps with `MenuBarExtra` without additional setup.
- **The app is Mac-only.** No plan to port to Windows (YoYotta is Mac-only
  anyway; Silverstack is Mac-only; the majority of DIT carts are Macs).

### 3.10 Estimate

5 engineer-days. Most of that is codesigning, notarization, and the
GitHub Actions pipeline — not the SwiftUI.

---

## 4. Web-based validator drop-zone

### 4.1 Problem

Post houses and auditors receive sidecars from unknown sources. They
have no Python environment, don't want to install anything, and need an
answer in 10 seconds: "is this sidecar real?" A stateless browser tool
is the minimum-friction answer.

### 4.2 Approach: Pyodide in the browser, zero backend

The validator is pure Python, no syscalls beyond file reads. Pyodide
(CPython 3.12 compiled to WASM) runs it unmodified in the browser. No
server, no auth, no key material leaves the user's machine — validation
is signature verification against public keys, which is inherently
client-safe.

The user drops a zip containing `sidecar.omc.json` + all referenced
artifacts (MHL, AMF, FDL, CDL, camera file if included) + `keyring.json`.
Pyodide unpacks, runs `dwc_sidecar.validate.main()`, renders the
9-stage report.

Why not a server? A server means auth, rate limits, TLS, logging,
storage of uploaded clips (potentially copyrighted), abuse. Pyodide has
none of those. The only thing the server hosts is static files.

### 4.3 Deliverables

- `tools/web-validator/` directory in the repo
  - `index.html`
  - `app.js` — Pyodide loader, drop-zone handler, report renderer
  - `app.css`
  - `build.py` — copies the installed `dwc_sidecar` wheel into the
    static output (reusing the existing `tools/publish-schemas/` pattern)
- `.github/workflows/web-validator.yml` — builds + deploys to Cloudflare
  Pages on push to main
- Hosted at `https://validate.the-dwc.com` (new DNS; project
  `dwc-validator` on the same Cloudflare account as `dwc-schemas`)

### 4.4 UX

Single page. Drop zone in the middle. Supported inputs:

- A single `.omc.json` (validates Stage 1, 2, 3, 4, 7 — everything that
  doesn't require artifact bytes)
- A `.zip` containing the sidecar plus referenced artifacts + keyring
  (validates all 9 stages, subject to §4.4a path mapping)
- Multiple files dragged together (same effect as zip, using the File
  System Access API where available, falling back to manual assembly)

Output: the 9 stages as a vertical list, each with a green check / amber
warn / red fail dot and a disclosure triangle for details. Raw JSON
report available via "Copy report" button.

A prominent "Validated locally in your browser — nothing is uploaded"
banner. This is the product's trust pitch.

**Drop zone is disabled while a validation is running** to prevent
concurrent `os.chdir` calls in the same Pyodide instance. *Revised per
edge-cases review #12 (medium): Pyodide is single-threaded but the JS
promise chain can issue a second `runPythonAsync` before the first
completes if the drop handler is not gated.*

### 4.4a Artifact path mapping

*Revised per edge-cases review #6 (high).*

Production sidecars commonly record artifact paths as absolute paths (e.g.,
`/Volumes/Mag_A001/...`). Inside Pyodide's `/work/` virtual filesystem,
absolute production paths do not exist. Before calling `validate_as_json`,
the JS layer must map the sidecar's artifact `path` fields to the files
present in the zip. Strategy: match by filename (basename) first; if
ambiguous, match by the shortest suffix that uniquely identifies a file in
the zip tree. Pass `base_dir` as a parameter to `validate_as_json()` rather
than using `os.chdir`. If an artifact cannot be matched, Stage 6 reports
that artifact as "SKIP — file not provided in zip" rather than FAIL.

`validate_as_json(sidecar_path, base_dir, ...)` accepts `base_dir`
explicitly (no global `os.chdir`). *Also required by edge-cases review
#12 (medium) to prevent concurrent-call CWD collision.*

### 4.5 Implementation sketch

```javascript
// app.js (abridged)
const pyodide = await loadPyodide();
await pyodide.loadPackage(['micropip']);
await pyodide.runPythonAsync(`
  import micropip
  await micropip.install('/dwc_sidecar-0.1.0-py3-none-any.whl')
  await micropip.install(['jsonschema', 'rfc8785', 'cryptography',
                          'xxhash'])
`);
// Verified against Pyodide v0.27.3 (2026-04-23):
//   cryptography 42.0.5, jsonschema 4.21.1, xxhash 3.4.1 — bundled
//   rfc8785 — pure-Python, pulled from PyPI by micropip
//   blake3 — NOT available; see §4.6 guarded-import fallback.

async function validate(files) {
  // write each file into the Pyodide FS
  for (const f of files) {
    const buf = new Uint8Array(await f.arrayBuffer());
    pyodide.FS.writeFile(`/work/${f.name}`, buf);
  }
  return pyodide.runPythonAsync(`
    import json
    from dwc_sidecar.validate import validate_as_json
    json.dumps(validate_as_json('/work/sidecar.omc.json',
                                base_dir='/work'))
  `);
}
```

*Revised per architecture review #2 (high): `validate_as_json()` is a
standalone function that does not use `os.chdir`. The `base_dir` parameter
is passed explicitly.*

### 4.6 What doesn't work in Pyodide

*Verified 2026-04-23 against the Pyodide v0.27.3 lockfile:
`cryptography 42.0.5`, `jsonschema 4.21.1`, `xxhash 3.4.1` are bundled;
`blake3` and `rfc8785` are not. `rfc8785` is pure-Python and installs via
`micropip.install('rfc8785')` from PyPI — no further work needed. `blake3`
is a Rust extension with no pure-Python fallback, so the guarded-import
path below is required.*

- **`blake3`** — the `blake3` PyPI package is a Rust-compiled C extension.
  There is no pure-Python fallback in the package and no WASM wheel in
  the Pyodide v0.27.3 lockfile. `canonical.py` must guard the import:
  add a `try/except ImportError` around `import blake3` and raise a clear
  `ImportError("blake3 not available in this environment")` when a blake3
  artifact is requested. The web validator UI must surface this as
  "blake3 not supported in browser — use the CLI" rather than a generic JS
  error. *Revised per architecture review #6 (low) and edge-cases review
  #7 (high): the original "pure-Python fallback ships" claim is incorrect.*
- **`xxhash`** — C extension. Pyodide v0.27.3 bundles `xxhash 3.4.1`, so
  the reference corpus (Sony VENICE, MHL v1, `xxhash64be`) is validatable
  in the browser. No fallback needed.
- **PKCS#11, GCP-KMS, Vault, Azure-MHSM signers** — all skipped; the web
  validator only **verifies**, never signs, so signer imports are
  lazy-loaded in `signers/__init__.py` (already true)

Before shipping, add a `pytest` matrix that runs the tests under Pyodide
via `pytest-pyodide`, to catch syscall regressions. Add a `web` test extra
to `pyproject.toml` for this purpose. Scope the Pyodide matrix to
`tests/test_validate_as_json.py` only. *Revised per testability review
#9 (low).*

### 4.7 Tests

- `tests/test_validate_as_json.py` — parity with CLI output (9 stages,
  same counts); `validate_as_json` accepts `base_dir` as explicit parameter
- CI smoke: headless Chromium opens the deployed page, drops a fixture
  zip, asserts the report renders. Playwright, run on every PR.

### 4.8 Risks

- **Pyodide download is ~10 MB.** First load is slow on a phone tether
  at a location shoot. Cache aggressively (Cloudflare edge + Service
  Worker). Second visit is ~200ms to ready.
- **Large clip files.** A 30GB camera original in the zip will OOM the
  browser. Gate at 2GB in the drop-handler and recommend the CLI for
  larger. Stage 6 (artifact integrity) can be skipped via a checkbox
  if the user just wants to verify the chain.
- **Version drift.** The deployed wheel must track the latest release.
  The GitHub Actions workflow rebuilds on every tag; document this.
- **`blake3` absent from Pyodide.** Verified 2026-04-23 against
  Pyodide v0.27.3. Guarded-import fallback required per §4.6; sidecars
  using blake3 must be routed to the CLI with a clear UI message.
- **`cryptography` version compatibility.** Pyodide v0.27.3 bundles
  `cryptography 42.0.5`. `Ed25519PublicKey.from_public_bytes` has been
  part of `cryptography` since v2.6 (2019), so the Ed25519 verify path
  works. Confirm once more during the integration test (§4.7) in case
  Pyodide bumps to a stripped build.

### 4.9 Estimate

3 engineer-days including DNS, Cloudflare Pages setup, and the
validate_as_json refactor (0.5 days of which moves to phase-opening §0).

---

## 5. `dwc init` — one-command onboarding

### 5.1 Problem

First-time setup today is: install → keygen → write keyring.json →
write signers.json → export DWC_SIGNERS → edit .gitignore → run
sign-example → run validate. Eight steps, five concepts. A DIT reading
the README bounces at step 3.

### 5.2 Deliverables

- `src/dwc_sidecar/init.py`
- `dwc init` CLI subcommand
- Templates under `src/dwc_sidecar/data/templates/`
  - `signers.local.json.tmpl`
  - `signers.keychain.json.tmpl`
  - `signers.file.json.tmpl`
  - `launchagent.plist.tmpl` (macOS only)
  - `systemd.service.tmpl` (Linux only) — *added per edge-cases review
    #13 (low): the Linux template was described in §5.4 behavior but
    absent from deliverables*
- `tests/test_init.py`
- Update `README.md` quickstart to a single line: `pipx install
  dwc-sidecar && dwc init`

### 5.3 Interactive flow

```
$ dwc init
DWC sidecar setup

Host: macOS (arm64), Python 3.12.3
Working directory: /Volumes/Mag_A001/WAR_Day01

Where should your signing key live?
  1) macOS Keychain (recommended on this host)
  2) File on disk (portable, for Docker/CI)
  3) I'll configure a cloud / HSM backend myself
[1]: _

Signing kid [dwc-dit-01]: _
Keyring entry valid for [90] days: _

Install a LaunchAgent so `dwc watch` starts at login? [Y/n]: _
Watch folder [current directory]: _

Generating key...                            done
Writing keyring.json...                      done
Writing signers.json (DWC_SIGNERS target)... done
Writing ~/Library/LaunchAgents/com.dwc.sidecar.watch.plist...  done
Adding keys.priv.json to .gitignore (if present)...   done (not present, good)

Next steps:
  1) Add this line to ~/.zshrc:
       export DWC_SIGNERS="$PWD/signers.json"
  2) Verify your rig:
       dwc doctor
  3) Start watching:
       launchctl load ~/Library/LaunchAgents/com.dwc.sidecar.watch.plist

Done.
```

Non-interactive: `dwc init --backend keychain --kid dwc-dit-01 --yes`.
Required for CI / scripted provisioning.

### 5.4 Defaults by platform

*Revised per architecture review #6 (medium) and edge-cases review #3
(high): Linux systemd activation and non-systemd handling clarified.*

| Platform         | Default backend | Launch mechanism       |
|------------------|-----------------|------------------------|
| macOS            | `keychain`      | LaunchAgent            |
| Linux            | `file`          | systemd user unit      |
| Windows          | `file`          | (documented as manual) |
| Docker detected  | `file`          | (nothing auto)         |

Docker detection: presence of `/.dockerenv` or `container=docker` env.

**Linux systemd activation:** After writing the unit file to
`~/.config/systemd/user/dwc-sidecar-watch.service`, run:

```
systemctl --user daemon-reload && systemctl --user enable dwc-sidecar-watch.service
```

if `systemctl` is found on `PATH`. If `systemctl` is absent or the command
fails (e.g., no D-Bus session — common in Docker, CI, and non-systemd
distros), print a clear warning:

```
WARNING: systemd --user not available. The unit file has been written to
~/.config/systemd/user/dwc-sidecar-watch.service but could not be enabled
automatically. On a systemd system, run:
  systemctl --user daemon-reload
  systemctl --user enable dwc-sidecar-watch.service
On non-systemd systems, start the watcher manually.
```

**Platform detection seam (testability review #5, medium):**
Platform detection is isolated in a function `_detect_platform() -> str`
returning one of `"macos"`, `"linux"`, `"windows"`, `"docker"`. The init
entry point accepts `platform: str = _detect_platform()` as a parameter.
Tests pass a synthetic platform string to exercise all four branches on
any CI host.

### 5.5 Template engine and `$HOME` handling

*Revised per edge-cases review #7/#8 (medium): the original plan mixed
`string.Template` syntax (`$kid`) with `{{...}}` template markers in the
same section, which is contradictory.*

Templates use `{{...}}` markers (double-brace). Rendering uses
`str.replace("{{kid}}", kid)` — not `string.Template`. `string.Template`
uses `$`-prefixed variables and would treat `$HOME` in the LaunchAgent
plist as a variable to expand, which is the opposite of what launchd
requires (launchd must receive the literal string `$HOME`).

The LaunchAgent plist template preserves the literal `$HOME` token because
the renderer only replaces `{{...}}` markers. No `os.path.expanduser` or
`os.environ` expansion is applied during template rendering.

Add a test that verifies the rendered plist contains the literal string
`$HOME`, not the resolved home directory.

### 5.6 Templates

Example `signers.keychain.json.tmpl`:

```json
{
  "signers": {
    "{{kid}}": {
      "backend": "keychain",
      "service": "dwc-sidecar",
      "account": "{{kid}}"
    }
  }
}
```

### 5.7 Clock injection

*Revised per testability review #6 (medium).*

Any function in `init.py` that computes a validity window calls
`keygen._iso_days(days, now=now)` or equivalent, where `now: datetime |
None = None` defaults to `datetime.now(timezone.utc)`. Tests pass a fixed
`datetime` to produce deterministic `keyring.json` content. Same pattern
as required for `keygen.py`.

### 5.8 What `init` never does

- Never writes private keys outside the chosen backend (no `keys.priv.json`
  is ever generated by `dwc init`; that file is a dev-only artifact of
  the older `keygen` default)
- Never overwrites an existing `keyring.json` / `signers.json` without
  `--force`
- Never emits a cloud-backend config (GCP-KMS, Vault, Azure-MHSM) —
  those keys are created in the respective cloud console; init prints a
  pointer to the backend module's docstring instead

### 5.9 Keychain dummy sign and CI handling

*Revised per edge-cases review #4 (high).*

`dwc init --backend keychain` performs a dummy sign at the end of setup so
the macOS permission dialog appears during setup, not at 2am during
offload. However, `dwc init --backend keychain --yes` running in CI
(GitHub Actions macOS runner) will fail because no keychain item exists and
`KeychainSigner` raises `RuntimeError` when the item is missing.

Handling:
- In `--yes` mode with `--backend keychain`: if the dummy sign raises
  `RuntimeError`, exit with a specific error code (not a traceback) and
  the message: "Keychain backend requires an interactive session. Use
  `--backend file` for CI environments."
- Document in `docs/operations/doctor.md` that the keychain backend is
  not suitable for headless CI.
- The CI job in §5.10 that exercises init uses `--backend file` on the
  macOS runner, not `--backend keychain`.

### 5.10 Tests

- `tests/test_init.py` (uses `tmp_path` + `click.testing` or
  `pexpect` for interactive flows)
  - happy path on macOS → keychain backend, launchagent written
    (platform injected as `"macos"`)
  - `--backend file` with platform injected as `"linux"` → systemd unit
    written; `systemctl` absent → warning printed, no crash
  - `--backend file` with platform injected as `"linux"` → systemd unit
    written; `systemctl` present (mocked) → enable called
  - `--backend keychain --yes` when KeychainSigner raises → clean exit
    with specific error code, no traceback
  - `--yes` with missing required args exits nonzero with a
    specific error code (not a traceback)
  - refuses to overwrite existing `keyring.json` without `--force`
  - `keys.priv.json` is never created
  - Docker detection branch produces file backend, no launchagent
  - rendered LaunchAgent plist contains literal `$HOME`, not expanded path
  - rendered signers.json contains actual kid value, not `{{kid}}`
  - `keyring.json` validity window is deterministic with injected clock
- CI runs init with `--backend file` on a macOS runner and an Ubuntu
  runner, then runs `dwc doctor` — end-to-end smoke.

### 5.11 Risks

- **Keychain interactive prompt on first sign.** macOS will prompt
  "DwcStatus wants to use your confidential information stored in…" on
  first signer.sign(). This is correct behavior but will surprise a DIT
  mid-offload. Mitigation: `dwc init` performs a dummy sign so the
  prompt appears during setup, not at 2am. Document this in the post-
  init output.
- **LaunchAgent plist path.** Must use `$HOME` expansion by launchd, not
  `~`, or launchd rejects it silently. Template renders literal `$HOME`;
  tested explicitly (see §5.10).
- **systemd --user not available in all Linux environments.** Handled
  with explicit warning and instructions (§5.4).

### 5.12 Estimate

2 engineer-days.

---

## 6. DWC Status onboarding nudge + CLI Homebrew formula

### 6.1 Problem

`dwc init` (§5) collapsed producer-side setup into one command, but two
onboarding gaps remain:

1. **Discovery on the consumer side.** A user who installs DWC Status
   from the .dmg has no signal that they need to run `dwc init` (or even
   that a CLI exists). The current first-launch flow only asks for a
   watch root (`macos-statusbar/README.md` "First launch"). If the CLI
   is missing or no `signers.json` exists, the icon stays grey and the
   panel has nothing to say about it.
2. **CLI install friction.** `docs/quickstart.md` instructs `pipx
   install dwc-sidecar`. pipx works but is one more concept; many
   macOS users already have Homebrew. The DWC Status app's cask is at
   `dwc-sidecar-status`, but the CLI itself has no Homebrew formula.

A fresh-Mac user who downloads the .dmg therefore sees a grey icon,
gets no actionable next step, and has to find their way back to the
README. The user-facing fix is two-track: (a) make CLI install a
single-line `brew install dwc-sidecar`, and (b) have DWC Status detect
the missing pieces and surface copy-pasteable commands.

DWC Status remains read-only throughout: it never invokes `dwc init`,
never writes signer config, never generates keys. It detects state and
points the user at the right command. The design constraint from
`macos-statusbar/README.md` ("the app can't be a vector for silent
misconfiguration if it can't configure anything") is preserved.

### 6.2 Deliverables

**Track A — DWC Status onboarding panel:**
- `OnboardingState` enum + `Services/OnboardingDetector.swift` in
  `macos-statusbar/Sources/DwcStatus/`
- Three-state panel UI in `MenuContent.swift` with copy-to-clipboard
  and "Open Terminal" actions
- "Recheck" button that re-runs detection without app restart
- `macos-statusbar/Tests/DwcStatusTests/OnboardingTests.swift` covering
  each state transition with mock filesystem + environment probes
- Updated "First launch" section in `macos-statusbar/README.md`

**Track B — CLI Homebrew formula:**
- New repo `DigitalWorkflowCompany/homebrew-tap` with
  `Formula/dwc-sidecar.rb`
- Formula installs `dwc-sidecar` via Homebrew's
  `Language::Python::Virtualenv` helper (matching pipx's isolation
  model); wires `dwc` onto PATH at `/opt/homebrew/bin/dwc`
- `.github/workflows/release.yml` addition: build sdist + wheel and
  attach to the GitHub release alongside the existing macOS DMG
- `.github/workflows/homebrew-tap-bump.yml`: on `release: published`,
  runs `brew bump-formula-pr` against the tap repo with the new URL
  and sha256
- `docs/quickstart.md` lead changed to `brew install
  digitalworkflowcompany/tap/dwc-sidecar`; pipx kept as a documented
  fallback for non-mac / non-brew users

### 6.3 Onboarding state machine (Track A)

DWC Status detects three states:

| State                  | When DWC Status enters it                                                                                  |
|------------------------|------------------------------------------------------------------------------------------------------------|
| `cliMissing`           | `which dwc` returns nothing AND none of the discovered paths in `macos-statusbar/README.md:33` exist        |
| `signersUnconfigured`  | CLI present, but no `signers.json` discoverable AND no `DWC_SIGNERS` env var                                |
| `ready`                | CLI present AND a `signers.json` discoverable                                                              |

Panel UI per state:

- **`cliMissing`** — "Install the DWC CLI" header. Copy-button-prefilled
  command: `brew install digitalworkflowcompany/tap/dwc-sidecar`. Pipx
  fallback in an expandable section.
- **`signersUnconfigured`** — "Configure signing" header. "Open Terminal
  at `dwc init`" button. Explanatory line: "DWC Status doesn't perform
  setup itself — `dwc init` walks you through it (§5)."
- **`ready`** — current panel content (recent signatures, quarantined
  clips, doctor findings).

Both onboarding states show a "I've finished setup, recheck" button
that re-runs detection. Detection also runs on each `MenuBarExtra`
open (no 60s timer racing with mid-setup state).

### 6.4 Detection seam

Detection lives in a single Swift function with injectable probes:

```swift
enum OnboardingState { case cliMissing, signersUnconfigured, ready }

protocol FileSystemProbing {
    func exists(_ path: String) -> Bool
}
protocol EnvironmentProbing {
    func value(forName: String) -> String?
}

func detectOnboardingState(
    fs: FileSystemProbing = RealFileSystem(),
    env: EnvironmentProbing = RealEnvironment(),
    config: AppConfig
) -> OnboardingState
```

Tests pass synthetic `FileSystemProbing` and `EnvironmentProbing` mocks
to exercise all three branches without touching the real filesystem.
This mirrors the `_detect_platform()` injection seam used in `init.py`
(§5.4).

### 6.5 What DWC Status onboarding never does

- Never runs `dwc init` directly. It only opens Terminal at the command
  or copies the command to the clipboard.
- Never writes `signers.json` or `keyring.json`. Those remain CLI-only
  outputs.
- Never offers to install Homebrew itself. If `brew` is missing, the
  pipx fallback row covers that case.
- Never silently re-runs detection on a timer. Recheck is the only
  refresh path, so a misconfigured setup is visible and not papered
  over.

### 6.6 Homebrew formula details (Track B)

Formula source (`Formula/dwc-sidecar.rb` in the tap repo):

```ruby
class DwcSidecar < Formula
  include Language::Python::Virtualenv

  desc "Per-clip film-industry metadata sidecar — CLI"
  homepage "https://ns.the-dwc.com/sidecar/"
  url "https://github.com/DigitalWorkflowCompany/Metadata-Interchange-Format/releases/download/vX.Y.Z/dwc_sidecar-X.Y.Z.tar.gz"
  sha256 "..."
  license "MIT"

  depends_on "python@3.12"

  resource "rfc8785" do
    url "https://files.pythonhosted.org/packages/.../rfc8785-X.tar.gz"
    sha256 "..."
  end
  # ... cryptography, jsonschema, xxhash, blake3, pyyaml resources

  def install
    virtualenv_install_with_resources
  end

  test do
    system bin/"dwc", "--version"
  end
end
```

**Sdist source.** The formula consumes the sdist published to GitHub
Releases. The release workflow currently produces a macOS DMG; one
small addition (`python -m build` step) produces the sdist + universal
wheel and attaches them to the same release.

**Tap-bump automation.** The `homebrew-tap-bump.yml` workflow triggers
on `release: published`. It runs `brew bump-formula-pr` (Homebrew's
official command for this) against the tap repo with the new URL and
sha256. Failures surface in the same workflow-run notifications as
DMG-build failures; they don't block the release itself.

### 6.7 Why a tap and not homebrew-core

Homebrew core requires 50+ stargazers, "notability", and a stable
release history. We're below that threshold. The tap path is faster,
fully under our control, and visually equivalent for users
(`brew install digitalworkflowcompany/tap/dwc-sidecar` is one command).
Migration to homebrew-core is a Phase 03+ candidate if/when adoption
justifies it.

### 6.8 Tests

**Track A (Swift):**
- `OnboardingTests.swift`: three state transitions tested with mocked
  `FileSystemProbing` and `EnvironmentProbing`
- "Open Terminal" action invokes the right `osascript` snippet
  (assertion against the recorded command, not the real `Process` run)
- "Copy to clipboard" places the expected install command on
  `NSPasteboard.general` (assertion against pasteboard contents)
- Recheck triggers re-detection and updates the panel state
- Detection on `MenuBarExtra` open is idempotent (no side effects from
  repeated calls)

**Track B (Python + CI):**
- `tests/test_release_artifacts.py` asserts the release workflow
  produces both an sdist and a wheel matching
  `dwc_sidecar-X.Y.Z.{tar.gz,whl}` (smoke-tests the path the formula
  depends on)
- Tap repo carries its own CI: `brew test-bot --only-formulae` runs on
  every formula bump PR; a failed install or `brew test` fails the PR
  check before merge

### 6.9 Risks

- **Formula sdist sha256 drift.** Each release produces a new sha256;
  if the bump workflow fails silently, the formula points at a missing
  or mismatched tarball. Mitigation: `homebrew-tap-bump.yml` exits
  nonzero on any step failure, and notifications surface alongside
  DMG-build failures (existing operator habit).
- **Mid-setup state race.** If a user is mid-`dwc init` when DWC Status
  re-opens, detection might briefly see `signersUnconfigured` then
  `ready`. Acceptable: recheck is explicit, not on a timer; worst case
  is one extra click.
- **AppleScript permission prompt.** Recent macOS versions prompt for
  permission the first time DWC Status invokes `osascript`. Documented
  in the post-onboarding panel ("If macOS asks DWC Status to control
  Terminal, allow it — one-time").
- **Tap repo sprawl.** A separate `homebrew-tap` repo means one more
  thing to keep in sync. Mitigation: the tap repo contains nothing but
  `Formula/`; all source-of-truth lives in this repo and the bump is
  fully automated.

### 6.10 Estimate

- Track A (DWC Status onboarding panel): 1.5 engineer-days
- Track B (Homebrew tap + bump workflow): 1 engineer-day

Total: 2.5 engineer-days. Tracks are independent and parallelizable.

### 6.11 Sequencing within the phase

Track A grows the existing menu-bar app (§3), so it lands after §3
ships. Track B is independent of §3 and can land any time after §5
(`dwc init`) ships, since the formula installs the binary that hosts
§5.

Updated ship order, extending the §8 sequencing diagram:

```
[phase-opening] validate_as_json() refactor

5. dwc init
        |
        v
2. dwc doctor
        |
        v
1. ALE emitter
        |
        v
3. Menu-bar app
        |
        v
6. Onboarding nudge + brew formula  --- both tracks parallelizable
        |
        v
4. Web validator
```

### 6.12 What this section deliberately does not include

- Linux/Windows equivalent of Track A. Phase 03 candidate, alongside
  the Linux/Windows menu-bar app deferred in §9.5.
- A `brew uninstall` cleanup script for `~/Library/Application
  Support/DwcStatus`. State cleanup is a Homebrew cask convention;
  this is a formula, not a cask. macOS users with state to clean
  already know `defaults` / `rm`.
- Auto-installing Homebrew when missing. Users who want Homebrew
  install it themselves; users who don't have it use the pipx
  fallback.

---

## 7. Cross-cutting work

### 6.1 Packaging

Add extras in `pyproject.toml`:

```toml
[project.optional-dependencies]
web = ["pytest-pyodide"]  # Pyodide test matrix for web validator
init = []                  # stdlib only
```

No new runtime deps for doctor, init, ale-emitter — all stdlib.

*Revised per testability review #9 (low): `pytest-pyodide` is declared as
a `web` test extra, not in the base `dev` extra.*

### 6.2 Documentation restructure

Current single CLAUDE.md is engineering-facing and will remain so. Add
user-facing docs under `docs/`:

```
docs/
  quickstart.md              <- one page, calls dwc init (ships with v0.2.0)
  integration/
    ale-spike-results.md     <- spike outcome before ALE code is written
    silverstack.md           <- ships with v0.3.0, after real-app validation
    yoyotta.md               <- ships with v0.3.0
    shotput.md               <- ships with v0.3.0
  operations/
    doctor.md
    watch.md                 <- extract from CLAUDE.md
    signer-backends.md       <- extract + expand
  spec/
    v0.1/                    <- existing schemas + narrative
```

*Revised per sequencing review #4 (low): docs ship as part of each item's
PR. `docs/quickstart.md` with v0.2.0 (item 5). Integration docs with
v0.3.0 (item 1), only after real-app validation exit criteria are met.*

Link from README to `docs/quickstart.md`. Keep CLAUDE.md as-is for Claude
Code consumption.

### 6.3 Backward compatibility

None of these items changes the schema, the sidecar format, the event
canonicalization, or the validator stages. The ALE emitter is additive.
`dwc doctor` and `dwc init` are new subcommands. The menu-bar app is a
separate binary. The web validator consumes existing sidecars. The
`.watch-state.json` gains an `emitted` field additively (`_load_state`
defaults it to `[]`). All Phase-02 work is pure addition; v0.1 sidecars
produced before Phase 02 remain valid forever.

### 6.4 Release plan

Each item tagged independently:

- `v0.2.0` — dwc init + dwc doctor (CLI-internal, no schema impact);
  includes `docs/quickstart.md` and `docs/operations/`
- `v0.3.0` — ALE emitter (new external artifact, needs its own compat
  story); includes integration docs, only after real-app validation passes
- `v0.4.0` — web validator (separate deploy target, no wheel change)
- `v1.0.0-mac` — menu-bar app (separate versioning, since it's a binary)

### 6.5 Success metrics

Define upfront, measure at v0.4.0:

- Time-to-first-signed-sidecar on a clean Mac: today ~20 min, target <3 min
- Percentage of `dwc doctor` FAILs caught before a `dwc watch` run: target 90%
- Number of third-party sidecars dropped into the web validator in first
  30 days of launch: target >100 (instrument with Cloudflare analytics only,
  no per-user telemetry)

If the web validator sees <10 drops in 30 days, the product-critique
conclusion ("the bottleneck is consumer adoption, not UX") is confirmed
and subsequent phases should pivot to Resolve / MAM consumer integration.

---

## 8. Sequencing

```
[phase-opening] validate_as_json() refactor  <- half-day, stabilises validate.py

5. dwc init       --- unblocks everything below (clean-slate onboarding)
        |
        v
2. dwc doctor     --- consumed by menu-bar app; also trivially useful alone
        |
        v
1. ALE emitter    --- spike first (§1.1a); emitted field added here (§1.8)
        |
        v
3. Menu-bar app   --- polishes watch; emitted field already exists from item 1
        |
        v
4. Web validator  --- highest marketing value, lowest internal urgency
```

Total: 15.5 engineer-days + codesigning / DNS setup overhead *(revised
2026-04-23 from 13.5 after item 1 grew from ALE-only to ALE + Silverstack
Lua + Resolve Python; see §1.11)*.

Parallelizable: 1 (DIT integrations) and 4 (web) have no shared code paths
with 2/3/5. Within item 1, the ALE / Silverstack Lua / Resolve Python
tracks are independent and can be built in parallel; they share only the
field set defined in §1.4. The `validate_as_json()` refactor (phase-opening
§0) must land before items 2 and 4 start, so both tracks have a stable
`validate.py`.

### 7.1 Exit criteria per item

- **5 (init)**: a fresh Mac goes from `pipx install` to `dwc doctor`
  all-green in under 3 minutes without reading docs.
- **2 (doctor)**: all 12 checks pass on the reference corpus
  (`/Volumes/DWC_Shuttle-04/WAR/260115_SD084`); negative tests cover
  each check individually.
- **1 (DIT integrations)** *(revised 2026-04-23 into three tracks)*:
  - **ALE**: YoYotta ID (trial) and ShotPut Pro both import
    `dwc-columns-YYYY-MM-DD.ale` from the stub corpus and display the
    eight `DWC_*` columns correctly. Screenshots committed to
    `docs/integration/yoyotta.md` and `docs/integration/shotput.md`.
  - **Silverstack Lua**: trial license of Silverstack 9.2+ runs the
    `onStampVideo` script against a fixture clip and displays all eight
    `DWC_*` fields in the clip grid. Screenshot committed to
    `docs/integration/silverstack.md`.
  - **Resolve Python**: Resolve Studio 20 **and** 21 both run
    `apply_dwc_metadata.py` from Workspace → Scripts → Utility against
    a Media Pool populated with the stub corpus, and display all eight
    `DWC_*` fields in the Metadata inspector. Screenshots committed to
    `docs/integration/resolve.md`.
  - If any one track fails its exit criteria, that track can be descoped
    to a follow-up phase without blocking the others; they're
    independently shippable.
- **3 (menu-bar)**: notarized DMG downloads and launches cleanly on a
  fresh macOS install. Icon reflects state within 60s of a state change.
- **4 (web validator)**: stub sidecar validates to all-PASS in the
  hosted build. Network tab confirms zero uploads other than the
  initial static assets. `blake3` fallback (guarded import + "use the
  CLI" UI message) exercised against a blake3-hashed fixture; `xxhash`
  path exercised against the reference corpus.

---

## 9. Open questions

1. **ALE column naming** *(resolved 2026-04-26)*. Underscore throughout
   (`DWC_Signed`) is the right call. Verified against Avid Media Composer
   24.10.0.58607: all eight names survive ALE merge; the >14-char names
   (`DWC_LastVerified`, `DWC_SidecarPath`) come through intact, so the
   14-char truncation folklore is not a hard rule in 2024. Avid case-
   normalises to `Dwc_signed`; the underlying data is preserved.
   Documented in `docs/integration/avid.md`.
2. **Menu-bar app bundle identifier** *(resolved 2026-04-26)*. Decided
   `com.the-dwc.sidecar.status` — matches the `ns.the-dwc.com` schema
   authority, mirrors the underscore-aware `dwc.sidecar.*` customData
   domain naming, and is already in `macos-statusbar/Scripts/make_app.sh`
   and the bundled `Info.plist`. No further plan-level question; the ID
   is the App ID to register with Apple when the Developer Program
   membership activates (see `macos-statusbar/RELEASE.md`).
3. **Web validator domain** *(resolved 2026-04-26)*. Decided
   `validate.the-dwc.com` (subdomain) over `ns.the-dwc.com/validate`
   (path). Two factors drove it: (a) the validator's ~10 MB
   Pyodide + WASM bundle benefits from long-cache on the version-
   suffixed wheel, and a fresh Cloudflare Pages project lets us set
   that without inheriting the `max-age=300` clamp observed on
   `dwc-schemas` (memory: `project_cache_control_limitation.md`);
   (b) decoupling validator-URL lifetime from the schema host's
   immutable URLs — `ns.the-dwc.com/...` is contractually frozen,
   `validate.the-dwc.com` can be repointed/replatformed freely. DNS
   work is a single CNAME at the apex zone. One-time setup runbook
   at `tools/web-validator/DEPLOY.md`; cache rules at
   `tools/web-validator/_headers`.
4. **Key expiry policy default.** 90 days (from `dwc init`) is a guess.
   Realistic DIT engagement is 8–20 weeks per show. A per-show key with
   a rotation ceremony at wrap might be the better default. Revisit
   after one real production.
5. **Linux/Windows menu-bar app.** Deferred, but Phase 03 candidate.
   Electron is out (too heavy); a GTK4 app or Rust+Tauri is in scope
   if a Linux-based DIT cart materializes.
6. **`blake3` and `xxhash` in Pyodide** *(resolved 2026-04-23)*. Pyodide
   v0.27.3 bundles `xxhash 3.4.1` — the reference corpus (`xxhash64be`)
   validates in-browser unchanged. `blake3` is not bundled and has no
   pure-Python fallback; `canonical.py` guards the `import blake3`
   per §4.6 and the web validator UI surfaces "use the CLI" for blake3
   sidecars. No `xxhash` shim or Stage-6 restriction needed. `rfc8785`
   is pure-Python and installed via `micropip` from PyPI.
7. **Field-name delimiter** *(added 2026-04-23, resolved 2026-04-23)*.
   **Decision: always use underscores — `DWC_Signed`, not `DWC-Signed`.**
   Applies across every transport (ALE columns, Silverstack custom
   metadata keys, Resolve `SetMetadata` keys, any future sidecar consumer).
   The `~/Documents/Resolve-Tools/` sister tools use hyphens (`AMF-Name`,
   `FDL-Name`); `docs/integration/resolve.md` should carry a one-line note
   flagging the deliberate divergence so nobody "fixes" it. Convention is
   recorded in `CLAUDE.md` → Conventions a future instance should follow.

---

## 10. What this plan deliberately does not include

- No new signer backends (AWS KMS still won't support Ed25519; skipping).
- No schema changes. If the ALE column set grows beyond eight, that's
  an emitter-internal change, not a schema change.
- No Resolve / MAM consumer integration. That's Phase 03 and has its
  own design cost.
- No WebAuthn / FIDO2 signing. Interesting, but unproven on set.
- No cross-sidecar "reel" views beyond what `example-reel.omc.json`
  already models.
- No attempt to automate the DIT-app-side configuration (tempting, but
  reverse-engineering Silverstack preferences is precisely the path we
  rejected in §1.1).

---

## 11. Low-severity items — consider during implementation

The following low-severity findings from reviewers require no plan change
but should be kept in mind during implementation:

- **Unicode beyond Latin-1 in ALE (edge-cases review #10):** Test coverage
  targets `Café`-level Unicode. Whether Silverstack/YoYotta handle Arabic,
  Hebrew, or Emoji clip names is out of scope for automated tests; flag in
  integration docs and confirm during real-app testing.
- **Doctor check 8 remedy wording (edge-cases review #14):** The WARN
  remedy message for `keys.priv.json` present alongside a non-local
  backend should be explicit: "Run `rm keys.priv.json` — this file
  contains plaintext private keys and is no longer needed."
- **Doctor check 5 O(n) complexity (edge-cases review #10):** Profile on
  the reference corpus first. If the scan exceeds 1 s on a 400-clip day,
  introduce a sidecar index cache. Not a day-one concern.
- **SwiftUI previews as smoke tests (testability review #8):** Previews
  are not CLI-executable. Use XCTest unit tests for decoder logic; treat
  previews as visual review aids only.
- **Doctor check 9 import-time coupling (architecture review #8):**
  Import `check_hosted_schemas` lazily inside the check-9 function body.
