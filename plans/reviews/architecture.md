# Architecture Review

**Plan reviewed:** `plans/phase-02.md`
**Verdict:** Sound with concerns

## Findings

### [high] `.watch-state.json` schema change breaks restart-safe resumption on in-flight deployments

- **Plan section:** §3.7 — "Upstream change: `.watch-state.json` gets an `emitted` rolling log"
- **Issue:** `watch.py:_save_state()` (line 84–88) currently writes only `{"processed_mhl_sha256": [...], "savedAt": "..."}`. `_load_state()` (line 75–82) reads that shape silently discarding unknown keys. Adding an `emitted` list to `_save_state` is backward-compatible in one direction (old reader ignores new key), but the plan instructs the menu-bar app and `dwc doctor` (check 10) to read `.watch-state.json` and expect `emitted`. If a watcher instance is running on the old code (e.g. during a rolling upgrade under launchd), neither the menu-bar app nor `dwc doctor --quick` will find the key and must not crash. The plan does not specify a fallback for missing `emitted`. More critically, `dwc doctor` check 10 says "`.watch-state.json` in CWD, if present, is parseable and its `last_mhl_sha256` file still exists" — the field name `last_mhl_sha256` does not exist in the current schema (`processed_mhl_sha256` is the field name); the spec text and implementation are already divergent before any code is written.
- **Evidence:** `src/dwc_sidecar/watch.py` lines 75–88 — `_load_state` reads `processed_mhl_sha256`, `_save_state` writes `processed_mhl_sha256`. The plan §2.3 check 10 names `last_mhl_sha256`. No field by that name exists in the current codebase.
- **Suggested change:** Align §2.3 check 10 field name to `processed_mhl_sha256`. In `_load_state` / `_save_state`, add `emitted` with a default of `[]` so the schema evolves additively. Document the contract (both old and new watchers produce a readable file).

---

### [high] `validate_as_json()` entry point changes `validate.py`'s caller contract — all five existing call sites use `subprocess` and depend on the current stdout/exit-code interface

- **Plan section:** §4.5 — "Requires a small addition to `validate.py`: a `validate_as_json()` entry point that returns a dict instead of printing."
- **Issue:** The plan calls this "a pure refactor." It is not purely additive. The existing `validate.main()` (line 498) prints stage results to stdout and returns an integer exit code. The plan proposes to extract a `validate_as_json()` that returns a dict "instead of printing." All five existing callers (`watch._validate` at line 261, `mhl_walker.main` at line 267, `batch.main` at line 222, and the `dwc validate` CLI path) invoke the validator via `subprocess.run(["python3", "-m", "dwc_sidecar.validate", ...])` and interpret `returncode`. If the refactor changes the print behavior of `main()` (e.g., by having `main()` call `validate_as_json()` internally and re-render its output), any change to the printed stage format will break tests that assert on stdout. If `validate_as_json()` is purely additive and `main()` is left untouched, there is no regression risk, but the plan's wording "the existing CLI path calls it and prints" implies `main()` is rewritten to delegate to the new function — which risks subtle output format changes.
- **Evidence:** `src/dwc_sidecar/watch.py` line 261–263 (`subprocess.run(..., capture_output=True)`); `src/dwc_sidecar/mhl_walker.py` lines 267–272; `src/dwc_sidecar/batch.py` lines 222–226. All three parse `r.returncode` and `r.stdout`/`r.stderr` text.
- **Suggested change:** Implement `validate_as_json()` as a standalone entry point that calls the same stage functions as `main()` but collects results into a dict rather than printing. Leave `main()` calling the stage functions directly as it does now — do not have `main()` call `validate_as_json()`. This keeps the two paths independent and eliminates regression risk to the subprocess callers.

---

### [medium] `dwc ale-export --validate` runs the 9-stage validator per sidecar but invokes it via the existing subprocess path, which requires CWD-relative `keyring.json`

- **Plan section:** §1.5 — `dwc ale-export [...] [--validate]`
- **Issue:** `validate.py` resolves `KEYRING = Path("keyring.json")` and `REVOCATIONS = Path("revocations.json")` relative to CWD (line 22–24), not relative to the sidecar's own directory or a supplied `--base-dir`. When `ale-export` calls the validator on a sidecar that lives in a different directory (e.g., `sidecars/A001C001.omc.json` while CWD is the production root), Stage 4 will silently pass with zero events checked if `keyring.json` is absent from CWD, because `validate_signatures()` returns 0 when `not KEYRING.exists()` (line 153). The ALE output will then report `DWC_Signed=true` for a sidecar whose signatures were never verified.
- **Evidence:** `src/dwc_sidecar/validate.py` lines 22–24 (`KEYRING = Path("keyring.json")`), line 152–153 (`if not KEYRING.exists(): ... return 0`). The `--validate` flag behavior in the existing watchers (`watch._validate`, `mhl_walker.main`) sidesteps this because they pass `--base-dir` to the subprocess, but the plan for `ale-export` mentions `--base-dir` as an optional flag, not a required one.
- **Suggested change:** When `--validate` is specified without `--base-dir`, default `--base-dir` to the parent directory of the first sidecar input. Document in the CLI help that `keyring.json` must be in CWD or `--base-dir` must point to a directory containing it.

---

### [medium] `dwc watch --emit-ale` rewrite-on-every-emission can produce arbitrarily large ALE files and is not bounded by the same 100-entry cap used for the `emitted` list

- **Plan section:** §1.6 — ALE append semantics: "re-read, dedupe by `Name` column (latest row wins), rewrite"
- **Issue:** The `emitted` rolling log in `.watch-state.json` is capped at 100 entries (§3.7), but `dwc-columns.ale` has no stated cap. Over a multi-day shoot with thousands of clips, the ALE grows unbounded. At 900 sidecars/sec throughput (the `mhl-walk` rate quoted in CLAUDE.md), a full day could produce thousands of rows in a few seconds of catch-up. An unbounded rewrite of a growing ALE file on every emission is a linear-time operation that defeats the O(1) cost model of `mhl-walk`. The plan's rationale for ALE rewrite ("a clip can be re-signed") is sound, but the implementation will degrade as the day progresses.
- **Evidence:** `src/dwc_sidecar/watch.py` lines 186–205 — `_process()` is called for every stable MHL; the plan adds an ALE rewrite inside this hot path. No cap is specified for the ALE row count. §3.7 caps `emitted` at 100 but §1.6 makes no analogous statement for the ALE.
- **Suggested change:** Either cap the ALE at a configurable row count (e.g. last 500 clips, oldest rows dropped) or write a separate per-day ALE file named with the date, so no single file grows across a multi-day shoot. The ALE atomic-rewrite pattern is correct; the scope of the data structure is the concern.

---

### [medium] `dwc doctor` signer self-test (check 7) calls `signer.sign(b"\x00" * 32)` but the `Signer` base class contract only guarantees `sign(message: bytes) -> bytes` — there is no `verify` call against the keyring public key in the self-test, so a misconfigured signer that signs with the wrong key passes

- **Plan section:** §2.4 — Signer self-test
- **Issue:** The plan says "call `signer.sign(b"\x00" * 32)` and verify with the matching public key in the keyring." The verify step is correctly described in prose but requires that `doctor.py` also calls `Ed25519PublicKey.verify()` with the public key from `keyring.json`. This is a non-trivial extra step: it requires loading `keyring.json`, resolving the `kid`, decoding the public key, and calling `cryptography`'s verify. If this step is omitted or only the `sign()` call succeeds without verification, the check passes even when the private key in the backend is rotated but the keyring still holds the old public key — which is exactly the scenario the check is meant to catch. The plan describes the intent correctly but the implementation path requires explicit attention because `signer.public_key_bytes()` is available on all backends (it is defined in `base.Signer`) and could be used to verify against itself rather than against the independently-held keyring public key, defeating the check's purpose.
- **Evidence:** `src/dwc_sidecar/signers/base.py` — `public_key_bytes()` is an abstract method on every signer. If `doctor` verifies the signature using `signer.public_key_bytes()` rather than the keyring's copy of the public key, the check does not catch key rotation mismatches.
- **Suggested change:** Make the plan explicit: verify using `load_pubkey_b64(keyring[kid]["publicKey"])` (already implemented in `canonical.py` line 108), not `signer.public_key_bytes()`. This is the only form of the check that catches backend/keyring divergence.

---

### [medium] `dwc init` on Linux emits a systemd user unit template but the plan gives no path or naming convention; systemd user unit paths differ by distribution and are not resolvable via stdlib alone

- **Plan section:** §5.4 — "Linux: systemd user unit" as launch mechanism
- **Issue:** The plan says `dwc init` on Linux will set up a systemd user unit. Systemd user unit files conventionally live at `~/.config/systemd/user/<name>.service`. However, `systemctl --user daemon-reload` and `systemctl --user enable` must be called for the unit to take effect, and those require the systemd user session to be active (not available in all CI environments or Docker). The plan explicitly handles macOS `launchctl load`, Docker `/.dockerenv` detection, and Windows as manual, but Linux is described as "systemd user unit" without specifying how `dwc init` activates it or whether it emits a warning when `systemctl` is absent. On non-systemd Linux systems (e.g., Alpine with OpenRC, the Docker base images most common in CI), the unit file is written but silently non-functional. This is a product-experience gap, not a code-correctness bug, but it creates a false all-green from `dwc doctor` on a system where the watcher never starts at login.
- **Evidence:** §5.4 table lists "systemd user unit" as the Linux launch mechanism. §5.7 says "CI runs init in... an Ubuntu runner" (Ubuntu uses systemd) but does not specify whether `systemctl --user` is invoked. No probe of systemd availability is described.
- **Suggested change:** After writing the unit file, run `systemctl --user daemon-reload && systemctl --user enable dwc-sidecar-watch.service` if `systemctl` is found on PATH and print a clear warning otherwise. Add `DBUS_SESSION_BUS_ADDRESS` to the doctor check for Linux to detect headless sessions where user units cannot activate.

---

### [low] `dwc ale-export` derives `Tape` column from "A-Cam-reel regex in `mhl_walker.py`" — but no such regex exists in `mhl_walker.py` today

- **Plan section:** §1.5 — "`--tape` overrides `Tape` column derivation (default: parsed from OMC `clipName` via the A-Cam-reel regex in `mhl_walker.py`)"
- **Issue:** `src/dwc_sidecar/mhl_walker.py` has no regex for parsing an A-cam reel identifier from `clipName`. The plan assumes this logic already exists and can be reused. It does not. The ALE emitter will need to implement it from scratch (or inline the regex in `ale_emitter.py`), and the plan's cross-reference is misleading for implementers.
- **Evidence:** `src/dwc_sidecar/mhl_walker.py` — full file read; no regex, no `clipName` parsing. The `name` field in a generated sidecar is `clip_abs.stem` (line 79).
- **Suggested change:** Remove the reference to an existing regex in `mhl_walker.py`. Define the A-cam-reel regex in `ale_emitter.py` (e.g., `^([A-Z]\d{3})` to extract the reel prefix `A001` from `A001C001_260115_R1AB`) and document the convention in the spec.

---

### [low] Pyodide `blake3` fallback claim is described as "pure-Python fallback ships" — the `blake3` package does not ship a pure-Python fallback; it is a C extension

- **Plan section:** §4.6 — "`blake3` — pure-Python fallback ships, 10× slower but fine for single sidecars"
- **Issue:** The `blake3` PyPI package (`blake3>=0.4`, declared in `pyproject.toml`) is a Rust-compiled C extension. There is no pure-Python fallback in the package. Pyodide ships a curated set of packages compiled to WASM; whether `blake3` is in that set requires verification. If it is not, importing `dwc_sidecar.canonical` inside Pyodide will fail at the `import blake3` line (line 12 of `canonical.py`), which would break the web validator entirely for sidecars that use `blake3` hashes, not just make them slower.
- **Evidence:** `src/dwc_sidecar/canonical.py` line 12: `import blake3`. `pyproject.toml` line 32: `"blake3>=0.4"`. The claim of a "pure-Python fallback" is UNVERIFIED — no fallback import guard exists in `canonical.py` (checked full file; the import is unconditional).
- **Suggested change:** Before shipping the web validator, verify whether Pyodide's package index includes `blake3` compiled to WASM. If not, add a try/except around the `blake3` import in `canonical.py` with a fallback that raises a clear `ImportError` when `blake3` hashes are requested, and gate the web validator's Stage 6 on whichever algs are available. Alternatively, use `hashlib` for WASM contexts where `blake3` is unavailable, since the web validator only verifies existing sidecars and the alg is declared in the artifact record.

---

### [low] `dwc doctor` check 9 reuses `--check-hosted` logic from `validate.py`, which shells out to `curl` — this creates a subprocess dependency in a function whose `--quick` variant is supposed to be network-free and <200ms

- **Plan section:** §2.3 check 9, §2.5 `--quick`
- **Issue:** `validate.py:check_hosted_schemas()` (lines 463–495) shells out to `curl` for each schema. The plan says `--quick` skips this check, which is correct. However, the hosted-schema check is bundled in the same module as the other doctor checks and the implementation note says it "reuses `--check-hosted` logic." If `doctor.py` imports `validate.check_hosted_schemas` at module level, the `curl` subprocess import chain is present regardless of `--quick`. This is a coupling concern, not a correctness issue, but it means `doctor.py`'s module import will drag in `validate.py`'s full import graph (including `jsonschema`, `cryptography`, etc.) on every `dwc doctor --quick` invocation — potentially adding import time that violates the <200ms budget.
- **Evidence:** `src/dwc_sidecar/validate.py` lines 463–495 — `check_hosted_schemas()` uses `subprocess.run(["curl", ...])` internally. The import of `hashlib` and `subprocess` is deferred inside the function body (line 468), which is good. However `validate.py` imports `jsonschema`, `cryptography`, and all stage functions at module level (lines 11–18).
- **Suggested change:** In `doctor.py`, import `check_hosted_schemas` lazily (inside the check-9 function body, not at module top-level) to avoid loading `validate.py`'s full dependency graph during `--quick` runs.

---

## Areas reviewed with no concerns

- **OMC envelope integrity:** All five new features (ALE emitter, doctor, init, menu-bar app, web validator) are purely additive. None proposes adding top-level fields to an OMC Asset, and none modifies `customData` schemas, so Stage 1 and Stage 2 are unaffected.
- **Schema version stability:** §6.3 explicitly states no schema changes in Phase 02; this is consistent with the non-goal declaration and with the convention in CLAUDE.md §4.
- **Signer backend interface:** `dwc doctor` check 7 self-test calls `signer.sign()`, which is the correct method on the `Signer` ABC. All backends implement it. The interface is compatible across all seven backend types.
- **ALE as additive output artifact:** The ALE emitter produces a new file; it reads from existing sidecars but does not modify them. No validator stage reads ALE files, so emitter bugs cannot corrupt sidecars or cause Stage 1–9 regressions.
- **`dwc init` not writing `keys.priv.json`:** The plan correctly notes that `dwc init` should never generate `keys.priv.json`. This is consistent with the codebase convention where only `sign_example.py` (demo path) and `keygen --backend local` (explicit opt-in) produce that file.
- **Pyodide: signer imports are lazy:** The plan correctly notes that signer backends are lazy-imported in `signers/__init__.py` (line 75–88 of that file — each `from .foo import Foo` is inside an `if t == ...` branch). The web validator will not trigger PKCS#11/GCP/Azure import errors at load time.
- **Atomic ALE rewrite:** The plan correctly specifies `os.replace` semantics (write to `.tmp` then rename), which is correct for POSIX atomicity. This is consistent with how the codebase handles state files elsewhere.
- **`dwc doctor` JSON output for menu-bar consumption:** The `--json` flag returning `{"status": "fail", "checks": [...]}` is a clean interface for the Swift `DoctorReport.swift` decoder. The plan correctly keeps the menu-bar app as a read-only consumer (no signing, no config writes).
- **macOS MenuBarExtra availability:** The plan restricts the menu-bar app to macOS 13+ (`MenuBarExtra` was introduced in macOS 13). This is correct and the restriction is explicitly documented.
- **`dwc init` template rendering via `string.Template`:** Using stdlib `string.Template` rather than Jinja adds no dependency and is appropriate for the simple `{{kid}}` substitution described.
- **MHL path resolution invariant:** None of the proposed features touches MHL path resolution logic. The convention (paths relative to MHL's own directory) is preserved.

## Unverified claims

- **§4.6: "`blake3` — pure-Python fallback ships"** — Searched `src/dwc_sidecar/canonical.py` for a try/except around the `blake3` import; none exists. Searched `pyproject.toml` for any conditional blake3 dependency; none exists. Whether Pyodide's package repository includes a WASM-compiled `blake3` wheel could not be verified from local files. The claim as written is almost certainly incorrect for the `blake3` PyPI package, which is a Rust extension. Marked as a finding above.
- **§1.3: "Silverstack/YoYotta accept LF too but CRLF is safest"** — No ALE specification document was found locally under `docs/`, `vendor/`, or `third_party/`. The claim about CRLF acceptance cannot be verified from the local codebase. This is a product claim, not a code claim, and is acceptable as a plan-level assertion pending real-app testing (covered by §1.9 risk mitigation).
- **§3.4: "SwiftUI `MenuBarExtra` is macOS 13+"** — Cannot verify the exact macOS version requirement from local docs; this is a first-party Apple API claim. The claim is consistent with public Apple documentation as of the knowledge cutoff but is noted as UNVERIFIED from local files.
