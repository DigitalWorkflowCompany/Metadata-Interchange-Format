# Testability and Dependencies Review

**Plan reviewed:** plans/phase-02.md
**Verdict:** Testable with minor changes

---

## Findings

### [high] `dwc doctor` check functions receive no injected dependencies — hard to unit-test individual checks

- **Plan section:** §2.3, §2.6
- **Concern:** The plan specifies 12 checks but does not say how they are structured so they can be exercised independently. If each check function hard-imports `os.getcwd()`, `Path("keyring.json")`, `Path(".watch-state.json")`, `Path("*.omc.json")`, and the `DWC_SIGNERS` env var, then testing one check in a `tmp_path` fixture forces the test to reconstruct an entire realistic CWD, and tests for checks 1–12 become full integration tests rather than unit tests.
- **Why:** `validate.py` (the existing precedent) resolves `KEYRING = Path("keyring.json")` and `REVOCATIONS = Path("revocations.json")` as module-level constants against `os.getcwd()` at call time — not against an injected path. `watch.py` does the same for `STATE = Path(".watch-state.json")`. If `doctor.py` follows the same pattern, there is no seam to feed a synthetic filesystem to an individual check function without `monkeypatch.chdir`.
- **Evidence:** `src/dwc_sidecar/validate.py` lines 25–26 (`KEYRING = Path("keyring.json")`, `REVOCATIONS = Path("revocations.json")`); `src/dwc_sidecar/watch.py` line 29 (`STATE = Path(".watch-state.json")`).
- **Suggested change:** Define each check as a function that accepts explicit path arguments: `check_keyring(keyring_path: Path, sidecars: list[Path], now: datetime) -> CheckResult`. The `dwc doctor` CLI entry point passes `Path.cwd() / "keyring.json"` etc. This mirrors the existing `base_dir` injection pattern in `validate.py`'s stage functions and allows tests like `test_check_keyring_missing(tmp_path)` without monkeypatching the working directory.

---

### [high] Signer self-test (doctor check 7) has no mock seam — requires live backend credentials to test

- **Plan section:** §2.4, §2.6 ("signer self-test uses the local backend against a generated key")
- **Concern:** The plan notes that check 7 calls `signer.sign(b"\x00" * 32)` with a 500ms timeout. Testing a FAIL path (e.g., a Vault backend whose token has expired) requires either a live Vault instance or a mock. The plan mentions testing "with the local backend against a generated key" for the PASS case, but does not describe how the FAIL path (backend timeout, credential failure) is tested.
- **Why:** `get_signer()` in `src/dwc_sidecar/signers/__init__.py` returns a concrete backend type by inspecting the signers.json config. There is no way to inject a pre-built `Signer` instance into the doctor without modifying its interface. If `doctor.py` calls `get_signer(kid)` directly, any test covering a failing backend must control the DWC_SIGNERS env var and provide a config that points to a backend class that is itself stubbed — two layers of indirection with no current harness.
- **Evidence:** `src/dwc_sidecar/signers/__init__.py` lines 95–106 (`get_signer` function), `tests/conftest.py` (shows existing pattern: mock the backend's signing callable, not the entire `get_signer` call).
- **Suggested change:** Accept an optional `signer_factory: Callable[[str], Signer] | None` parameter in the doctor's check-7 function (defaulting to `get_signer`). Tests pass a lambda that returns a `Signer` subclass with a controlled `sign()` method — identical to the pattern already established in `tests/conftest.py`. The timeout path can be tested by passing a signer whose `sign()` sleeps past the budget.

---

### [high] `validate.py` has no `validate_as_json()` entry point — web validator requires one, and there are no existing tests for the JSON output contract

- **Plan section:** §4.5, §4.7
- **Concern:** The plan requires adding `validate_as_json()` to `validate.py`, which returns a dict instead of printing. This is a new public surface with an implied schema (nine stages, counts, per-stage status). There is currently no test for the return structure, and no existing `tests/test_validate_as_json.py`. The plan names that test file but it does not exist yet.
- **Why:** `validate.py`'s `main()` currently prints directly and returns an integer exit code. Every stage function (`validate_omc`, `validate_chain_integrity`, etc.) prints to stdout and returns an error count. A `validate_as_json()` wrapper must capture or bypass all those `print()` calls. If implemented by redirecting stdout (`io.StringIO`) rather than refactoring the stage functions, the test cannot assert on individual stage results without parsing freeform output — brittle.
- **Evidence:** `src/dwc_sidecar/validate.py` lines 77–88 (`validate_omc` prints to stdout), lines 498–543 (`main` function drives all stages, returns int). No `tests/test_validate_as_json.py` found.
- **Suggested change:** Refactor each stage function to return a structured result object (or dict with `status`, `errors`, `detail`) instead of printing directly. A thin `validate_as_json()` assembles these into the dict the web validator consumes. The CLI `main()` calls `validate_as_json()` and formats/prints the result. This separates output from logic and makes all nine stages individually testable without stdout capture. The plan notes this is "a pure refactor — the existing CLI path calls it and prints" — that framing is correct, but the plan does not commit to the refactored internal shape that makes it testable.

---

### [medium] `dwc watch --emit-ale` integrates ALE I/O directly in the watcher loop — no seam for unit-testing the append/rewrite path

- **Plan section:** §1.6
- **Concern:** The plan says the watcher appends to `<watch-root>/dwc-columns.ale` after each sidecar emission using read-dedupe-rewrite semantics with an atomic `os.replace`. If this logic is inlined in `Watcher._process()` or a private method that also calls `build_sidecar_from_mhl_entry`, the ALE update logic cannot be tested independently of a fully wired `Watcher`.
- **Why:** `watch.py`'s `_process()` method (lines 147–205) already mixes collision detection, sidecar writing, validation subprocess launch, and quarantine into a single function. Adding ALE rewrite into the same function makes it harder to test the dedup-by-Name logic and the "existing ALE vs. no ALE" branches in isolation.
- **Evidence:** `src/dwc_sidecar/watch.py` lines 147–205.
- **Suggested change:** Put the ALE update logic in `ale_emitter.py` as a standalone `update_ale(ale_path: Path, row: dict) -> None` function that reads, dedupes, and rewrites. The watcher calls it. Tests for this function can exercise all branch conditions (no file, one row, duplicate name, unicode) without instantiating a `Watcher` at all. The plan already names `ale_emitter.py` as the new module — this just confirms its scope should include the update path, not only the serialisation path.

---

### [medium] `dwc init` interactive flow has no clock or platform injection — tests for platform-branch logic require monkeypatching `sys.platform`

- **Plan section:** §5.4, §5.7
- **Concern:** The platform defaults table (macOS → keychain → LaunchAgent; Linux → file → systemd) implies that `init.py` branches on `sys.platform`. Testing the Linux branch on a macOS CI runner (or the other way around) requires monkeypatching `sys.platform`. The plan mentions `click.testing` or `pexpect` for interactive flows but does not name a platform-injection seam.
- **Why:** The existing `KeychainSigner` already embeds `if sys.platform != "darwin": raise RuntimeError(...)` at line 47 of `src/dwc_sidecar/signers/keychain.py` — a direct `sys.platform` check with no injection point. If `init.py` does the same, tests for the Linux-default branch cannot run on macOS CI without patching the interpreter's platform string.
- **Evidence:** `src/dwc_sidecar/signers/keychain.py` line 47.
- **Suggested change:** Extract the platform detection into a small function `_detect_platform() -> str` that returns a canonical string (`"macos"`, `"linux"`, `"windows"`, `"docker"`). Tests pass a synthetic value via a parameter to `init_interactive(platform: str = _detect_platform(), ...)`. This is the minimum seam that lets all four platform branches be tested on any host.

---

### [medium] `dwc init` uses wall clock (`datetime.now`) for key validity window — non-deterministic in tests

- **Plan section:** §5.3 ("Keyring entry valid for [90] days")
- **Concern:** `keygen.py` (the existing precedent) calls `datetime.now(timezone.utc)` to compute `validFrom` and `validUntil`. If `init.py` calls the same function, any test that asserts the content of `keyring.json` will fail intermittently unless the clock is frozen or the assertion is made against a range rather than an exact value.
- **Why:** `src/dwc_sidecar/keygen.py` function `_iso_days()` at lines 31–33 calls `datetime.now(timezone.utc)` directly.
- **Evidence:** `src/dwc_sidecar/keygen.py` lines 31–33.
- **Suggested change:** Accept a `now: datetime | None = None` parameter in any function that computes a validity window. If `None`, default to `datetime.now(timezone.utc)`. Tests pass a fixed datetime. This is a two-line change in `keygen.py` and should be replicated in `init.py` when it generates keys.

---

### [medium] Doctor check 9 (hosted-schema drift) uses `subprocess.run(["curl", ...])` — no injection point for network calls in tests

- **Plan section:** §2.3 check 9, §2.6 ("--quick does not touch the network (use `responses` library to assert no HTTP)")
- **Concern:** The existing `check_hosted_schemas()` in `validate.py` (lines 463–495) calls `subprocess.run(["curl", ...])`. The plan says `--quick` skips this check. But the plan's test for `--quick` says "use `responses` library to assert no HTTP" — `responses` is a `requests` library interceptor and does not intercept `subprocess.run`. There is also a `responses` library dependency not declared in `pyproject.toml`.
- **Why:** `src/dwc_sidecar/validate.py` lines 476–484 use `subprocess.run(["curl", ...])`. The `responses` library intercepts Python-level HTTP (via `requests` or `urllib`) but cannot intercept a subprocess exec of `curl`. The test described in the plan would not actually prevent a real network call.
- **Evidence:** `src/dwc_sidecar/validate.py` lines 476–484; `pyproject.toml` does not list `responses` in any extras.
- **Suggested change:** In `doctor.py`, wrap the HTTP fetch behind an injectable function: `fetch_url: Callable[[str], bytes] = _default_fetch`. The `--quick` path sets `fetch_url=None` (skipped). Tests for the FAIL case inject a callable that returns synthetic bytes. This removes the need for `responses` and the `curl` subprocess entirely. If the existing `check_hosted_schemas` in `validate.py` is reused by doctor, that function also needs the same refactor.

---

### [medium] `Watcher._validate()` uses `subprocess.run(["python3", "-m", "dwc_sidecar.validate", ...])` — subprocess call makes watch-folder tests slow and non-deterministic

- **Plan section:** §1.8 ("Integration test: run `dwc watch` over a fixture, assert `dwc-columns.ale` matches a golden")
- **Concern:** The integration test described in §1.8 will trigger `Watcher._validate()`, which shells out to a new Python process. This is already a concern in the existing code, but the plan's integration test for ALE emission would inherit it. There is no mock path for validation in `Watcher`.
- **Why:** `src/dwc_sidecar/watch.py` lines 260–266: `subprocess.run(["python3", "-m", "dwc_sidecar.validate", ...])`. Any integration test that allows `validate_each=True` must either allow the subprocess (slow, environment-dependent) or inject a validator callable.
- **Evidence:** `src/dwc_sidecar/watch.py` lines 260–266.
- **Suggested change:** The `Watcher.__init__` could accept a `validator: Callable[[Path], tuple[bool, str]] | None = None` parameter. In production, `None` triggers the subprocess path. In tests, pass a lambda that returns `(True, "")`. The plan's integration test should use `--no-validate` equivalent at the API level. This is not new work required by Phase 02 — but the ALE integration test should be written to avoid this subprocess so it stays in-process and fast.

---

### [low] macOS menu-bar Swift JSON decoders have no shared fixture source — Python and Swift test fixtures can drift

- **Plan section:** §3.9
- **Concern:** The plan says Swift unit tests use "doctor and watch-state fixtures copied from the Python tests." Copy-pasting fixtures between two language ecosystems (Python `tests/` and Swift `macos-statusbar/`) means that if the `dwc doctor --json` schema or `.watch-state.json` format changes, one set of fixtures can drift silently.
- **Why:** The `emitted` field being added to `.watch-state.json` (§3.7) is a structural change that must be reflected in both the Python test for `watch.py` and the Swift `WatchState.swift` decoder tests. There is no described mechanism to keep them in sync.
- **Suggested change:** Generate the Swift fixture files from the Python tests (e.g., a pytest fixture that writes JSON to `macos-statusbar/Tests/Fixtures/` as a side effect, or a CI step that diffs them). At minimum, document in the Swift test directory that fixture files are sourced from `tests/fixtures/` and should not be edited independently.

---

### [low] `ale_emitter.py` column `DWC_LastVerified` is a wall-clock timestamp — non-deterministic in golden-file comparison

- **Plan section:** §1.4, §1.8 ("round-trips a fixture sidecar → ALE → parse-back → asserts all 8 DWC_* columns match source")
- **Concern:** `DWC_LastVerified` is "ISO-8601 UTC of the validation run that produced the row." If the emitter calls `datetime.now()` internally, the ALE output changes on every test run and cannot be compared to a golden file.
- **Why:** The plan says the integration test asserts `dwc-columns.ale` "matches a golden" — a golden comparison fails if any timestamp field differs.
- **Suggested change:** Accept a `now: datetime | None = None` parameter in `ale_emitter.py`'s main emission function (same pattern as the keygen fix above). Tests pass a fixed datetime. The integration test golden file uses the same fixed datetime.

---

### [low] Web validator's `pytest-pyodide` matrix is mentioned but not planned for setup

- **Plan section:** §4.6 ("Before shipping, add a `pytest` matrix that runs the tests under Pyodide via `pytest-pyodide`")
- **Concern:** `pytest-pyodide` is not in `pyproject.toml` and requires a Chromium or Node binary available at test time. The plan mentions it as a future addition but gives no detail on how it fits into the CI matrix or how the existing test suite avoids syscall-incompatible calls when running under Pyodide.
- **Why:** `pyproject.toml` only lists `pytest>=7` in the `dev` extra. `pytest-pyodide` is UNVERIFIED as a dependency and requires separate install.
- **Suggested change:** Before the web validator ships, add a `web` test extra to `pyproject.toml` and a separate CI job (`pytest --run-in-pyodide`) that gates on the `tests/test_validate_as_json.py` suite only. This scopes the Pyodide requirement to one job and one test file.

---

## Existing test infrastructure found

- **Framework:** `pytest>=7`, declared in `pyproject.toml` under `[project.optional-dependencies] dev`.
- **Only confirmed existing test files:**
  - `tests/conftest.py` — shared fixtures: `ed25519_keypair` (generates a real Ed25519 keypair), `verify_signature` (cryptographic verification callable). Both are injected via pytest fixtures, not global state.
  - `tests/test_c4_interop.py` — parametrized cross-verification of `_C4` against the reference Avalanche-io algorithm. Uses `@pytest.mark.parametrize` with fixed byte vectors.
- **No test config file found** (`pytest.ini`, `setup.cfg [tool:pytest]`, `pyproject.toml [tool.pytest]` — none present). Pytest runs with defaults.
- **No existing tests for:** `validate.py`, `watch.py`, `mhl_walker.py`, `bootstrap.py`, `batch.py`, `keygen.py`, `doctor.py` (planned), `init.py` (planned), `ale_emitter.py` (planned).
- **Signer tests:** Git log entry "Add mock-based unit tests for all signer backends (33 tests)" appears in recent commits, but no corresponding test files were found on disk at review time. Either they are staged but not committed to a tracked path, or they exist under a filename not explored. This is noted — the mock pattern in `conftest.py` is clearly designed to support them.

---

## Aspects checked with no concerns

- **`ale_emitter.py` core logic is pure.** ALE serialisation is a string transformation over a dict of values extracted from a parsed sidecar JSON. No external dependencies beyond the sidecar document itself. Unit-testable with no mocking beyond the `now` timestamp injection noted above.
- **`dwc init` template rendering uses `string.Template` (stdlib).** No Jinja dependency is introduced. Template rendering is trivially testable.
- **`dwc init --yes` non-interactive path.** The plan explicitly calls for a `--yes` flag with all args supplied, which is the path that `click.testing.CliRunner` or a plain `subprocess.run` can exercise in CI without `pexpect`. No concern.
- **Signer abstraction (`Signer` base class) is already a clean seam.** `src/dwc_sidecar/signers/base.py` defines `sign(bytes) -> bytes` and `public_key_bytes() -> bytes` as the only interface. Any doctor self-test and any ALE emitter that needs a signer can receive a stub `Signer` subclass in tests.
- **ALE dedup logic is deterministic.** The plan's dedup key ("Name" column = clip name) and "latest row wins" rule are pure in-memory operations on a list of dicts. Testable without any I/O once `update_ale` is extracted as a standalone function (see finding above).
- **`os.replace` atomicity in ALE rewrite.** The plan uses the correct atomic pattern. This is not a testability concern — `os.replace` is synchronous and its outcome is observable by reading the file.
- **Web validator is stateless and has no server.** The Pyodide approach means there is no server-side state to mock. The `validate_as_json()` function, once extracted, can be tested entirely in-process from Python without Pyodide.
- **No new signer backends in Phase 02.** The plan explicitly defers new backends. The existing signer fixture pattern in `conftest.py` does not need to be extended.

---

## Unverified claims

- **`pytest-pyodide` capability (§4.6):** The plan says this matrix can be added before the web validator ships. Whether `pytest-pyodide` supports the full `dwc_sidecar` import surface (specifically `rfc8785`, `cryptography`, `xxhash`) under Pyodide's WASM environment is not verified. The plan itself acknowledges `blake3` needs a pure-Python fallback.
- **`responses` library assertion (§2.6):** The plan says to "use `responses` library to assert no HTTP" for the `--quick` flag. As noted in the finding above, `responses` does not intercept subprocess-based HTTP (`curl`). Whether the intent is to refactor away from `curl` first or to use a different assertion mechanism is unspecified.
- **33 signer unit tests from git log:** Git history references these tests but they were not found as files on disk at review time. It is possible the files exist under an unexplored path or are in a branch not merged to `main`. The conftest fixtures suggest they were designed to exist. This does not affect Phase 02 planning but is noted.
- **SwiftUI preview tests as smoke tests (§3.9):** The plan says "One UI smoke test per state (green/amber/red/grey) via SwiftUI previews committed to the repo." SwiftUI previews are not executable unit tests — they require Xcode Preview rendering, which is not scriptable from CI without a macOS runner and the Xcode Preview daemon. Whether these count as automated tests in CI is unverified.
