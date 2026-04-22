# Phase 02 — Adoption & Ergonomics

Scope: ship the five highest-leverage usability wins identified in the
product-critique discussion of 2026-04-22.

  1. DIT-tool integration via ALE round-trip + watch-folder (Silverstack /
     YoYotta / ShotPut Pro)
  2. `dwc doctor` — pre-flight audit of a production host
  3. macOS menu-bar status app (read-only)
  4. Web-based validator drop-zone (stateless, public)
  5. `dwc init` — one-command onboarding

Each item is independently shippable. Ship order is 5 → 2 → 1 → 3 → 4
(highest adoption-leverage-per-effort first; see §7).

Non-goals in this phase: schema changes (would bump to v0.2/), new
signer backends, new hash algorithms, Windows/Linux menu-bar apps.

---

## 1. DIT-tool integration via ALE round-trip

### 1.1 Problem

Silverstack, YoYotta ID, and ShotPut Pro have no scripting surface worth
targeting. YoYotta has no API. Silverstack has no public SDK. Reverse-
engineering is fragile and adversarial. But all three **import ALE** and
display custom columns in their clip grids. ALE is the integration.

### 1.2 Deliverables

- `src/dwc_sidecar/ale_emitter.py` — new module
- `dwc ale-export <sidecar...>` CLI subcommand
- `dwc watch --emit-ale` flag (default: on)
- `docs/integration/silverstack.md`, `docs/integration/yoyotta.md`,
  `docs/integration/shotput.md` — per-app setup (1 page each)
- `tests/test_ale_emitter.py`
- A sample `dwc-columns.ale` committed alongside the stub data so reviewers
  can open it in Silverstack Lab without running the tool

### 1.3 ALE format

ALE is tab-separated, with a fixed header block. We emit UTF-8 with CRLF
line endings (Avid convention; Silverstack/YoYotta accept LF too but CRLF
is safest for round-trip through Windows Avid systems).

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
  OMC `clipName` via the A-Cam-reel regex in `mhl_walker.py`).

### 1.6 `dwc watch --emit-ale`

Default on. After each sidecar emission the watcher appends to
`<watch-root>/dwc-columns.ale`. Append semantics:

- If the ALE does not exist: write full header + one data row.
- If it exists: re-read, dedupe by `Name` column (latest row wins), rewrite.
  Rationale: a clip can be re-signed (additional event appended), and the
  DIT wants the grid to reflect the current state, not the first-seen state.

Rewrite is atomic: write to `.dwc-columns.ale.tmp` then `os.replace`.

Failure mode: if ALE rewrite raises, log WARN and continue. Sidecar
emission must never be blocked by ALE I/O.

### 1.7 Per-app setup docs (outline)

All three docs follow the same four-step recipe, differing only in
screenshots:

  1. Configure output — point the app's reports folder at the same
     directory as clip offloads (this is already convention).
  2. Run `dwc init` once (see §5).
  3. `launchctl load ~/Library/LaunchAgents/com.dwc.sidecar.watch.plist`.
  4. In the DIT app, `File → Import → ALE`, point at `dwc-columns.ale`.
     Columns appear in the clip grid.

For Silverstack specifically, note that Silverstack 8+ remembers imported
custom columns across project sessions, so step 4 is a one-time action per
project.

### 1.8 Tests

- `tests/test_ale_emitter.py`
  - round-trips a fixture sidecar → ALE → parse-back → asserts all 8
    `DWC_*` columns match source
  - dedup: two sidecars for the same clip (different `seq`) produce one
    row, and it's the later one
  - CRLF line endings
  - tab delimiter survives values containing spaces
  - unicode clipName (e.g. `A001_Café_260115`) survives round-trip
- Integration test: run `dwc watch` over a fixture, assert
  `dwc-columns.ale` matches a golden.

### 1.9 Risks

- **ALE spec is Avid-proprietary and loosely documented.** Mitigate by
  testing against actual Silverstack/YoYotta/Resolve imports before
  tagging the release. Keep the emitter minimal: only the header fields
  all three tools agree on.
- **Column name bikeshedding.** Lock the names in this plan; any future
  change is a breaking change for consumers who built grids on them.

### 1.10 Estimate

2 engineer-days including docs.

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

Each check is a function returning `CheckResult(status, title, detail,
remedy)` where `status ∈ {PASS, WARN, FAIL}`. Doctor exits `0` if no
`FAIL`, `1` otherwise. `WARN` never fails the check.

| # | Check                                                             | FAIL if…                                                               |
|---|-------------------------------------------------------------------|------------------------------------------------------------------------|
| 1 | Python ≥ 3.11                                                     | older                                                                  |
| 2 | Required packages importable (`jsonschema`, `rfc8785`, `cryptography`, `xxhash`, `blake3`) | any ImportError                                           |
| 3 | All declared hash algs in `canonical.HASH_ALGS` resolve           | optional backend missing *and* referenced by any sidecar in CWD        |
| 4 | `keyring.json` present and parses                                 | missing / malformed                                                    |
| 5 | Every keyring entry has valid `validFrom` ≤ now ≤ `validUntil`    | any key expired **and** referenced by events in CWD sidecars           |
| 6 | Signer config resolves for every kid in the keyring               | `DWC_SIGNERS` points at missing file, or kid without a backend         |
| 7 | Each signer backend self-test passes (see §2.4)                   | backend refuses to sign a throwaway 32-byte payload                    |
| 8 | No plaintext `keys.priv.json` present when backend ≠ `local`      | WARN only — user might still want dev defaults                         |
| 9 | Local schemas byte-match `ns.the-dwc.com` (reuses `--check-hosted` logic) | drift detected (same check CI runs)                             |
|10 | `.watch-state.json` in CWD, if present, is parseable and its `last_mhl_sha256` file still exists | stale state blocks resume on next `dwc watch`                  |
|11 | All `*.omc.json` in CWD parse as JSON and contain a `customData[dwc.sidecar.*]` block | corrupt file in tree                                         |
|12 | Key window expiry > 30 days away                                  | WARN if < 30 days, FAIL if already expired                             |

### 2.4 Signer self-test

For each kid, call `signer.sign(b"\x00" * 32)` and verify with the matching
public key in the keyring. This exercises: credentials, network (for
cloud backends), token state (Vault), unlocked keychain (macOS), HSM slot
login (PKCS#11). Total round-trip budget: 500ms per kid; timeout → FAIL.

Self-test is the most valuable single check in the doctor — it catches
GCP-KMS credential rotation, Vault token expiry, and YubiHSM lockout
before the DIT sees a stage-4 signature failure mid-offload.

### 2.5 Output

Default is a compact table. Each row: `[PASS|WARN|FAIL] <title>` +
one-line detail. On any `FAIL` or `WARN`, a "Remedies" section appears
below with the `remedy` field for each non-PASS check.

`--json` emits `{"status": "fail", "checks": [...]}` for CI / menu-bar
consumption.

`--quick` skips signer self-test and hosted-schema check (no network).
Budget: <200ms. Used by the menu-bar app (§3) every 60s.

### 2.6 Tests

- `tests/test_doctor.py`
  - fabricate a tmp CWD with each failure mode in turn; assert the named
    check fails and others pass
  - `--json` schema (not formal, just keys)
  - `--quick` does not touch the network (use `responses` library to
    assert no HTTP)
  - signer self-test uses the local backend against a generated key

### 2.7 Risks

- **Signer self-test can leave artifacts** (Vault creates audit events,
  KMS creates CloudTrail entries). Document that `dwc doctor` is a
  signing operation and will show up in audit logs. This is fine — it's
  the point.
- **Check 12's 30-day threshold** is a guess. Revisit after first real-world
  use; could be per-key via a new `keyring.json` field.

### 2.8 Estimate

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
  ✓ Signed        37
  ⚠ Quarantined   1
  Recent signatures (last 5)
    ✓ A001C007  12s ago
    ✓ A001C006  01m ago
    ✓ A001C005  03m ago
    ✓ A001C004  05m ago
    ✗ A001C003  08m ago   ← click opens quarantine entry

Health (dwc doctor)
  ✓ 11 checks passed
  ⚠ 1 warning — Key dwc-dit-01 expires in 14 days

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
- Last-emitted sidecars: tail of `.watch-state.json`'s `emitted` array
  (new field — see §3.7)

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

### 3.7 Upstream change: `.watch-state.json` gets an `emitted` rolling log

`watch.py` currently stores `processed_mhls` (a set). Add an `emitted`
list, capped at 100, each entry `{clipName, omcPath, signedAt, status}`.
This is what the menu-bar app reads for the "Recent signatures" section.

Bounded size is important — the menu-bar app should not grow unbounded
state.

### 3.8 Distribution

`DwcStatus.dmg` on GitHub releases. Signed with Developer ID, notarized.
Install: drag to Applications, launch. On launch it prompts the user to
install a LaunchAgent plist that starts the app at login (optional).

Homebrew cask once we have a 0.1.0 tag:
`brew install --cask dwc-sidecar-status`

### 3.9 Tests

- Swift unit tests for JSON decoders (doctor and watch-state fixtures
  copied from the Python tests)
- One UI smoke test per state (green/amber/red/grey) via SwiftUI previews
  committed to the repo

### 3.10 Risks

- **Codesigning and notarization require a paid Apple Developer ID.** Budget
  $99/year; the key lives in a GitHub secret. Without this, users get a
  "damaged, move to Trash" dialog on first launch. Non-negotiable for a
  production DIT-facing app.
- **SwiftUI MenuBarExtra is macOS 13+.** Don't support older. DITs on
  Intel Macs running Monterey are edge cases; ship only for the majority.
- **The app is Mac-only.** No plan to port to Windows (YoYotta is Mac-only
  anyway; Silverstack is Mac-only; the majority of DIT carts are Macs).

### 3.11 Estimate

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
  (validates all 9 stages)
- Multiple files dragged together (same effect as zip, using the File
  System Access API where available, falling back to manual assembly)

Output: the 9 stages as a vertical list, each with a green check / amber
warn / red fail dot and a disclosure triangle for details. Raw JSON
report available via "Copy report" button.

A prominent "Validated locally in your browser — nothing is uploaded"
banner. This is the product's trust pitch.

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

async function validate(files) {
  // write each file into the Pyodide FS
  for (const f of files) {
    const buf = new Uint8Array(await f.arrayBuffer());
    pyodide.FS.writeFile(`/work/${f.name}`, buf);
  }
  return pyodide.runPythonAsync(`
    import json, os
    os.chdir('/work')
    from dwc_sidecar.validate import validate_as_json
    json.dumps(validate_as_json('sidecar.omc.json'))
  `);
}
```

Requires a small addition to `validate.py`: a `validate_as_json()` entry
point that returns a dict instead of printing. This is a pure refactor
— the existing CLI path calls it and prints.

### 4.6 What doesn't work in Pyodide

- `blake3` — pure-Python fallback ships, 10× slower but fine for single
  sidecars
- PKCS#11, GCP-KMS, Vault, Azure-MHSM signers — all skipped; the web
  validator only **verifies**, never signs, so signer imports are
  lazy-loaded in `signers/__init__.py` (already true)

Before shipping, add a `pytest` matrix that runs the tests under Pyodide
via `pytest-pyodide`, to catch syscall regressions.

### 4.7 Tests

- `tests/test_validate_as_json.py` — parity with CLI output (9 stages,
  same counts)
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

### 4.9 Estimate

3 engineer-days including DNS, Cloudflare Pages setup, and the
validate_as_json refactor.

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

Generating key...                            ✓
Writing keyring.json...                      ✓
Writing signers.json (DWC_SIGNERS target)... ✓
Writing ~/Library/LaunchAgents/com.dwc.sidecar.watch.plist...  ✓
Adding keys.priv.json to .gitignore (if present)...   ✓ (not present, good)

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

| Platform         | Default backend | Launch mechanism       |
|------------------|-----------------|------------------------|
| macOS            | `keychain`      | LaunchAgent            |
| Linux            | `file`          | systemd user unit      |
| Windows          | `file`          | (documented as manual) |
| Docker detected  | `file`          | (nothing auto)         |

Docker detection: presence of `/.dockerenv` or `container=docker` env.

### 5.5 What `init` never does

- Never writes private keys outside the chosen backend (no `keys.priv.json`
  is ever generated by `dwc init`; that file is a dev-only artifact of
  the older `keygen` default)
- Never overwrites an existing `keyring.json` / `signers.json` without
  `--force`
- Never emits a cloud-backend config (GCP-KMS, Vault, Azure-MHSM) —
  those keys are created in the respective cloud console; init prints a
  pointer to the backend module's docstring instead

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

Rendered with `string.Template` (stdlib). No Jinja dependency.

### 5.7 Tests

- `tests/test_init.py` (uses `tmp_path` + `click.testing` or
  `pexpect` for interactive flows)
  - happy path on macOS → keychain backend, launchagent written
  - `--backend file` produces `file` backend and no launchagent
  - `--yes` with missing required args exits nonzero with a
    specific error code (not a traceback)
  - refuses to overwrite existing `keyring.json` without `--force`
  - `keys.priv.json` is never created
  - Docker detection branch produces file backend, no launchagent
- CI runs init in a macOS runner and an Ubuntu runner, then runs
  `dwc doctor` — end-to-end smoke.

### 5.8 Risks

- **Keychain interactive prompt on first sign.** macOS will prompt
  "DwcStatus wants to use your confidential information stored in…" on
  first signer.sign(). This is correct behavior but will surprise a DIT
  mid-offload. Mitigation: `dwc init` performs a dummy sign so the
  prompt appears during setup, not at 2am. Document this in the post-
  init output.
- **LaunchAgent plist path.** Must use `$HOME` expansion, not `~`, or
  launchd rejects it silently. Tested via the Linux runner won't catch
  this — requires a macOS integration test.

### 5.9 Estimate

2 engineer-days.

---

## 6. Cross-cutting work

### 6.1 Packaging

Add extras in `pyproject.toml`:

```toml
[project.optional-dependencies]
web = []  # Pyodide consumes the base install
init = []  # stdlib only
```

No new runtime deps for doctor, init, ale-emitter — all stdlib.

### 6.2 Documentation restructure

Current single CLAUDE.md is engineering-facing and will remain so. Add
user-facing docs under `docs/`:

```
docs/
  quickstart.md              ← one page, calls dwc init
  integration/
    silverstack.md
    yoyotta.md
    shotput.md
  operations/
    doctor.md
    watch.md                 ← extract from CLAUDE.md
    signer-backends.md       ← extract + expand
  spec/
    v0.1/                    ← existing schemas + narrative
```

Link from README to `docs/quickstart.md`. Keep CLAUDE.md as-is for Claude
Code consumption.

### 6.3 Backward compatibility

None of these items changes the schema, the sidecar format, the event
canonicalization, or the validator stages. The ALE emitter is additive.
`dwc doctor` and `dwc init` are new subcommands. The menu-bar app is a
separate binary. The web validator consumes existing sidecars. All
Phase-02 work is pure addition; v0.1 sidecars produced before Phase 02
remain valid forever.

### 6.4 Release plan

Each item tagged independently:

- `v0.2.0` — dwc init + dwc doctor (CLI-internal, no schema impact)
- `v0.3.0` — ALE emitter (new external artifact, needs its own compat story)
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

## 7. Sequencing

```
5. dwc init       ─── unblocks everything below (clean-slate onboarding)
        │
        ▼
2. dwc doctor     ─── consumed by menu-bar app; also trivially useful alone
        │
        ▼
1. ALE emitter    ─── the real adoption lever; depends on neither
        │
        ▼
3. Menu-bar app   ─── polishes watch; depends on doctor + watch-state changes
        │
        ▼
4. Web validator  ─── highest marketing value, lowest internal urgency
```

Total: 13.5 engineer-days + codesigning / DNS setup overhead.

Parallelizable: 1 (ALE) and 4 (web) have no shared code paths with 2/3/5
and can be picked up by a second contributor once 5 lands.

### 7.1 Exit criteria per item

- **5 (init)**: a fresh Mac goes from `pipx install` to `dwc doctor`
  all-green in under 3 minutes without reading docs.
- **2 (doctor)**: all 12 checks pass on the reference corpus
  (`/Volumes/DWC_Shuttle-04/WAR/260115_SD084`); negative tests cover
  each check individually.
- **1 (ALE)**: Silverstack Lab (trial license), YoYotta ID (trial), and
  Resolve Studio all import `dwc-columns.ale` from the stub corpus and
  display the eight `DWC_*` columns correctly. Screenshots committed to
  the integration docs.
- **3 (menu-bar)**: notarized DMG downloads and launches cleanly on a
  fresh macOS install. Icon reflects state within 60s of a state change.
- **4 (web validator)**: stub sidecar validates to all-PASS in the
  hosted build. Network tab confirms zero uploads other than the
  initial static assets.

---

## 8. Open questions

1. **ALE column naming.** `DWC_Signed` vs `DWC.Signed` vs `Dwc_Signed` —
   Avid reserves `.` in some contexts. Going with underscore throughout;
   confirm before v0.3.0 by testing against a live Avid Media Composer.
2. **Menu-bar app bundle identifier.** Propose `com.the-dwc.sidecar.status`
   (matches the `ns.the-dwc.com` schema authority).
3. **Web validator domain.** `validate.the-dwc.com` vs
   `ns.the-dwc.com/validate` — subdomain is cleaner but needs a DNS
   record. Decide before §4 ships.
4. **Key expiry policy default.** 90 days (from `dwc init`) is a guess.
   Realistic DIT engagement is 8–20 weeks per show. A per-show key with
   a rotation ceremony at wrap might be the better default. Revisit
   after one real production.
5. **Linux/Windows menu-bar app.** Deferred, but Phase 03 candidate.
   Electron is out (too heavy); a GTK4 app or Rust+Tauri is in scope
   if a Linux-based DIT cart materializes.

---

## 9. What this plan deliberately does not include

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
