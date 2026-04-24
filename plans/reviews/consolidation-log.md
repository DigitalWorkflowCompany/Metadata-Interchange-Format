# Consolidation Log

**Plan:** plans/phase-02.md
**Date:** 2026-04-22
**Reviewer verdicts:**
- Architecture: Sound with concerns
- Edge cases: Significant gaps
- Testability: Testable with minor changes
- Sequencing: Minor reordering suggested

---

## Changes applied

### From architecture review

- [high] `.watch-state.json` field name divergence (`last_mhl_sha256` vs
  `processed_mhl_sha256`) → §2.3b corrected the field name to
  `processed_mhl_sha256` throughout. §2.3b also specifies that `emitted`
  defaults to `[]` in `_load_state` when absent, making the schema
  evolution additive. §1.8 added the `emitted` field specification with
  the explicit backward-compatibility contract.

- [high] `validate_as_json()` refactor risks regression to five existing
  subprocess callers → §0 (new section) and §4.5 both make explicit:
  `validate_as_json()` is a standalone function calling stage functions
  directly; `main()` is left calling stage functions directly and must NOT
  delegate through `validate_as_json()`. The two paths are independent.
  §0 also incorporates the testability reviewer's companion finding
  (testability #3) that stage functions should return structured dicts
  instead of printing.

- [medium] `dwc ale-export --validate` without `--base-dir` silently skips
  signature verification when `keyring.json` is absent from CWD → §1.5
  specifies: when `--validate` is given without `--base-dir`, default
  `--base-dir` to the parent directory of the first sidecar input. CLI help
  text must document the keyring location requirement.

- [medium] `dwc watch --emit-ale` ALE file grows unboundedly across a
  multi-day shoot → §1.6 changed the ALE filename to
  `dwc-columns-YYYY-MM-DD.ale` (one file per day, not one cumulative file).
  The CLI command in §1.7 and §7.1 exit criteria updated accordingly.

- [medium] Signer self-test verifies against `signer.public_key_bytes()`
  instead of the keyring's copy — would not catch key rotation mismatches
  → §2.4 makes this explicit: verification must use
  `load_pubkey_b64(keyring[kid]["publicKey"])`, not
  `signer.public_key_bytes()`. The rationale (only this form catches
  backend/keyring divergence) is documented inline.

- [medium] Linux `dwc init` systemd activation path unspecified; non-systemd
  distros silently non-functional → §5.4 now specifies: run
  `systemctl --user daemon-reload && systemctl --user enable ...` if
  `systemctl` is on PATH; if absent or failing, print a named warning with
  manual instructions. Architecture reviewer also suggested adding
  `DBUS_SESSION_BUS_ADDRESS` to the doctor check for Linux headless
  sessions — this is captured in §10 (low-severity, consider during
  implementation) because it touches doctor check design and the
  architecture reviewer did not rate it above medium.

- [low] Reference to A-cam-reel regex in `mhl_walker.py` that does not
  exist → §1.5a corrects the cross-reference: the regex is defined in
  `ale_emitter.py`, not `mhl_walker.py`. The convention
  (`^([A-Z]\d{3})`) is documented.

- [low] `blake3` pure-Python fallback claim is incorrect; `blake3` is a
  Rust extension → §4.6 corrects the claim, marks the WASM availability
  as UNVERIFIED, specifies the required `try/except ImportError` guard in
  `canonical.py`, and specifies the UI-level error message for the browser.

- [low] Doctor check 9 `--check-hosted` logic imports `validate.py`'s
  full dependency graph even in `--quick` mode → §2.5 specifies lazy
  import of `check_hosted_schemas` inside the check-9 function body.
  Also moved to §10 (low-severity) for implementation reminder.

### From edge-cases review

- [critical] ALE atomic rewrite is not crash-safe when stale `.tmp` exists
  from a prior crash → §1.6 specifies the four-step sequence: (1) delete
  `.tmp` if present; (2) read production file; (3) write `.tmp`; (4)
  `os.replace`. The re-read step must always open the production filename,
  never `.tmp`. Test added to §1.9 (stale `.tmp` deleted before re-read).

- [critical] ALE rewrite O(n) with data-loss window on process kill →
  §1.6 documents this as an accepted trade-off (sidecar is source of
  truth; ALE is a derived view) and §1.9 Risks names it explicitly with
  the recovery command.

- [high] Tab characters in a column value corrupt the ALE row → §1.3
  mandates sanitisation of `\t`, `\r`, `\n` in all column values before
  emission. §1.9 adds a test for tab-in-value.

- [high] CRLF in a column value splits the row → combined with the tab
  finding above; §1.3 covers all three control characters.

- [high] ALE dedup key collision on clips with the same `Name` from
  different reels → §1.6 changes the dedup key from `Name` to
  `DWC_SidecarPath`. §1.9 adds a test asserting two sidecars with the
  same `Name` but different `DWC_SidecarPath` are not merged.

- [high] Doctor check 10 references field `last_mhl_sha256` that does not
  exist and has an implicit sequencing dependency on §3.7 → §2.3b corrects
  the field name to `processed_mhl_sha256` and redefines the check against
  the existing schema. The sequencing dependency is resolved by moving the
  `emitted` field addition into item 1 (§1.8) per sequencing reviewer
  recommendation.

- [high] Signer self-test 500 ms timeout has no enforcement mechanism →
  §2.4 specifies `ThreadPoolExecutor(max_workers=1).submit(...).result(timeout=0.5)`
  with `TimeoutError` catch → FAIL. The test for this path is added to §2.7.

- [high] `dwc init --backend keychain --yes` in CI triggers unhandled
  `RuntimeError` from `KeychainSigner` → §5.9 specifies clean exit with
  a specific error code and message. CI job in §5.10 uses `--backend file`
  on macOS runner. Test added to §5.10.

- [high] Web validator: production sidecar absolute paths do not resolve
  inside Pyodide's `/work/` filesystem → §4.4a specifies a JS-layer path
  mapping strategy (match by basename, then shortest unique suffix). Stage 6
  reports unmatched artifacts as "SKIP — file not provided in zip" rather
  than FAIL. `validate_as_json()` accepts `base_dir` as an explicit
  parameter (no `os.chdir`).

- [high] Web validator: `blake3` pure-Python fallback claim is incorrect →
  addressed jointly with architecture review #6 above; see §4.6.

- [medium] Doctor check 11 concurrent write with `dwc watch` produces
  spurious FAIL → §2.3c adds a retry: 1–2 attempts, 50 ms apart, before
  marking a file FAIL.

- [medium] `string.Template` mismatch: plan shows `{{...}}` syntax but
  `string.Template` uses `$...` syntax; `$HOME` in LaunchAgent plist
  would be expanded → §5.5 (new section) replaces `string.Template` with
  `str.replace("{{kid}}", kid)`. LaunchAgent plist template preserves
  literal `$HOME` because the renderer only replaces `{{...}}` markers.
  Test added to §5.10 for literal `$HOME` in rendered plist.

- [medium] ALE deletion mid-day loses all prior rows; no recovery path
  documented → §1.6 and §1.7 now document recovery via
  `dwc ale-export <watch-root>/*.omc.json`.

- [medium] LaunchAgent plist `$HOME` expansion clarified → resolved jointly
  with the `string.Template` finding above; §5.5 covers both.

- [medium] Doctor check 5 O(n) scan unspecified time budget → noted in
  §2.8 (risks) and §10 (low-severity implementation note). Not added as a
  plan constraint because on the reference corpus (40 clips) it is well
  within budget; profiling deferred.

- [medium] Web validator `os.chdir` global state / concurrent drop →
  §4.4 specifies the drop zone is disabled while a validation is running.
  §4.4a and §4.5 change `validate_as_json()` to accept `base_dir`
  explicitly, eliminating the `os.chdir` call.

- [medium] Doctor check 9 network failure in firewall-blocked environments
  → §2.3a specifies: network failure → WARN ("could not verify — network
  unavailable"); fetched-but-diverged → FAIL. §2.7 adds tests for both
  paths via injected `fetch_url`.

- [low] Unicode beyond Latin-1 in ALE → §1.10 Risks and §10
  (low-severity) flag for real-app testing. No code change required beyond
  the UTF-8 declaration already in §1.3.

- [low] Linux `systemd.service.tmpl` missing from §5.2 deliverables →
  added to §5.2.

- [low] Doctor check 8 WARN remedy message should be explicit →
  §2.3 table updated with explicit remedy text. Also in §10.

### From testability review

- [high] Doctor check functions hard-code `os.getcwd()` — no injection
  seam for unit tests → §2.3 specifies the function signature pattern:
  each check accepts explicit `Path` arguments. The CLI entry point passes
  `Path.cwd() / "keyring.json"` etc. This is the "seam" that makes each
  check independently testable with `tmp_path`.

- [high] Signer self-test has no mock seam — live credentials required
  → §2.4 adds the `signer_factory: Callable[[str], Signer] | None = None`
  parameter. §2.7 adds tests for the timeout and keyring-divergence FAIL
  paths using injected signers.

- [high] `validate_as_json()` entry point has no existing tests, and
  stdout-capture implementation would make stage results unassertable →
  §0 requires stage functions to return structured dicts; `validate_as_json()`
  assembles them. §0 deliverables include `tests/test_validate_as_json.py`.

- [medium] ALE I/O inlined in `Watcher._process()` — no seam for unit
  testing → §1.2 specifies `ale_emitter.update_ale()` as a standalone
  function. §1.9 explicitly states tests exercise this function without
  instantiating a `Watcher`.

- [medium] `init.py` branches on `sys.platform` without injection → §5.4
  specifies `_detect_platform() -> str` and the entry point accepting
  `platform: str = _detect_platform()`. §5.10 tests all four platform
  branches by injecting synthetic strings.

- [medium] Clock injection for validity window — non-deterministic tests
  → §5.7 (new section) specifies `now: datetime | None = None` parameter
  pattern for `init.py`. §1.6 and §1.4 (`DWC_LastVerified`) apply the
  same pattern via the `ale_emitter` `now=` parameter. §5.10 adds
  assertion that `keyring.json` validity window is deterministic.

- [medium] Doctor check 9 uses `curl` subprocess — `responses` library
  cannot intercept it; `responses` undeclared dependency → §2.5 replaces
  the `curl`-based approach with an injectable `fetch_url: Callable` seam.
  `responses` library is not used and not added to `pyproject.toml`.

- [medium] `Watcher._validate()` subprocess makes ALE integration test
  slow → §1.9 specifies the integration test uses `--no-validate`
  equivalent at the API level to stay in-process.

- [low] Swift fixture files can drift from Python fixtures → §3.8a (new
  subsection) specifies a CI step that diffs `macos-statusbar/Tests/Fixtures/`
  against `tests/fixtures/` and fails on divergence.

- [low] `DWC_LastVerified` non-deterministic in golden comparison →
  addressed by the clock injection in §1.6 (`now=` parameter). §1.9
  golden file test uses a fixed datetime.

- [low] `pytest-pyodide` not in `pyproject.toml`, no CI job defined →
  §6.1 adds `pytest-pyodide` to a `web` test extra. §4.6 scopes the
  Pyodide test matrix to `tests/test_validate_as_json.py` only, in a
  separate CI job.

### From sequencing review

- [high] `validate_as_json()` refactor is a shared prerequisite for items
  2 and 4 but was buried inside item 4; parallel execution of items 2 and
  4 without it would cause merge conflicts in `validate.py` → §0 (new
  section) extracts the refactor as the first commit of the phase. Its 0.5-day
  cost is moved from item 4's estimate to the phase-opening slot.

- [medium] `watch.py` `emitted` field change (originally §3.7) would
  invalidate item 1's integration test golden when item 3 lands → §1.8
  moves the `emitted` field addition into item 1. Item 3 reads a field
  that already exists. Original §3.7 section removed and replaced with
  §1.8.

- [medium] ALE format spike risk is high-uncertainty but not front-loaded
  → §1.1a adds an explicit spike as the first activity of item 1 (before
  writing `ale_emitter.py`). Spike result committed to
  `docs/integration/ale-spike-results.md`. §7.1 exit criteria updated to
  reference the spike gate.

- [low] Docs restructure (§6.2) undated with implicit dependencies on
  items 1 and 5 → §6.2 now assigns each doc directory to a release tag:
  `docs/quickstart.md` and `docs/operations/` with v0.2.0; integration
  docs with v0.3.0 after real-app validation passes. §6.4 release plan
  updated to match.

---

## Conflicts between reviewers

- **Architecture reviewer (§4.5)** said `validate_as_json()` should call
  stage functions directly (not have `main()` delegate to it). **Testability
  reviewer (§0)** said stage functions should return structured dicts and
  `main()` should call `validate_as_json()`. These are partially in tension.
  **Resolution:** Stage functions return structured dicts (testability
  finding applied). `validate_as_json()` calls stage functions and assembles
  dicts. `main()` calls stage functions directly (not through
  `validate_as_json()`), formats results from stage function return values,
  and prints. This satisfies both: the two code paths are independent (no
  regression risk to subprocess callers) and stage functions are individually
  testable. The one-line difference from the architecture reviewer's
  suggestion is that `main()` now processes structured return values instead
  of relying on stage functions printing to stdout — but since we own the
  stage functions, this is a safe internal refactor.

- No other direct conflicts between reviewers were found. All four reviewers
  independently flagged the `blake3` pure-Python fallback claim as incorrect,
  which confirms that finding strongly.

---

## Findings acknowledged but not applied

- [medium] Architecture review: "Add `DBUS_SESSION_BUS_ADDRESS` to doctor
  check for Linux to detect headless sessions where user units cannot
  activate." — Not added as a named doctor check. The §5.4 warning path
  (print instructions when `systemctl` is absent or fails) covers the
  user-visible consequence. A dedicated doctor check for a D-Bus session
  address would only be relevant on Linux and would need its own check
  number and test; the marginal value over the `systemctl` presence probe
  is low. Noted in §10 as a "consider during implementation" item.

- [low] Testability review: "SwiftUI preview tests as smoke tests (§3.9)
  — previews are not CLI-executable." — The plan retains the mention of
  SwiftUI previews as visual review aids in §3.8 but explicitly clarifies
  they are not executable CI tests. The actual automated tests use XCTest
  unit tests for decoder logic. This is a clarification, not a dropped
  feature — the user-facing intent (having committed UI smoke coverage)
  is preserved at the XCTest level.

- [low] Edge-cases review: "Doctor check 5 O(n) complexity — may exceed 2s
  budget on large shows." — Not added as a plan constraint. On the reference
  corpus (40 clips), the scan is well within budget. The plan §2.8 notes
  this risk and defers the cache design to implementation, consistent with
  the reviewer's own framing ("not a correctness issue").

---

## Unverified items carried into the revised plan

- **§4.6 / §4.8:** Whether `blake3` is available as a WASM-compiled wheel
  in the Pyodide 0.26+ package index. TODO for the implementer: check
  the Pyodide package list before writing `build.py`. If unavailable, add
  `try/except ImportError` guard in `canonical.py` and surface a browser
  error message.

- **§4.6 / §4.8:** Whether `xxhash>=3` is available as a WASM wheel in
  Pyodide. The reference corpus uses `xxhash64be`; if unavailable, the
  web validator cannot verify the most common production case without
  an additional fallback or restriction. TODO: verify and resolve before
  the web validator ships.

- **§4.5 / §4.8:** Whether the Pyodide-bundled `cryptography` package
  version is compatible with the `Ed25519PublicKey.from_public_bytes` API
  used in `canonical.py`. TODO: verify the Pyodide `cryptography` version
  against `pyproject.toml` pinning.

- **§1.3:** Whether Silverstack 8+, YoYotta ID, and ShotPut Pro accept
  LF line endings in ALE files. Plan defaults to CRLF as the safe choice.
  Confirm during the §1.1a format spike.

- **§1.7:** Whether Silverstack 8+ remembers imported custom columns across
  project sessions. Confirm during real-app testing (§7.1 exit criteria).

- **§3.3 / §3.9:** Whether GitHub Actions macOS runners support
  `xcodebuild` for SwiftUI apps with `MenuBarExtra` without additional
  setup. TODO: verify runner image docs before writing
  `.github/workflows/macos-statusbar.yml`.

- **§3.4 / §3.9:** `MenuBarExtra` macOS 13+ requirement is consistent with
  public Apple documentation but not verified against local project files.
  Treat as confirmed for planning purposes.

---

## Recommended next step

Ready for implementation. Begin with the phase-opening `validate_as_json()`
refactor (§0), then proceed 5 → 2 → 1 (with §1.1a spike before coding) → 3 → 4.
Resolve the three Pyodide package availability TODOs (blake3, xxhash,
cryptography version) before item 4 enters implementation.
