# `dwc doctor`

Pre-flight audit of a DWC signing host. Runs twelve independent checks against the current working directory and the configured signer; prints a `[PASS]/[WARN]/[FAIL]` table plus a Remedies section for anything not green.

```
dwc doctor              # full audit, ~1–2 s
dwc doctor --quick      # skip the two network/signer checks; <200 ms
dwc doctor --json       # machine-readable; consumed by CI and the menu-bar status app
```

Exit code is `0` on all-PASS-or-WARN, non-zero if any FAIL. Use `--json` for scripting.

## What each check does

The checks run in this fixed order; see [`src/dwc_sidecar/doctor.py`](../../src/dwc_sidecar/doctor.py) for the implementation. Tag conventions:

- **PASS** — green, no action required.
- **WARN** — non-blocking issue; signing still works but something is off (e.g. expiring key, drift in a non-critical surface).
- **FAIL** — blocking issue; signing is unsafe or impossible until you fix it.

### 1. Python version

Runtime version ≥ 3.11. **FAIL** below that — the package depends on syntax and stdlib features that don't exist in older Pythons. Remedy: install a newer Python (`pipx` will pick it up; `pipx reinstall dwc-sidecar` after).

### 2. Required packages

All six runtime deps importable: `jsonschema`, `rfc8785`, `cryptography`, `xxhash`, `blake3`, `pyyaml`. **FAIL** if any are missing — usually means the install was incomplete or a venv shadows the pipx install. Remedy: `pipx reinstall dwc-sidecar`.

### 3. Hash algorithms

Every algorithm referenced in any `*.omc.json` in CWD is resolvable through `dwc_sidecar.canonical.HASH_ALGS`. **FAIL** if a sidecar mentions an algorithm the runtime can't compute (e.g. an old sidecar referenced `md5` from a build that dropped it). Remedy: rebuild the affected sidecars from current MHLs.

### 4. `keyring.json`

`./keyring.json` exists, parses as JSON, has at least one kid, and conforms to the published v0.1 keyring schema. **FAIL** if missing/malformed. Remedy: run `dwc init` (creates a fresh keyring) or paste a published `keyring.json` from your project's source of truth.

### 5. Keyring validity windows

Every kid that signed any event in any local sidecar has a current entry in `keyring.json` whose `validFrom`/`validUntil` window covers the event's `ts`. **WARN** if a kid is *expiring soon* (≤ 14 days). **FAIL** if a sidecar's signer kid is missing, revoked, or out-of-window. Remedy: rotate the kid (publish a new keyring entry) or, for archival, re-sign the affected sidecars with a current kid.

### 6. Signer config (`DWC_SIGNERS`)

`DWC_SIGNERS` env var is set, points at a parseable `signers.json`, and every kid in `keyring.json` has a backend mapping. **WARN** if the env var is unset and the runtime is falling back to `./keys.priv.json` (dev default). **FAIL** if `DWC_SIGNERS` is set but the file is unreadable, malformed, or missing a kid. Remedy: `export DWC_SIGNERS="$PWD/signers.json"` (add to `.zshrc`/`.bashrc` for persistence) and check the file's JSON.

### 7. Signer self-test *(skipped on `--quick`)*

The configured signer for the *first* kid in the keyring signs a 32-byte throwaway payload through to the backend. Catches Keychain auth prompts, Vault token expiry, GCP credential failures, PKCS#11 hardware not present, etc., before they bite during a real signing run. **WARN** if the keyring is empty (no kid to test against). **FAIL** if the backend refuses to sign. Remedy: depends on backend — see [`signer-backends.md`](signer-backends.md) for per-backend failure modes.

### 8. Plaintext private keys

`./keys.priv.json` (the dev-default file) is **absent**. **WARN** if present, because it carries plaintext private keys at rest. Remedy: `rm keys.priv.json` once you've confirmed your `signers.json` points at a real backend (Keychain, file at a non-CWD path, PKCS#11, or a cloud backend).

### 9. Hosted schema drift *(skipped on `--quick`)*

Each schema bundled in this install byte-matches the canonical hosted copy at `https://ns.the-dwc.com/sidecar/v0.1/`. **FAIL** on drift — your local schemas have been mutated (sketchy) or the hosted copy has changed (someone shipped a non-immutable update, which is a v0.1 invariant violation). Remedy: `pipx reinstall dwc-sidecar` to restore bundled schemas, or open an issue if the hosted copy diverged.

### 10. `.watch-state.json`

If a `.watch-state.json` exists in CWD, it parses as valid JSON and its `processed_mhl_sha256` list is non-empty (i.e. the watcher has actually processed something). **WARN** if the file is corrupt or stale (zero entries despite a watcher having run). Remedy: stop the watcher, `rm .watch-state.json`, restart — every MHL gets reprocessed but resulting sidecars are content-addressed so this is safe.

### 11. Sidecar parse

Every `*.omc.json` in CWD parses as valid JSON and contains a `customData[dwc.sidecar.*]` block. **FAIL** on a corrupt or non-DWC `.omc.json`. Remedy: regenerate the affected sidecar via `dwc bootstrap` or `dwc batch`. The check uses an internal retry to tolerate concurrent writes by `dwc watch`.

### 12. Key expiry window

Every kid in `keyring.json` is valid for at least the next 14 days. **WARN** if a kid expires within 14 days. **FAIL** if any kid is already expired (which means signing with it would produce sidecars that fail validation). Remedy: `dwc keygen --kid <new-kid> --backend <backend>` to generate a successor; publish the new keyring entry; rotate when ready.

## Output formats

Default is the table format above with a Remedies section. For automation:

```bash
dwc doctor --json
```

Emits `{"status": "pass|warn|fail", "checks": [...]}` where each check has `id`, `title`, `status`, `detail`, `remedy`. The menu-bar status app (`§3` of the phase plan) consumes this every 60 s with `--quick` for sub-200 ms refresh.

## When to run doctor

- After `dwc init` on a fresh host — confirms the install is healthy before any production signing.
- Before kicking off a long `dwc watch` session — confirms the signer backend is reachable.
- In CI after dependency upgrades — catches accidental schema drift or package regressions.
- When `dwc validate` starts failing with "kid out of window" or similar — doctor's check 5 / 12 will tell you exactly which kid expired.

## Related

- [`watch.md`](watch.md) — the long-running watcher whose state doctor inspects.
- [`signer-backends.md`](signer-backends.md) — per-backend setup and failure modes referenced by checks 6–7.
- [`../quickstart.md`](../quickstart.md) — the full first-3-minutes walk-through that ends with a green doctor.
