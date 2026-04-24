# Sequencing Review

**Plan reviewed:** `plans/phase-02.md`
**Verdict:** Minor reordering suggested

---

## Findings

### [high] `validate_as_json()` refactor is buried in the last item but is a shared prerequisite

- **Plan section:** §4.5 (web validator implementation sketch)
- **Issue:** The plan requires adding a `validate_as_json()` entry point to `validate.py` so the web validator can get structured output. This is described as "a pure refactor — the existing CLI path calls it and prints." However, `dwc doctor` check 9 (§2.3) also reuses the `--check-hosted` logic from the same file, and the doctor's `--json` output (§2.5) implies a machine-readable result format from the validator internals. More critically: the Pyodide integration test matrix (§4.6) and the web validator CI smoke test (§4.7) both depend on this refactor existing before they can be written. If item 4 is always shipped last, this is not a blocking problem in the stated order — but it becomes one if a second contributor picks up item 4 in parallel with item 2 (which §7 explicitly calls out as parallelizable). The refactor touches `validate.py`, which is the most central module in the codebase; doing it as an afterthought at the end of a parallel track risks a merge conflict with any doctor work that also imports or calls validate internals.
- **Depends on / blocks:** Item 4 (web validator) depends on the refactor. Item 2 (doctor) is independent of it but shares the same source file. Parallel execution of items 2 and 4 without coordinating this refactor first risks a merge conflict at `validate.py`.
- **Suggested reorder:** Extract the `validate_as_json()` refactor as the first commit of the phase — it is a pure internal change with no user-facing impact, no schema impact, and a natural test (`tests/test_validate_as_json.py` from §4.7 can be written immediately). This makes `validate.py` stable before items 2 and 4 both touch it in parallel. Estimated cost: half a day, already bundled into item 4's 3-day estimate — move that half-day to the front.

---

### [medium] Menu-bar app's `.watch-state.json` schema change (§3.7) is not gated on the watch integration test in item 1

- **Plan section:** §3.7, §1.6, §1.8
- **Issue:** Item 3 (menu-bar app) requires adding an `emitted` rolling log to `.watch-state.json` in `watch.py`. Item 1's integration test ("run `dwc watch` over a fixture, assert `dwc-columns.ale` matches a golden") also exercises `watch.py` and saves state via `_save_state()`. In the stated order 5 → 2 → 1 → 3, item 1 lands before item 3, which means item 1's golden output for `.watch-state.json` will be generated from the pre-`emitted` schema. When item 3 then modifies `_save_state()` to include the `emitted` list, item 1's integration test golden is stale and will fail until updated. This is bounded rework (one golden file update) but it is avoidable.
- **Depends on / blocks:** Item 3's `watch.py` change invalidates item 1's `.watch-state.json` golden if item 1 commits a golden that captures the old schema.
- **Suggested reorder:** Two options, either is acceptable. Option A: add the `emitted` field to `watch.py` as part of item 1 (it is a small additive change and the field is bounded at 100 entries as stated). The menu-bar app then reads a field that already exists. Option B: write item 1's integration test golden to not assert on `.watch-state.json` contents — only assert on `dwc-columns.ale`. This keeps the test narrowly scoped and avoids the staleness. Option A is preferable because it means the watch-state schema is stable before the menu-bar app is built.

---

### [medium] ALE exit criteria require real-app validation (Silverstack, YoYotta, Resolve) but this risk is not front-loaded

- **Plan section:** §7.1 (exit criteria for item 1), §1.9 (risks)
- **Issue:** The plan correctly identifies that "the ALE spec is Avid-proprietary and loosely documented" (§1.9). The exit criteria (§7.1) require Silverstack Lab, YoYotta ID, and Resolve Studio to actually import and display the eight `DWC_*` columns correctly before the item is considered done. This real-app validation is the highest-uncertainty moment in item 1 — it could surface column name rejections, encoding problems, or header block incompatibilities that require reworking `ale_emitter.py`. The plan schedules this validation only at the end of a 2-engineer-day item that also includes docs. If Silverstack rejects the column format (e.g., the `DWC_` prefix triggers an internal filter, or the header block ordering differs from what it expects), the entire emitter may need to be redesigned.
- **Depends on / blocks:** Item 1 (ALE emitter) completion blocks the integration docs (§1.7) and blocks item 3's exit criteria (the DIT-tool integration is one of the adoption drivers the menu-bar is built to serve). The ALE spec ambiguity is a risk that, if realized late, forces rework that is already in the past by the time items 3 and 4 ship.
- **Suggested reorder:** Before writing `ale_emitter.py`, generate a minimal hand-crafted `.ale` file with one `DWC_*` column and verify that Silverstack Lab, YoYotta, and Resolve all display it. This is a zero-code spike that takes half a day and can be done during or immediately before item 5. If all three tools accept it, the risk is retired. If one rejects it, the format decision can be made before any code is written. The plan already notes this risk; the reorder simply makes the spike explicit and moves it earlier.

---

### [low] Cross-cutting docs restructure (§6.2) is undated and has implicit dependencies on items 1 and 5

- **Plan section:** §6.2
- **Issue:** The docs restructure creates `docs/quickstart.md` (which calls `dwc init`, item 5) and `docs/integration/silverstack.md` etc. (which document item 1's ALE workflow). If docs are written before item 1's real-app validation (see finding above) confirms the ALE workflow, the integration docs may need to be rewritten. The plan does not assign §6.2 to a release tag or a step in the ship order.
- **Depends on / blocks:** Docs depend on items 5 and 1 being finalized. Writing them before item 1's exit criteria are met risks rework.
- **Suggested reorder:** Commit docs as part of each item's PR rather than as a separate cross-cutting effort. This is already implied by the per-item deliverable lists (§1.2 includes the three integration docs) but §6.2's restructure framing suggests it might be treated as a separate pass. Make explicit that §6.2's restructure ships with v0.2.0 (alongside item 5's `docs/quickstart.md`) and that the integration docs ship with v0.3.0 (item 1).

---

## Checkpoints identified in the plan

- **v0.2.0** (`dwc init` + `dwc doctor`): self-contained CLI release, no external artifacts, no new deploy targets. Natural pause point after item 5 and item 2.
- **v0.3.0** (ALE emitter): first external artifact (`dwc-columns.ale`). Breaking change surface for any consumer who maps columns. Natural checkpoint for real-app validation before tagging.
- **v0.4.0** (web validator): new Cloudflare Pages deploy target (`validate.the-dwc.com`). DNS and Pages setup can be done in parallel with coding.
- **v1.0.0-mac** (menu-bar app): separate binary versioning; codesigning and notarization are a hard gate before any user download.
- **ALE format spike** (implicit, suggested above): a zero-code checkpoint before item 1 coding begins.

---

## Aspects checked with no concerns

- The stated ship order 5 → 2 → 1 → 3 → 4 is correct at the coarse level. Item 5 (`dwc init`) has no dependencies on any other item and correctly comes first; it also provides the onboarding foundation that the integration docs reference.
- Item 2 (`dwc doctor`) correctly follows item 5. Doctor check 6 (signer config resolves) and check 7 (signer self-test) are straightforward to implement given the existing signer abstraction in `signers/__init__.py`. No new signer code is required.
- Items 1 and 4 are correctly identified as parallelizable with items 2 and 5 at the code level (no shared files except `validate.py` — see finding above).
- Item 3 (menu-bar app) correctly comes after item 2 because it shells out to `dwc doctor --quick --json`; building the Swift JSON decoder before the JSON schema is stable would be wasted work.
- The release tagging strategy (each item tagged independently) matches the project's existing pattern of shipping incremental commits to `main` with no long-lived branches evident in the git log.
- The plan correctly treats the menu-bar app as a separate binary with its own version series (`v1.0.0-mac`), avoiding coupling the macOS distribution lifecycle to the Python wheel lifecycle.
- The backward-compatibility statement (§6.3) is accurate: no plan item touches the sidecar schema, event canonicalization, or validator stage logic in a breaking way.
- The plan explicitly excludes schema changes (non-goal), keeping the v0.1 hosted schemas frozen and the CI drift check (`hosted-schema-drift.yml`) green throughout.
- The `--quick` flag on `dwc doctor` (§2.5) is correctly designed to be the menu-bar polling path, avoiding network I/O on the 60-second cadence. The full signer self-test is reserved for call-time use.
- The `os.replace` atomic rewrite for `dwc-columns.ale` (§1.6) is the correct approach and consistent with the existing watch-state persistence pattern in `watch.py`.

---

## Unverified claims

- **§4.6 — "pure-Python blake3 fallback ships, 10x slower but fine for single sidecars"**: the `blake3` PyPI package (`blake3>=0.4`, declared in `pyproject.toml`) is a Rust extension. Whether a pure-Python fallback is bundled or automatically selected in Pyodide's WASM environment is UNVERIFIED. If the fallback does not exist, Stage 6 and Stage 8 artifact verification will raise `ImportError` in the browser for any sidecar that uses `blake3` as its hash algorithm. This should be confirmed before the Pyodide test matrix is written.
- **§4.5 — `micropip.install` of `xxhash` in Pyodide**: `xxhash` is also a C extension. Whether Pyodide's package index carries a WASM wheel for `xxhash>=3` is UNVERIFIED. The same concern applies to `cryptography`, which has C components. Pyodide ships `cryptography` as a built-in package (UNVERIFIED for the specific version required by this project), but `xxhash` may not be available, which would break Stage 6/8 for sidecars using `xxh64` or `xxh3` — the hash algorithms used by the reference corpus (Sony VENICE, MHL v1 with `xxhash64be`).
- **§1.3 — "Silverstack/YoYotta accept LF [line endings] too but CRLF is safest"**: the ALE parsing behavior of Silverstack 8+, YoYotta ID, and ShotPut Pro for LF-only files is UNVERIFIED from public documentation. The plan's CRLF default is a reasonable conservative choice.
- **§3.3 — `MenuBarExtra` (macOS 13+)**: confirmed as a SwiftUI API introduced in macOS 13 Ventura. The constraint excluding Monterey (macOS 12) users is real. UNVERIFIED whether GitHub Actions macOS runners support `xcodebuild` for SwiftUI apps with `MenuBarExtra` without additional setup.
