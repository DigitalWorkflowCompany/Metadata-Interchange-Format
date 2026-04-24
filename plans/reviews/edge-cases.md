# Edge Cases and Error Handling Review

**Plan reviewed:** `plans/phase-02.md`
**Verdict:** Significant gaps

---

## Findings

### [critical] ALE atomic rewrite is not crash-safe when `.tmp` file already exists from a prior crashed write

- **Plan section:** §1.6 — `dwc watch --emit-ale` append semantics
- **Trigger:** A previous watcher process crashed after writing `.dwc-columns.ale.tmp` but before calling `os.replace`. On the next run the watcher reads the existing `dwc-columns.ale`, dedupes, writes to `.dwc-columns.ale.tmp` — but the file already exists from the crash and may be a partial write. `os.replace` is still atomic on POSIX so the replace itself is safe, but the plan says "re-read, dedupe by `Name` column, rewrite": if the re-read step reads the stale `.tmp` file instead of the real ALE (e.g. a stale file handle, or a future refactor confusing the two paths), corrupt state can propagate silently.
- **Expected behavior:** Stale `.tmp` from a prior crash should be detected and removed or unconditionally overwritten without reading it as input.
- **Actual behavior per plan:** Undefined — the plan specifies the happy-path only; stale `.tmp` handling is not mentioned.
- **User-visible consequence:** If `.tmp` is ever read as source data, dedupe merges corrupt rows into the ALE silently; Silverstack/YoYotta display garbled clip metadata.
- **Suggested handling:** Before the re-read step, delete any pre-existing `.tmp` file. Since `os.replace` is used for the final rename, the stale `.tmp` is only a problem if the read-phase opens it by mistake — make the read always target the production filename, never the `.tmp`.

---

### [critical] `dwc watch --emit-ale` rewrite reads then rewrites the ALE on every clip: O(n²) and data-loss window

- **Plan section:** §1.6 — "re-read, dedupe by `Name` column (latest row wins), rewrite"
- **Trigger:** A day with 400 clips. Each new clip triggers: read the ALE (now 399 rows), parse, dedupe, write 400 rows. The 400th clip triggers a read of a 4.8 KB file — fine. But the window between the read and the `os.replace` is a data-loss window: if the watcher process is killed exactly here, the newly written `.tmp` has the full 400 rows but `os.replace` never runs, and the original `dwc-columns.ale` remains at 399 rows. The plan notes "log WARN and continue" on ALE I/O failure, but not on process kill. This is an inherent limitation of the design — the plan is silent on it.
- **Expected behavior:** Either acknowledged as an accepted trade-off (sidecar emission is never blocked; ALE can be one row behind) or mitigated with a journal approach.
- **Actual behavior per plan:** Undefined — plan says the rewrite is atomic but does not note that the last row can be lost on kill.
- **User-visible consequence:** After a crash, the ALE is missing the last-written clip row. DIT sees a clip as unsigned that is actually signed. Severity is bounded to one row per crash, but a production cart that hard-reboots mid-offload will have reproducible one-row drift.
- **Suggested handling:** Document the trade-off explicitly. The sidecar itself is the source of truth; the ALE is a view. A note in §1.9 Risks would suffice.

---

### [high] ALE emitter: tab characters in a DWC column value corrupt the entire row

- **Plan section:** §1.3 — "ALE is tab-separated"; §1.8 tests — "tab delimiter survives values containing spaces"
- **Trigger:** `DWC_SidecarPath` or `DWC_Kid` value contains a literal tab character (e.g., a kid or path that was manually set with a tab). More realistically: `clipName` derived from OMC that contains a tab (e.g., a filename with a tab on a filesystem that allows it, or a bug in upstream OMC generation).
- **Expected behavior:** Tab in a column value must be escaped or rejected; the row must remain parseable.
- **Actual behavior per plan:** Undefined. The plan tests spaces but not tabs. Since ALE is tab-delimited, a tab in a value shifts all subsequent columns one position right, silently breaking the row.
- **User-visible consequence:** Silverstack/YoYotta/Resolve parse the shifted columns and display wrong data in every `DWC_*` column for that row. No error is raised.
- **Suggested handling:** `ale_emitter.py` must sanitize column values: replace or strip tab/CR/LF characters before writing. Add a test for tab-in-value.

---

### [high] ALE emitter: CRLF in a column value (e.g., multi-line clip description from OMC) breaks row structure

- **Plan section:** §1.3 — "CRLF line endings"; §1.8
- **Trigger:** An OMC `description` or any field used to derive a column value contains an embedded `\r\n` or `\n`. Descriptions are free text in OMC; production data from Resolve exports has been observed with embedded newlines in `description` fields.
- **Expected behavior:** Column values are stripped of embedded line endings before emission.
- **Actual behavior per plan:** Undefined.
- **User-visible consequence:** ALE row is split in two; the second half appears as a malformed incomplete row. Parser errors in DIT tools vary: Silverstack may silently drop the row, YoYotta may import it as a separate phantom clip.

---

### [high] `dwc watch --emit-ale` dedup key collides on clips with identical `Name` but different roll/reel

- **Plan section:** §1.6 — "dedupe by `Name` column (latest row wins)"
- **Trigger:** Two sidecars for clips that share a `Name` value but are from different reels or cameras (e.g., `A001C001` appears in both reel A and reel B if the DIT starts the roll counter from C001 twice, which is an Arri convention for second-unit shoots). The dedup keeps only the latest row.
- **Expected behavior:** `Name` is not a globally unique key; dedup should use `DWC_SidecarPath` (which is path-relative and therefore unique per sidecar file) as the primary dedup key, falling back to `Name` only if paths are identical.
- **Actual behavior per plan:** The second reel's clip silently overwrites the first reel's row in the ALE. The DIT sees one clip signed and the other missing.
- **User-visible consequence:** Silent data loss in the ALE for the first-seen clip. Because the plan says "latest row wins," the overwrite is by design — but the design is wrong for multi-roll shoots.
- **Suggested handling:** Dedup on `DWC_SidecarPath`, not on `Name`. `DWC_SidecarPath` is already in the schema and is path-unique.

---

### [high] `dwc doctor` check 10: `.watch-state.json`'s `last_mhl_sha256` file field is not present in the current schema

- **Plan section:** §2.3, check 10 — "`last_mhl_sha256` file still exists"
- **Trigger:** Check 10 references a `last_mhl_sha256` field in `.watch-state.json` and asserts the referenced file still exists. The actual `.watch-state.json` written by `watch.py` (`_save_state`) stores `processed_mhl_sha256` (a list of all processed MHL sha256 hashes) and `savedAt`, with no concept of a "last MHL file path." The check as written cannot be implemented against the current schema without a schema change.
- **Expected behavior:** Either the check is defined against the current schema, or §3.7 is a prerequisite and the new `emitted` field must be shipped before doctor check 10 can be implemented.
- **Actual behavior per plan:** Plan is internally inconsistent. Check 10 references a field that does not exist yet and whose addition is described separately in §3.7 (the menu-bar emitted log). This creates a sequencing dependency (doctor must ship after the watch-state change) that contradicts §7, which shows doctor shipping before the menu-bar app.
- **User-visible consequence:** Doctor check 10 either always passes vacuously (the field is absent → no stale state detected) or crashes on a `KeyError` depending on implementation. Either way the check is not useful until §3.7 lands.
- **Suggested handling:** Clearly state in §2.3 that check 10 depends on the §3.7 watch-state schema change, and document it as a no-op pass until that change lands. Or redefine check 10 against the existing `processed_mhl_sha256` list (e.g., "list is parseable and non-empty").

---

### [high] `dwc doctor` signer self-test: 500ms timeout is applied per-kid but the plan does not specify what happens if `sign()` hangs without raising

- **Plan section:** §2.4 — "Total round-trip budget: 500ms per kid; timeout → FAIL"
- **Trigger:** A cloud backend (GCP-KMS, Vault, Azure-MHSM) that hangs (TCP connect established but server stops responding) will block the `sign()` call indefinitely. The existing signer implementations (`gcp_kms.py`, `vault.py`, `azure_mhsm.py`) use their respective SDK's internal timeouts — Vault uses `urllib` with `timeout=15`, GCP-KMS uses the gRPC client default which is typically 60s. None of the signers expose a way to cap the call at 500ms from the caller side.
- **Expected behavior:** `dwc doctor` enforces its 500ms budget by running the self-test in a `threading.Timer` or via `concurrent.futures.ThreadPoolExecutor` with a timeout, and kills the call if it exceeds the budget.
- **Actual behavior per plan:** Undefined — the plan states the budget but provides no implementation sketch for enforcing it. Without caller-side enforcement, `dwc doctor` can hang for 15–60 seconds per kid on a dead backend.
- **User-visible consequence:** A DIT running `dwc doctor --quick` to do a pre-roll check blocks for 60+ seconds, or the terminal hangs entirely. `--quick` is supposed to skip the self-test, but any path that does run it will block.
- **Suggested handling:** Implement the self-test using `concurrent.futures.ThreadPoolExecutor(max_workers=1).submit(...).result(timeout=0.5)` with a `TimeoutError` catch → FAIL. This is the only stdlib-safe way to enforce a wall-clock timeout on a synchronous call.

---

### [high] `dwc init` on macOS: keychain dummy sign during init can trigger the macOS permission dialog in a non-interactive context (e.g. CI, `--yes` mode)

- **Plan section:** §5.3 — "`dwc init` performs a dummy sign so the prompt appears during setup"; §5.7 — "CI runs init in a macOS runner and an Ubuntu runner"
- **Trigger:** `dwc init --backend keychain --yes` is run in CI (GitHub Actions macOS runner). The dummy sign calls `security` to retrieve the key, then signs in-process. On a fresh CI runner, the Keychain item does not exist because `dwc keygen` has not been run. The `KeychainSigner.__init__` raises `RuntimeError` (subprocess `CalledProcessError`) when the item is missing.
- **Expected behavior:** `dwc init` in `--yes` mode fails gracefully with a specific error message, not a traceback. The init docs should note that the keychain backend is not suitable for headless CI.
- **Actual behavior per plan:** §5.7 says "CI runs init in a macOS runner... then runs `dwc doctor`." This will fail unless the CI first seeds the keychain item, which is not described. The plan also says `--yes` with missing args exits nonzero with a specific error code — but missing keychain item is not a missing arg, it's a runtime error. This case is not covered.
- **User-visible consequence:** CI fails with an unhandled `RuntimeError` traceback from `KeychainSigner`, not a clean exit code. The "no traceback" guarantee in §5.7 is violated.

---

### [high] Web validator: user drops a `.zip` where the sidecar's `path` fields are absolute (e.g. `/Volumes/Mag_A001/...`)

- **Plan section:** §4.4 — user drops a zip containing the sidecar plus referenced artifacts
- **Trigger:** A production sidecar has artifact paths recorded as absolute paths (or paths relative to the production root that do not match the flat structure inside the zip). Stage 6 (artifact file integrity) attempts to open `base_dir / artifact["path"]` — inside Pyodide's `/work/` virtual filesystem, the absolute path `/Volumes/Mag_A001/...` does not exist.
- **Expected behavior:** The web validator should handle path mismatch gracefully — either by normalizing paths to be relative to the zip root, or by reporting Stage 6 as "SKIP — artifact not provided" rather than FAIL.
- **Actual behavior per plan:** The plan says the user drops "the sidecar plus referenced artifacts." It does not specify how path mapping between the sidecar's `path` fields and the zip's flat/nested layout works. The `validate_as_json()` call passes a `base_dir` of `/work`, which will not resolve absolute production paths.
- **User-visible consequence:** Stage 6 fails for every artifact with an absolute path, even when the user provided the correct files. The report shows FAIL instead of the expected PASS, making the web validator appear broken.
- **Suggested handling:** Before calling `validate_as_json`, the JS layer should inspect the sidecar's artifact paths, compute a `base_dir` from them, or the Python layer should be extended with a path-remapping mode that matches filenames from the zip against the zip's directory tree regardless of path prefix.

---

### [high] Web validator: `blake3` pure-Python fallback is mentioned but not confirmed to exist

- **Plan section:** §4.6 — "`blake3` — pure-Python fallback ships, 10× slower but fine for single sidecars"
- **Trigger:** Any sidecar that uses `blake3` as the clip-integrity hash algorithm (common on Hedge and ShotPut Pro v5+).
- **Expected behavior:** A pure-Python fallback for `blake3` is used in the browser.
- **Actual behavior per plan:** UNVERIFIED — the `blake3` package (`pip install blake3`) is a Rust extension. A "pure-Python fallback" is asserted but not identified. Pyodide ships `blake3` as a wheel only if it has been compiled to WASM; as of Pyodide 0.26, `blake3` is not in the official Pyodide package list. `micropip` cannot install native extension wheels. If no fallback exists, Stage 6 raises `ImportError` or `KeyError` from `HASH_ALGS` for blake3 sidecars.
- **User-visible consequence:** Dropping a `blake3`-hashed sidecar causes an unhandled exception in the Pyodide runtime, rendering a generic JS error instead of a validation report. User sees a broken page, not a clean "unsupported algorithm" message.
- **Suggested handling:** Verify whether a pure-Python `blake3` wheel exists for Pyodide. If not, the web validator must either exclude `blake3` from `HASH_ALGS` in its Pyodide build (patching `canonical.py` at build time) or display a clear "blake3 not supported in browser; use CLI" message when a blake3 artifact is encountered.

---

### [medium] `dwc doctor` check 11: scanning `*.omc.json` in CWD includes sidecar files being actively written by `dwc watch`

- **Plan section:** §2.3, check 11 — "All `*.omc.json` in CWD parse as JSON"
- **Trigger:** `dwc doctor` is run while `dwc watch` is concurrently writing a sidecar. The watcher uses `out.write_text(json.dumps(doc, indent=2) + "\n")` which is not atomic — it opens and writes in one call, but the file is visible to other processes as soon as it is created (zero-length or partial). If `dwc doctor` reads a partial file (e.g., a file open for write is interrupted between truncation and full write), `json.loads` raises `JSONDecodeError`.
- **Expected behavior:** Check 11 treats a `JSONDecodeError` as a FAIL for that file. This is correct but may produce false positives when watcher and doctor run concurrently.
- **Actual behavior per plan:** The plan says "corrupt file in tree → FAIL." On the reference corpus (40 clips, ~900 sidecars/sec), the window is tiny but nonzero. A DIT running `dwc doctor` from the same terminal as `dwc watch` will occasionally see a spurious FAIL.
- **User-visible consequence:** Check 11 occasionally fails for a file that is valid once the write completes. If the DIT re-runs doctor it passes. Confusing but not catastrophic.
- **Suggested handling:** Wrap the `json.loads` call in a retry (1–2 attempts, 50ms apart) before marking as FAIL.

---

### [medium] `dwc init` template rendering with `string.Template`: a kid containing `$` silently truncates the rendered output

- **Plan section:** §5.6 — "Rendered with `string.Template` (stdlib)"
- **Trigger:** A user enters a kid like `dwc-dit-$01` or a watch folder path like `/Volumes/Mag_$A001/WAR_Day01`. `string.Template` substitution will attempt to interpret `$01` or `$A001` as a variable reference. If no matching variable is defined, Python's `string.Template.substitute()` raises `KeyError`; `safe_substitute()` leaves the `$...` literal in place silently.
- **Expected behavior:** Kid and path values are validated to contain no `$` characters before being passed to `string.Template`, or the template engine is replaced with simple `str.replace()` on `{{kid}}` and `{{watchRoot}}` (as the template syntax in §5.6 uses `{{...}}` not `$...`).
- **Actual behavior per plan:** The plan shows template syntax as `{{kid}}` (double-brace), which is NOT `string.Template` syntax — `string.Template` uses `$kid` or `${kid}`. There is a mismatch between the template format shown in §5.6 and the stated rendering engine. Either the template syntax in the plan is wrong, or `string.Template` is the wrong engine. Using `string.Template` on a `{{kid}}`-style template will produce output with `{{kid}}` unreplaced, not an error.
- **User-visible consequence:** Generated `signers.json` contains the literal string `{{kid}}` instead of the actual kid value. The signer config is invalid JSON-semantically (valid JSON syntax, wrong value). `get_signer()` will look up `{{kid}}` in the keyring and fail with `KeyError`.
- **Suggested handling:** Either use `string.Template` with `$kid`-style templates throughout, or use `str.replace()` / `re.sub()` with the `{{...}}` markers as shown. Don't mix the two syntaxes.

---

### [medium] `dwc watch --emit-ale`: ALE file grows unboundedly in the "no existing ALE" first-write branch when the ALE was deleted mid-day

- **Plan section:** §1.6 — "If the ALE does not exist: write full header + one data row"
- **Trigger:** A DIT deletes `dwc-columns.ale` mid-day (e.g., to send it to a colleague via AirDrop). The next clip triggers the first-write branch (header + one row), discarding all previous rows. The 200 clips emitted earlier are lost from the ALE.
- **Expected behavior:** The plan does not address manual deletion. This is the stated design — the ALE is a re-derivable view. But there is no way to regenerate the ALE for historical clips short of re-running `dwc ale-export` manually. The `dwc ale-export` CLI subcommand in §1.5 is the recovery path, but the plan does not connect these two: the watch docs should say "if you delete `dwc-columns.ale`, run `dwc ale-export <out-dir>/*.omc.json` to regenerate it."
- **Actual behavior per plan:** Silent data loss from the DIT's perspective — the ALE goes from 200 rows to 1 row. The sidecar files are intact; only the ALE view is missing.
- **User-visible consequence:** DIT opens Silverstack after the delete, sees only one clip in the grid. Confusing.
- **Suggested handling:** Add a note to §1.6 explaining recovery via `dwc ale-export`. No code change needed.

---

### [medium] `dwc init` LaunchAgent plist: plan acknowledges `$HOME` vs `~` issue but does not specify how `$HOME` is expanded during template rendering

- **Plan section:** §5.8 — "Must use `$HOME` expansion, not `~`, or launchd rejects it silently"
- **Trigger:** The `launchagent.plist.tmpl` template must write a literal `$HOME` string (not expanded) into the XML, so that launchd performs the expansion at load time. If `dwc init` uses Python's `os.path.expanduser()` or `string.Template` to expand `$HOME` during rendering, the plist will contain the hardcoded home directory (e.g., `/Users/adam`), which breaks if the plist is loaded under a different user account (rare but real: shared DIT cart with multiple macOS users).
- **Expected behavior:** The plist template preserves the literal `$HOME` token and does NOT expand it during Python rendering.
- **Actual behavior per plan:** Undefined. The plan correctly identifies the `~` vs `$HOME` issue but does not specify how the template renderer avoids accidentally expanding `$HOME`. If `string.Template` is used (as stated), `$HOME` in the template will be treated as a variable reference and substituted with `os.environ["HOME"]` — the opposite of what launchd requires.
- **User-visible consequence:** The generated plist has a hardcoded path. On a shared Mac with multiple users, only the user who ran `dwc init` can load the plist. On a single-user Mac, it works fine but is non-portable.
- **Suggested handling:** The plist template must escape the `$HOME` reference as `$$HOME` in `string.Template` syntax. If the template engine is changed to `{{...}}` style, this issue disappears. Either way, add a test that verifies the rendered plist contains the literal string `$HOME`.

---

### [medium] `dwc doctor` check 5: "referenced by events in CWD sidecars" scan is O(n) per key per sidecar, unspecified time budget

- **Plan section:** §2.3, check 5 — "any key expired and referenced by events in CWD sidecars"
- **Trigger:** CWD contains 400 sidecars, each with 10 events. Check 5 must scan 4,000 events to determine whether any expired key is actually in use. The plan specifies a <2s total budget for `dwc doctor` and a <200ms budget for `--quick` mode. Check 5 is not in the `--quick` skip list.
- **Expected behavior:** The scan is bounded. On the reference corpus (40 clips), this is fast. At 400 clips with 10 events each, parsing 400 JSON files × 10 events = 4,000 event reads. At 900 sidecars/sec throughput (from CLAUDE.md), this is ~0.5s — acceptable. But at a larger show (2,000+ clips), the check may exceed the 2s budget.
- **Actual behavior per plan:** Undefined — no complexity analysis for check 5.
- **User-visible consequence:** `dwc doctor` takes >2s on large shows, violating the stated "under 2 seconds" goal. Not a correctness issue.

---

### [medium] Web validator: Pyodide `os.chdir('/work')` is global state; concurrent validation calls in the same Pyodide instance overwrite each other's working directory

- **Plan section:** §4.5 — `os.chdir('/work')` in the Python snippet; §4.4 — "Multiple files dragged together"
- **Trigger:** A user drops a second zip while the first validation is still running in the browser (Pyodide is single-threaded but the JS promise chain can issue a second `runPythonAsync` call before the first completes if the drop handler is not gated).
- **Expected behavior:** Either only one validation runs at a time (gate the drop handler), or `validate_as_json` accepts a `base_dir` parameter instead of relying on `os.chdir`.
- **Actual behavior per plan:** Undefined — plan does not mention concurrency control in the browser drop handler.
- **User-visible consequence:** The second validation's `chdir` overwrites the first's working directory. Stage 6 file-not-found errors appear in the first validation report. Results are silently wrong, not an obvious crash.
- **Suggested handling:** `validate_as_json()` should accept `base_dir` as a parameter (the refactor is planned anyway). The JS drop handler should disable the drop zone until the current validation completes.

---

### [medium] `dwc doctor` check 9 (hosted schema): network call in a production DIT environment may be blocked by firewall

- **Plan section:** §2.3, check 9 — "Local schemas byte-match ns.the-dwc.com"
- **Trigger:** Many film production environments (studio lot networks, set facility networks) block outbound HTTPS to arbitrary domains via corporate proxy or firewall. `curl` returns a non-zero exit code; the existing `check_hosted_schemas()` implementation treats this as a drift error (`errs += 1`).
- **Expected behavior:** A network failure (curl timeout, DNS failure, 403/407 from proxy) is classified as WARN ("could not verify — network unavailable") rather than FAIL, since the schemas being locally correct is more important than reachability of the hosted copy.
- **Actual behavior per plan:** Plan says check 9 FAILs on "drift detected." Network errors from `check_hosted_schemas()` (in `validate.py` line 480–485) already produce `errs += 1` with a "FETCH FAIL" message — inherited behavior. Doctor will FAIL for network unavailability, not just actual drift.
- **User-visible consequence:** `dwc doctor` exits 1 on a firewall-blocked network, blocking the DIT's morning health check. The remedy message points to schema drift, which is the wrong diagnosis.
- **Suggested handling:** Distinguish between "fetch failed" (WARN: can't verify) and "fetched but diverged" (FAIL: actual drift). The existing `validate.py` `check_hosted_schemas()` already has the "FETCH FAIL" path — doctor wraps it and must interpret that exit distinctly.

---

### [low] ALE emitter: Unicode clip names with right-to-left characters or zero-width joiners may pass the round-trip test but render incorrectly in Silverstack

- **Plan section:** §1.8 — "unicode clipName (e.g. `A001_Café_260115`) survives round-trip"
- **Trigger:** A clip name containing Arabic, Hebrew, or Emoji characters (e.g., a UAE co-production shoot). The test only covers `Café` (Latin-1 Extended).
- **Expected behavior:** UTF-8 is declared (§1.3: "emit UTF-8") and should handle all Unicode code points at the byte level. The round-trip test passes at the Python level.
- **Actual behavior per plan:** The plan tests a benign Unicode case. Whether Silverstack/YoYotta parse multi-byte characters correctly in ALE is not tested. This is out of scope for a code reviewer but should be flagged for the integration testing gate in §7.1.
- **User-visible consequence:** Clip names garbled in the DIT app's grid. Low severity because the sidecar itself is unaffected; only the ALE display is cosmetic.

---

### [low] `dwc init` on Linux: `systemd --user` unit template is listed as a deliverable but not included in §5.2 Deliverables

- **Plan section:** §5.2 — templates listed; §5.4 — "Linux: systemd user unit"
- **Trigger:** A user runs `dwc init` on Linux.
- **Expected behavior:** A `launchagent.plist.tmpl` is listed in §5.2 but there is no `systemd.service.tmpl` in the deliverables list. §5.4 says the Linux mechanism is "systemd user unit" but the template is not listed.
- **Actual behavior per plan:** Plan is incomplete — the Linux template is described in behavior (§5.4) but absent from deliverables (§5.2).
- **User-visible consequence:** Linux `dwc init` prints instructions for systemd but cannot write the unit file.

---

### [low] `dwc doctor` check 8: WARN on `keys.priv.json` present when backend ≠ `local` — but the condition is reversed for the common dev-to-production migration case

- **Plan section:** §2.3, check 8
- **Trigger:** A DIT migrated from dev (`local` backend, `keys.priv.json` present) to production (`keychain` backend). The old `keys.priv.json` was not deleted. Check 8 WARNs — correct.
- **Actual behavior per plan:** The check fires correctly. However, the plan says "WARN only — user might still want dev defaults." Given that the plan's §5.5 explicitly says `dwc init` "never writes `keys.priv.json`," the presence of `keys.priv.json` alongside a non-local backend is always a legacy artifact that should be cleaned up. A WARN is appropriate but the remedy message should be explicit: "Run `rm keys.priv.json` — this file contains plaintext private keys and is no longer needed."

---

## Failure modes checked with no concerns

- **ALE `--validate` flag with failing sidecar**: plan correctly specifies the sidecar produces `DWC_Signed=false` and a WARN log but does not abort the export. This matches the existing watcher pattern of continuing on per-clip errors.
- **`dwc doctor` no keyring.json**: check 4 explicitly FAILs on missing keyring. Consistent with existing validator behavior (Stage 4 skips gracefully when keyring is absent, but doctor escalates it to FAIL, which is appropriate for a pre-flight check).
- **`dwc init --force`**: the plan correctly requires `--force` to overwrite existing `keyring.json`/`signers.json`. Consistent with the convention of never silently destroying config files.
- **Web validator 2GB gate**: plan explicitly gates at 2GB in the drop-handler and recommends the CLI for larger. Reasonable mitigation for the OOM risk.
- **Signer self-test audit trail**: plan explicitly documents that `dwc doctor` produces audit log entries (Vault, KMS, CloudTrail). Good disclosure.
- **`.watch-state.json` schema extension (§3.7)**: bounded at 100 entries. The bound prevents unbounded memory growth in the Swift app and unbounded disk growth. Adequate.
- **Docker detection**: `/.dockerenv` and `container=docker` env are the standard two signals; detection is adequate for the common cases.
- **`dwc init` never overwrites `keyring.json` without `--force`**: correct guard against the most destructive init accident.
- **ALE I/O failure does not block sidecar emission**: §1.6 specifies "log WARN and continue." This matches the existing error-swallowing pattern in `watch.py`'s scan loop.
- **Existing `mhl_walker.py` collision handling** (REFRESH / CONFLICT with hash-prefix suffix): plan does not touch this logic; the existing robust behavior is preserved.
- **`DWC_SidecarPath` relative to ALE directory**: specifying this as relative-to-ALE-dir (not absolute) means the ALE is portable if the ALE and sidecars move together. Correct choice.

---

## Unverified claims

- **§4.6: "pure-Python `blake3` fallback ships"** — No pure-Python `blake3` implementation is known to exist as a standalone package. The `blake3` PyPI package is a Rust extension with no pure-Python fallback mode. UNVERIFIED whether Pyodide's package set includes a WASM-compiled `blake3` wheel compatible with the version pinned in `pyproject.toml`.
- **§1.7: "Silverstack 8+ remembers imported custom columns across project sessions"** — Claimed without citation. Could not verify against Silverstack documentation in this repository. UNVERIFIED.
- **§4.5: `micropip.install(['jsonschema', 'rfc8785', 'cryptography', 'xxhash'])`** — `cryptography` is a native extension (Rust/C). Pyodide ships a WASM build of `cryptography` in its official package list, but the version may differ from `pyproject.toml`'s pinned version. Compatibility UNVERIFIED. If the Pyodide `cryptography` version is older than the `Ed25519PublicKey.from_public_bytes` API used in `canonical.py`, the import will succeed but the call will raise `AttributeError`.
- **§2.4: "500ms per kid; timeout → FAIL"** — No mechanism is described for enforcing this timeout against synchronous signer calls (see finding above). The Vault signer uses `timeout=15` at the urllib level; GCP-KMS uses gRPC default. Whether 500ms can be enforced at the caller level without threading is UNVERIFIED.
