# Plan — Trust extensions: m-of-n locks, transfer counter-signing, sealed-bundle verify

_Drafted 2026-06-12 · Follows up the three "genuine protocol design work" items deferred from
`plans/code-review-260611.md` (implemented through v0.2: signed artifact commitments, chain-head
anchor, lock signature verification, `--require-keyring`)._

## What these three features have in common

All three turn the format from "one signer attests" into "multiple parties attest, and a
recipient can check it with nothing but the bundle in hand":

1. **m-of-n threshold locks** — a lock can require signatures from multiple roles
   ("DIT + Post must both freeze the AMF"), expressed in the lock record and enforced by the
   validator.
2. **Two-party `transfer` counter-signing** — the `transfer` action (already in the event enum,
   currently unvalidated) becomes a sender-signed offer + receiver-signed acceptance, so the
   format actually proves chain-of-custody between facilities.
3. **Sealed-bundle verify** — `dwc verify <bundle>` takes sidecar + keyring + files as one unit,
   never resolves CWD files, and never fails open. The receiving end of (2).

Features 1–2 change schemas (locks + events) and need format-version handling. Feature 3 is
**schema-neutral** and can ship independently, in any order.

---

## Decision gate 0 — v0.2 fold-in vs. v0.3 bump  ⚠️ check first

As of 2026-06-12 the v0.2 schemas are **not yet live** at `ns.the-dwc.com/sidecar/v0.2/`
(checked: 404). CLAUDE.md convention #4: *before publication a version's URLs are editable;
after publication they are frozen.*

- **If v0.2 is still unpublished when this work starts** (the v0.2 commit hasn't been pushed /
  Pages hasn't deployed): fold the locks/events schema changes **into v0.2**. No new version
  directory, no second freeze, emitters/`SIDECAR_NS` unchanged. This is the cheap path and the
  recommended one if the work follows on quickly.
- **If v0.2 has shipped**: bump to **v0.3** — copy current schema bytes to
  `tools/publish-schemas/frozen/v0.2/`, add `("v0.2", […4 files…])` to `FROZEN_VERSIONS` in
  `tools/publish-schemas/build.py`, change every `$id` to `/v0.3/`, bump `SIDECAR_NS` in
  `canonical.py`, update `VERSION` in build.py, re-sign examples. The drift workflow and
  `--check-hosted` already derive URLs from `$id`, so they need no code change.

The rest of this plan says "vNEXT" for whichever applies. Validator version gates added in v0.2
(`_payload_version()` in `validate.py`) extend naturally: v0.1 → NOTE, v0.2/vNEXT → enforce.

---

## Phase 1 — Shared groundwork

Both lock co-signing and transfer counter-signing append events to an **existing** sidecar.
Nothing in the codebase does that today (all emitters create single-event documents; only
`sign_example` rewrites chains, and it re-signs from scratch).

### 1.1 `src/dwc_sidecar/append.py` — append-to-chain machinery

```python
def append_event(asset: dict, body: dict, signer) -> dict
    # - find the asset's events entry, take last event's hash as prevHash
    # - assign seq = last.seq + 1, ts = now (UTC, Z-suffixed)
    # - event_hash() + sign via signer (same JCS path as canonical.py)
    # - rewrite the dwc.sidecar.head entry via make_head(new_event, signer)
    # - returns the signed event
def load_sidecar_asset(path, *, clip_uuid=None) -> (doc, asset)
    # single-asset docs: the asset; multi-asset (reel): require --clip-uuid
def save_sidecar(path, doc)  # temp + os.replace (reuse watch._atomic_write_text — move it
                             # to a shared module, e.g. canonical.py or a new _io.py)
```

Design rule carried over from v0.2: **whoever appends last re-signs the head.** Append must
also *verify before appending* (run `validate_chain_integrity` + `validate_binding` on the
asset and refuse to extend a broken chain) — otherwise the CLI happily launders a tampered
sidecar by appending a fresh signed tip.

### 1.2 kid↔actor binding in `keyring.json`  (prerequisite for meaningful two-party proof)

Today nothing binds an actor URN to a kid: any trusted key can sign an event claiming any
`actor.id`. For threshold locks ("distinct *parties*", not "distinct keys") and transfer
("the *receiver* accepted") this matters.

- Keyring entry gains optional `"actor": "urn:email:post@the-dwc.com"`.
- Stage 4: when the signing kid's keyring entry declares `actor`, the event's `actor.id` must
  match → FAIL on mismatch. No `actor` declared → behavior unchanged (NOTE at most).
- `keygen` prints the field in its paste-ready entry when `--actor` is given.
- `sign_example` writes actor bindings for the three demo kids.

Backward-compatible: pure keyring extension, no schema bump implications (keyring is not one of
the published schemas).

### 1.3 Canonical-bytes strip set

`canonical_bytes()` strips `hash`/`sig`. Threshold locks add a `sigs` array that must also be
excluded from the signed body. Extend `_strip()` to `("hash", "sig", "sigs")` — safe for all
existing v0.1/v0.2 records (none carry a `sigs` key, so existing signatures are unaffected).
Add a regression test asserting v0.2 example signatures still verify after the change.

**Effort: S–M. No schema changes yet; everything here is additive.**

---

## Phase 2 — m-of-n threshold locks

### 2.1 Schema (`locks.schema.json`, vNEXT)

A lock is **either** legacy single-sig (unchanged, stays valid) **or** threshold:

```jsonc
{
  "target": "urn:uuid:…", "scope": "artifact",
  "by": "urn:email:dit@…",          // initiator (unchanged)
  "at": "2026-06-12T10:00:00Z",
  "reason": "…",
  "policy": { "m": 2, "of": ["dwc-dit-01", "dwc-post-01", "dwc-color-01"] },
  "sigs": [ { "alg": "ed25519", "kid": "dwc-dit-01",  "value": "…" },
            { "alg": "ed25519", "kid": "dwc-post-01", "value": "…" } ]
}
```

- Schema `oneOf`: (`sig` required, no `policy`/`sigs`) XOR (`policy` + `sigs` required, no `sig`).
- `policy.m ≥ 1`, `policy.of` minItems ≥ m, items unique.
- Every `sigs[].value` signs **the same bytes**: `JCS(lock minus sig/sigs)` — so the policy
  itself is under every co-signature; editing `m` or `of` invalidates all of them.

### 2.2 Validator (Stage 5 extension — no renumbering)

For a threshold lock, per asset:
1. every `sigs[].kid` must be in `policy.of`; duplicates count once;
2. each signature verifies cryptographically against the keyring (revocation + validity window
   via `_check_key_window`, using the matching event's ts);
3. each co-signing kid must have a **matching signed `lock` event in the same asset** (same
   `target`, event `sig.kid` == co-sig kid) — co-signing appears in the append-only log, not
   just in the derived record;
4. count of valid, event-backed, distinct kids ≥ `m` → pass; else FAIL with
   `"threshold not met (k of m)"`.

**Policy-downgrade defense (the part the record can't provide for itself):** an attacker who
controls one trusted key could write a fresh 1-of-1 lock. Optional keyring extension:

```jsonc
"policies": { "lock": { "artifact": { "m": 2, "of": ["dwc-dit-01", "dwc-post-01"] } } }
```

When the verifier's keyring declares a lock policy for a scope, Stage 5 enforces it as a
*minimum* regardless of what the document's own policy says. Document + tests.

**`unlock` semantics:** an unlock of a threshold lock requires the same threshold (m matching
`unlock` events from `policy.of` kids). Without this, one signer can undo what two had to
agree on. Validator: a lock record removed from `locks[]` is invisible (derived view), so the
check is event-level: WARN when the event log shows a threshold `lock` followed by `unlock`
events that never reached m. (Full lock-lifecycle reconstruction is out of scope; keep this
warning-only and document it.)

### 2.3 CLI — `dwc lock` / `dwc lock cosign`  (new module `src/dwc_sidecar/lock.py`)

```bash
dwc lock <sidecar> --target <urn> --scope artifact \
         --policy 2 --of dwc-dit-01,dwc-post-01 \
         --signing-kid dwc-dit-01 [--reason "…"] [--clip-uuid …]
# appends a signed lock event, creates the lock record with policy + first sig, rewrites head

dwc lock cosign <sidecar> --target <urn> --signing-kid dwc-post-01
# verifies the chain first; appends the co-signer's lock event; appends to record.sigs;
# rewrites head; prints "threshold now satisfied (2/2)" or "pending (1/2)"
```

Both go through Phase 1's `append_event` and `signers.get_signer(kid)` (HSM backends work
unchanged — co-signing is exactly the DIT-laptop-plus-post-HSM scenario).

### 2.4 Examples + sign_example

Convert `example-clip`'s existing lock to a 2-of-2 (`dwc-color-01` + `dwc-post-01`) with the
matching second lock event, so the stub demonstrates the feature and
`test_stub_clip_passes_all_stages` exercises threshold verification. `sign_example.re_sign_asset`
learns to (re)build `sigs[]` from `policy.of` ∩ available demo keys.

### 2.5 Tests (`tests/test_threshold_locks.py`)

| Case | Expect |
|---|---|
| 2-of-3, two valid co-sigs + events | pass |
| 2-of-3, one sig | FAIL "threshold not met (1 of 2)" |
| duplicate kid signs twice | counts once → FAIL |
| co-sig kid not in `policy.of` | FAIL |
| co-sig valid but no matching lock event | FAIL |
| edit `policy.m` 2→1 after signing | all sigs invalid → FAIL |
| keyring `policies.lock` demands 2, doc says 1-of-1 | FAIL |
| legacy single-sig lock (v0.2 form) | still passes |
| revoked co-signer key | FAIL via `_check_key_window` |
| `dwc lock` + `dwc lock cosign` round-trip on a bootstrap-emitted sidecar | validates 10/10 |

**Effort: M. Schema + Stage 5 are the core; CLI is mostly Phase 1 plumbing.**

---

## Phase 3 — Two-party transfer counter-signing

### 3.1 Event model (`events.schema.json`, vNEXT)

Two chained events; the acceptance commits **by hash** to the exact offer:

```jsonc
// sender appends:
{ "seq": 4, "action": "transfer", "actor": {"id": "urn:email:dit@onset.com", "role": "DIT"},
  "target": "urn:uuid:<clip-uuid>",
  "transfer": { "to": "urn:email:io@postfacility.com" },
  "artifacts": [ {"id": "…", "hash": {…}}, … ],   // re-commit what is being handed over
  "prevHash": "…", "hash": "…", "sig": {…} }

// receiver appends (possibly days later, at the other facility):
{ "seq": 5, "action": "accept", "actor": {"id": "urn:email:io@postfacility.com", "role": "IO"},
  "target": "urn:uuid:<clip-uuid>",
  "transfer": { "of": "sha256:<hash of the transfer event>", "from": "urn:email:dit@onset.com" },
  "prevHash": "…", "hash": "…", "sig": {…} }
```

Schema changes (both additive):
- `action` enum gains `"accept"`;
- optional `transfer` object: `{to}` (required when action=transfer), `{of, from}` (required
  when action=accept) — expressed with `if/then` like the existing `seq:1 → prevHash:null` rule.

The offer re-commits the artifact hashes so acceptance means "I verified *these bytes*", not
"I acknowledge something arrived". (`artifacts` commitments already exist from v0.2.)

### 3.2 Validator (Stage 3.5 extension — structural; Stage 4 already covers sigs)

Per asset:
- `accept.transfer.of` must equal the hash of an earlier `transfer` event in the same chain
  → FAIL otherwise ("acceptance does not reference this chain's offer").
- `accept.actor.id` must equal that offer's `transfer.to`; `accept.transfer.from` must equal
  the offer's `actor.id` → FAIL otherwise.
- The two events must be signed by **different kids**; with Phase 1.2 actor bindings, sender
  and receiver keys belong to their declared actors → FAIL on same-kid self-acceptance.
- A `transfer` with no matching `accept` → **WARN** ("custody hand-off not counter-signed —
  transfer in flight"), never FAIL: pending transfers are a legitimate state. An `accept`
  with no offer → FAIL.

### 3.3 CLI — `dwc transfer offer` / `dwc transfer accept`  (new module `transfer.py`)

```bash
dwc transfer offer  <sidecar> --to urn:email:io@post.com --signing-kid dwc-dit-01
dwc transfer accept <sidecar> --signing-kid dwc-post-io-01 --base-dir <receiving-root>
```

`accept` must **re-verify before signing** — run the full validator (with `--require-keyring`
semantics) against the receiver's local copy of the files; refuse to counter-sign on any error.
That ordering is the whole point: the acceptance signature attests a successful verification at
the receiving site. Natural composition: `accept` over a Phase-4 bundle
(`dwc transfer accept <bundle.zip> …`) is the flagship workflow.

### 3.4 Examples + tests (`tests/test_transfer.py`)

Add a transfer/accept pair to `example-reel`'s second asset (`dwc-dit-01` → `dwc-post-01`).
Test matrix: happy path; wrong receiver actor; wrong `of` hash; same-kid self-accept; accept
before offer; pending-offer WARN (and exit code stays 0); accept CLI refuses on Stage-6
mismatch (tamper a byte first); actor-binding mismatch.

**Effort: M. The CLI verify-then-sign flow is the trickiest part; the schema/validator work is
mechanical after Phase 2 establishes the patterns.**

---

## Phase 4 — Sealed-bundle verify  (independent — no schema changes; can ship first)

### 4.1 The trust problem to solve explicitly

A bundle that carries its own keyring proves only internal consistency — an attacker builds a
perfectly self-consistent bundle around their own keys. The verifier needs trust from
**outside** the bundle. Three modes, in descending strength, all explicit in the UX:

1. `--keyring <path>` — external keyring supplied by the verifier (strongest);
2. `--keyring-fingerprint <sha256>` — bundled keyring accepted only if its bytes match a
   fingerprint received out-of-band (e-mail, transfer manifest, phone call);
3. neither → run, but print an unmissable banner: `TRUST IS SELF-CONTAINED — signatures
   verified against the keyring *inside* the bundle; confirm its fingerprint out-of-band:
   sha256:…` and exit non-zero unless `--trust-bundled-keyring` is passed. **Never silent.**

### 4.2 `dwc bundle` (create) — `src/dwc_sidecar/bundle.py`

```bash
dwc bundle <sidecar> --base-dir <root> --out clip.dwcbundle.zip [--lite]
```

- Zip layout: sidecar at root, `keyring.json` (+ `revocations.json` if present), artifact files
  at their sidecar-relative paths, `dwc-bundle.json` manifest
  `{bundleVersion: 1, created, tool, keyringSha256, sidecar: <name>}`.
- Collects exactly the files the artifacts block references (resolved with the validator's
  `_safe_path` — refuse to bundle an escaping path).
- `--lite` excludes `role: clip-integrity` files (camera originals are tens of GB); records
  the omission in the manifest so verify can demand `--allow-missing-clips` explicitly.
- Prints the keyring fingerprint at the end — that line is what the sender e-mails the
  receiver.

### 4.3 `dwc verify` (verify)

```bash
dwc verify clip.dwcbundle.zip [--keyring K | --keyring-fingerprint H | --trust-bundled-keyring]
                              [--allow-missing-clips] [--strict]
```

- Extracts to a temp dir (zip-slip-safe: reject entries escaping the extraction root — audit
  and reuse `web_remap`'s zip handling, which already serves the browser drop-zone, and add an
  explicit zip-slip test either way).
- Runs `validate_as_json(sidecar, base_dir=tmp, keyring_path=…, require_keyring=True)`.
  **Hard rules, not flags:** no CWD-relative resolution of anything (`keyring.json`,
  `revocations.json`, `keys.priv.json` are never picked up from the caller's directory);
  missing keyring is always FAIL; missing artifact files are FAIL unless the manifest's
  declared `--lite` omission + the verifier's `--allow-missing-clips` both agree.
- Exit code: errors → 1; `--strict` also promotes warnings (weak algs, pending transfers,
  keyring NOTEs) to 1.
- Output ends with a one-line verdict including the keyring fingerprint that was trusted and
  how (`external | fingerprint-pinned | bundled (trusted by flag)`).

Also expose `verify_bundle(path, …) -> report` as a library function — the menu-bar app and
the web validator's zip path should converge on it eventually (note as follow-up, don't
refactor the web validator in this pass).

### 4.4 Tests (`tests/test_bundle.py`)

Round-trip create→verify OK (10/10); tampered artifact byte → FAIL; sidecar edited after
bundling → FAIL (signatures); keyring missing from bundle and no flags → FAIL;
`--keyring-fingerprint` mismatch → FAIL before any stage runs; bundled-keyring mode without
`--trust-bundled-keyring` → non-zero + banner; zip-slip entry (`../../evil`) → refused;
`--lite` bundle verifies with `--allow-missing-clips` and fails without; CWD contamination
test: run verify from a directory containing a *different* `keyring.json` and assert it is
ignored.

**Effort: M. Mostly CLI/IO + UX care; the validator already does the heavy lifting.**

---

## Phase 5 — Release mechanics

- `pyproject.toml` → 0.6.0 (new subcommands + format extension).
- `cli.py`: register `lock`, `transfer`, `bundle`, `verify` (+ help text).
- Schema publication per Decision gate 0 (fold-in vs. freeze v0.2 + new v0.3 dir).
- `sign_example` + both examples re-signed (threshold lock in clip, transfer pair in reel);
  `tests/test_validate_as_json.py` / `test_sign_example_multi.py` expectations updated.
- Docs: CLAUDE.md (stage descriptions for extended 3.5/5, new trust-surface notes for
  `policies.lock` and actor bindings, new commands in Common commands), README command table,
  `docs/quickstart.md`, a new `docs/operations/transfer.md` describing the facility-to-facility
  workflow (offer → bundle → ship → verify → accept), web-validator copy if stage wording is
  surfaced there.
- Real-corpus smoke: `DWC_CORPUS=/Volumes/DWC_Shuttle-04/WAR/260115_SD084 pytest` plus a manual
  `dwc bundle`/`dwc verify --lite` against one VENICE clip from the reference corpus.

## Suggested order & sizing

| Order | Phase | Size | Depends on |
|---|---|---|---|
| 1 | Phase 4 — sealed bundle | M | nothing (schema-neutral, immediately useful) |
| 2 | Phase 1 — append machinery + actor binding | S–M | nothing |
| 3 | Phase 2 — threshold locks | M | Phase 1 |
| 4 | Phase 3 — transfer counter-signing | M | Phases 1, (4 for the flagship accept-a-bundle flow) |
| 5 | Phase 5 — release | S | all |

## Open questions (answer before Phase 2/3 start)

1. **Version**: fold into the still-unpublished v0.2, or ship v0.2 first and do this as v0.3?
   (Gate 0 — depends only on whether the v0.2 push happens first.)
2. **Acceptance action name**: `accept` vs `receive` vs overloading `verify`. Plan assumes
   `accept` (new enum value).
3. **Unlock threshold**: is warning-only unlock enforcement acceptable for now (full lock
   lifecycle reconstruction deferred), or must unlock be a hard m-of-n check in this pass?
4. **Actor bindings**: OK to make Stage 4 FAIL on kid↔actor mismatch when the keyring declares
   a binding, or should that start as WARN for one release?
5. **Bundle naming**: `.dwcbundle.zip` double extension vs plain `.zip` — any tooling on the
   YoYotta/Silverstack side that cares? (Martin at YoYotta may have an opinion on what their
   transfer manifests could embed for the fingerprint hand-off.)
