# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A reference implementation of a per-clip film-industry metadata sidecar format that **composes with MovieLabs OMC v2.8** rather than replacing it. The sidecar is a JSON document that references (never duplicates) AMF, ASC MHL, ASC FDL, ASC CDL, and DaVinci Resolve exports, carries a signed append-only provenance log, and verifies end-to-end through a ten-stage validator.

The design principle that governs everything: **reference canonical files by content hash, carry cryptographic provenance above them, and never re-invent what OMC already defines.** Since format v0.2, the signed events also *commit* to the artifact hashes (and a signed chain-head anchors the log), so the signatures cover the integrity claims themselves — not just the narrative.

## Architecture

### Layering

```
OMC v2.8 JSON Schema (upstream, unmodified)         ← envelope
    │
    └─ assetFC.functionalProperties.customData[]    ← OMC extension point
            │
            ├─ dwc.sidecar.artifacts                ← hashes of referenced files
            ├─ dwc.sidecar.events                   ← Ed25519-signed, hash-chained log
            │                                          (each event commits to artifact hashes)
            ├─ dwc.sidecar.locks                    ← derived view of 'lock' events (signed)
            └─ dwc.sidecar.head                     ← signed chain-head anchor (v0.2)
```

All DWC-specific payload lives under OMC's documented `customData` array. Nothing is added at the top level — OMC uses `unevaluatedProperties: false`, so top-level extensions would fail validation.

### The ten validation stages (`dwc_sidecar/validate.py`)

Each stage is a function returning an error count; their sum is the exit code. Stages are independent and idempotent. Know what each attests before changing validator code:

1. **OMC v2.8 structure** — upstream JSON Schema
2. **DWC payload schemas** — `src/dwc_sidecar/data/schemas/{artifacts,events,locks,head}.schema.json`
3. **Event chain continuity** — `seq` monotonic, `prevHash` links (null/absent `seq` is an error, never a crash)
3.5. **Event↔asset binding + signed commitments** — per asset: every `event.target` resolves to one of that asset's identifiers or artifact ids (blocks cross-sidecar replay); the artifact hashes *committed inside signed event bodies* match the declared artifacts block (v0.2 requires every artifact to be committed — this is what makes the artifact hashes tamper-evident); the `dwc.sidecar.head` anchor matches the chain tip (truncation detection); **two-party transfer binding** — an `accept` event must commit by hash to an earlier `transfer` offer in the same chain (`transfer.of`), name that offer's receiver as its actor and the offerer as `transfer.from`, and be signed by a *different* kid than the offer (no self-acceptance); a `transfer` with no matching `accept` is an in-flight hand-off → **WARN**, never FAIL. v0.1 payloads get NOTE lines instead of failures.
4. **Ed25519 signatures + key validity** — events *and* head records; window + CRL-style revocation. A missing `keyring.json` is a **warning** (or a failure with `--require-keyring`) — never a silent pass. When the signing kid's keyring entry declares an `actor` URN, the event's `actor.id` must match it (kid↔actor binding) — without this, any trusted key could impersonate any actor and the "distinct parties" guarantee below would be vacuous.
5. **Lock ↔ signed event crosscheck** — per asset (a lock on asset A cannot be satisfied by an event on asset B). A **single-signer** lock pairs to one signed `lock` event and its own Ed25519 sig is verified over JCS(lock minus `sig`). An **m-of-n threshold** lock (`policy` + `sigs`) requires `m` valid, event-backed, *distinct* co-signers, each signature taken over the shared JCS(lock minus `sig`/`sigs`) so the policy itself is under every co-signature; editing `m`/`of` invalidates all of them. A keyring-declared minimum (`policies.lock.<scope>`) is enforced on top as a floor — the **policy-downgrade defense** against an attacker who controls one trusted key and writes a fresh 1-of-1. Unlock-below-threshold is warning-only.
6. **Artifact file integrity** — paths are containment-checked against `--base-dir` (traversal = FAIL), declared hashes match bytes on disk (supports `--trust-mhl` to skip when an MHL in the same sidecar already attests); weak algs (`md5 sha1 xxh64 xxh3`) on integrity-role artifacts log WARN
7. **`x-controlledValues` enforcement** — treated as `enum` via jsonschema extension
8. **MHL inner consistency** — re-hash files the MHL points to against its own declarations (entry paths containment-checked)
9. **CDL consistency (warning-only)** — standalone CDL vs AMF-embedded `lookTransform`s; divergence logs WARN but never fails validation

### Hash registry (`dwc_sidecar/canonical.py`)

`HASH_ALGS` maps name → hasher class. Supports `md5 sha1 sha256 sha512 blake3 xxh64 xxh3 c4`. The **C4 implementation is cross-verified against `Avalanche-io/pyc4`** by `tests/test_c4_interop.py` — do not modify the C4 algorithm without re-running `pytest tests/test_c4_interop.py`.

Canonicalisation for event hashing is **RFC 8785 JCS** via the `rfc8785` pip package. Event hash = `sha256(JCS(event_body_minus_hash_and_sig))`. Signatures are Ed25519 over the same canonical bytes.

### The two ingestion paths

Both produce valid sidecars; they differ in I/O cost:

- **`dwc bootstrap` / `dwc batch`** — re-read clip bytes to compute the clip-integrity hash. Cost: ~450 MB/s, bounded by source-disk sequential read.
- **`dwc mhl-walk`** — lift the clip-integrity hash directly from the MHL the DIT tool already wrote. Cost: ~900 sidecars/sec (constant-time, clip-size-independent). **This is the production path.** Use `dwc batch` only as a periodic audit.

`dwc watch` wraps `mhl-walk` with polling, stability detection, dedup by MHL sha256, collision handling (REFRESH / CONFLICT), optional post-emit validation with a quarantine for failures, and `.watch-state.json` for restart-safe resumption.

### External trust surfaces

- `keyring.json` — publishable Ed25519 public keys with `validFrom` / `validUntil` / `revokedAt`. Required at validation time; path is CWD-relative. Two optional fields turn keys into trust anchors:
  - per-key `"actor": "urn:email:post@the-dwc.com"` — binds a kid to an actor. Stage 4 FAILs any event signed by that key under a different `actor.id`. Set it with `dwc keygen --actor …`; the demo keyring (written by `dwc sign-example`) binds all four demo kids. This is the prerequisite for *meaningful* threshold/transfer proofs ("distinct parties", not just "distinct keys").
  - top-level `"policies": { "lock": { "<scope>": { "m": 2, "of": ["kid", …] } } }` — a verifier-side minimum lock policy. When present, Stage 5 enforces it as a floor regardless of the document's own policy (policy-downgrade defense). The keyring is the verifier's *out-of-band* trust input; the document cannot supply this for itself.
- `revocations.json` (optional) — CRL-style, overrides `keyring.json` at validation time.
- **Signer backends** (`dwc_sidecar/signers/`) — production key material is held by a backend, never inlined. Every production signing callsite (`bootstrap`, `batch`, `mhl_walker`, `watch`, plus the append-based `lock` and `transfer`) goes through `signers.get_signer(kid)`, so HSM/cloud backends work unchanged for co-signing and counter-signing (`sign_example` uses `JsonFileSigner` directly — demo keys only). Backend selection per-kid via `DWC_SIGNERS=<path>` env var pointing at a JSON config (example in `dwc_sidecar/signers/__init__.py` docstring). Unset → `JsonFileSigner` reading `keys.priv.json` — dev default.
  - `local` — dev: reads base64 Ed25519 private keys from `keys.priv.json` in CWD. Private keys at rest; **never use in production**.
  - `file` — reads from an arbitrary path (Docker/Kubernetes secret mount, external volume). Same on-disk format as `local`; keeps private keys off the repo.
  - `pkcs11` — any PKCS#11 v3.0 token (YubiHSM 2, Nitrokey, SoftHSM, Thales, **AWS CloudHSM**, Entrust nShield). Private key never leaves the hardware. Install: `pip install dwc-sidecar[hsm]`.
  - `gcp-kms` — Google Cloud KMS with `EC_SIGN_ED25519`. Install: `pip install dwc-sidecar[gcp]`. Auth via GCP default credential chain.
  - `vault-transit` — HashiCorp Vault Transit engine (native Ed25519 support). Uses stdlib urllib — no extra install. Token via `VAULT_TOKEN` env var.
  - `azure-mhsm` — Azure Key Vault **Managed HSM** tier (`EDDSA` algorithm). Install: `pip install dwc-sidecar[azure]`. Auth via `DefaultAzureCredential`. Ed25519 is not supported on standard Key Vault, only Managed HSM.
  - `keychain` — macOS login Keychain, lightweight variant. Stores Ed25519 private bytes as a generic-password item; signing happens in-process. Stdlib only. Good for DIT-on-Mac workflows where keys should be off the filesystem but Secure Enclave is over-engineered.
  - **AWS KMS is not supported**: KMS does not offer Ed25519 keys (as of 2026). For AWS HSM-grade signing, use AWS CloudHSM through the `pkcs11` backend with the CloudHSM Client's PKCS#11 library as `module`.
  - Generate new local / file / pkcs11 / keychain keys with `dwc keygen --kid <new-kid> --backend <backend> [...]`. Cloud-backend keys (GCP, Vault, Azure) are created externally — see each module's docstring. All paths emit a `keyring.json` entry ready to paste.
- `keys.priv.json` — **dev/demo only.** Plain-text private keys on disk; assume it should not exist on a production signing host. Add it to `.gitignore` and replace with `file` or `pkcs11` backend before shipping.
- Schema `$id`s are published at `https://ns.the-dwc.com/sidecar/v0.2/...` — the active version, sourced from `src/dwc_sidecar/data/schemas/` (Cloudflare Pages, project `dwc-schemas`, from the `DigitalWorkflowCompany/Metadata-Interchange-Format` GitHub repo — `tools/publish-schemas/build.py` produces the `dist/` tree and Pages deploys on push). The published-and-frozen v0.1 schemas live byte-exact under `tools/publish-schemas/frozen/v0.1/` and keep being republished verbatim. `validate.py` resolves schemas locally by `domain` lookup, not by URL fetch; pass `--check-hosted` to additionally byte-compare each local schema against its hosted copy (URL taken from the schema's own `$id`). CI runs the same check (frozen + active) via `.github/workflows/hosted-schema-drift.yml`.

## Common commands

The package installs a single `dwc` CLI with subcommands. After `pip install -e .` from the repo root:

```bash
# Re-sign every example sidecar (clip + reel, all assets, locks, heads) —
# needed after editing example events or rotating demo keys
dwc sign-example

# Validate the stub example through all 10 stages
dwc validate

# Validate any sidecar against a production root
# (--require-keyring: fail instead of warn when keyring.json is absent —
#  use it whenever the sidecar comes from an untrusted sender)
dwc validate <sidecar.omc.json> --base-dir <production-root> [--trust-mhl] [--require-keyring]

# Validate + byte-compare local schemas against the published copies at ns.the-dwc.com
dwc validate --check-hosted

# Produce a signed sidecar from disk files (re-reads the clip)
dwc bootstrap --clip <CLIP> --mhl <MHL> --mhl-entry <path-in-mhl> \
               --amf <AMF> --fdl <FDL> --cdl <CDL> --ale <ALE> \
               --base-dir <PRODUCTION-ROOT> --out <OUT.omc.json>

# Walk a production tree and emit sidecars using the MHL's own hashes (zero clip re-read)
dwc mhl-walk <PRODUCTION-ROOT> [--out-dir <DIR>]

# Full-day batch with real hashing (slow audit mode)
dwc batch <PRODUCTION-ROOT> [--validate]

# Long-running watch-folder service
dwc watch <PRODUCTION-ROOT> --interval 2 --stable 5 \
           [--no-validate] [--quarantine-dir <DIR>]

# m-of-n threshold lock: create, then co-sign (appends signed lock events + head)
dwc lock <sidecar> --target <urn> --scope artifact \
         --policy 2 --of dwc-color-01,dwc-post-01 --signing-kid dwc-color-01 \
         [--reason "…"] [--clip-uuid …]
dwc lock cosign <sidecar> --target <urn> --signing-kid dwc-post-01   # prints "2/2 satisfied"

# Two-party custody transfer: sender offers, receiver re-verifies then counter-signs
dwc transfer offer  <sidecar> --to urn:email:io@post.com --signing-kid dwc-dit-01
dwc transfer accept <sidecar> --signing-kid dwc-post-01 --base-dir <receiving-root>

# Sealed bundle: pack everything, ship, verify with out-of-band trust
dwc bundle <sidecar> --base-dir <root> --out clip.dwcbundle.zip [--lite]
dwc verify clip.dwcbundle.zip --keyring <trusted.json>            # strongest
dwc verify clip.dwcbundle.zip --keyring-fingerprint sha256:<hex>  # pin bundled keyring
dwc verify clip.dwcbundle.zip --trust-bundled-keyring [--strict] [--allow-missing-clips]

# Generate a new signing key and print a keyring.json entry
# (--actor binds the key to an actor URN; Stage 4 then rejects events claiming any other actor)
dwc keygen --kid dwc-dit-02 --backend local [--actor urn:email:dit2@the-dwc.com]
dwc keygen --kid dwc-dit-02 --backend keychain --service dwc-sidecar     # macOS Keychain
dwc keygen --kid dwc-dit-02 --backend pkcs11 \
           --module /usr/local/lib/libykcs11.dylib --slot 0
# GCP KMS / Vault / Azure MHSM keys: create externally, then add to signers.json

# Point the runtime at a production signer config instead of keys.priv.json
export DWC_SIGNERS=/etc/dwc/signers.json

# Tests (pytest; CDL test auto-skips if DWC_CORPUS env var is unset)
pytest
DWC_CORPUS=/Volumes/DWC_Shuttle-04/WAR/260115_SD084 pytest
```

Each subcommand can also be invoked as a module for scripting: `python3 -m dwc_sidecar.validate ...`. CWD-relative files (`keyring.json`, `keys.priv.json`, `example-clip.omc.json`) are resolved against wherever you run the command from — dev workflow is `cd <repo>; dwc validate`.

## Python dependencies

Declared in `pyproject.toml`; install with `pip install -e .[dev]`:

- `jsonschema` (Draft 2020-12 validator, `FormatChecker`, `validators.extend` for `x-controlledValues`)
- `rfc8785` — JCS canonicalisation
- `cryptography` — Ed25519 primitives
- `xxhash`, `blake3`, `pyyaml` — hash algs + MHL v2
- `pytest` (dev extra) — test runner

## Conventions a future instance should follow

1. **Never add top-level fields to an OMC Asset.** They will fail Stage 1 due to `unevaluatedProperties: false`. Put extensions in `assetFC.functionalProperties.customData[]` with a `dwc.*` domain.
2. **Warnings vs. errors.** CDL divergence is a *warning* (Stage 9) because production workflows legitimately carry independent CDL records (on-set vs post). Don't escalate it to a failure without evidence.
3. **Filenames and collisions.** `dwc_sidecar/watch.py` keys the processed-set on MHL sha256 and on clip-integrity hash; writing a collision resolver that just suffixes by timestamp will silently mask a genuine hash disagreement. Preserve the hash-prefix convention.
4. **Schema URL stability.** Published schema versions are immutable. The active version is v0.2 (`src/dwc_sidecar/data/schemas/`, `$id`s under `ns.the-dwc.com/sidecar/v0.2/`); the published v0.1 bytes are frozen at `tools/publish-schemas/frozen/v0.1/` and stay hosted at their original URLs indefinitely. Any change to the active schemas — additive or breaking — must bump the version in the `$id` (→ `v0.3/`), freeze the previous version's bytes under `tools/publish-schemas/frozen/`, and leave old URLs untouched. Before publication a version's URLs are editable; after publication they are frozen, and the CI drift-check (`.github/workflows/hosted-schema-drift.yml`) will fail any PR that mutates a hosted file.
5. **Leaf-element truthiness.** `xml.etree.ElementTree.Element.__bool__` returns `False` for leaf elements (no children). Use `elem is None` checks, never `elem or fallback`. See `dwc_sidecar/cdl.py:_find_any`.
6. **MHL path resolution.** Paths inside an MHL are relative to the MHL file's own directory, not the production root. Stage 8 and `dwc_sidecar/mhl_walker.py` both depend on this — preserve it if touching path handling.
7. **DWC field-name convention: underscores, always.** Any DWC-prefixed identifier exposed to an external tool — ALE column headers, Silverstack custom metadata keys, Resolve `MediaPoolItem:SetMetadata` keys, or any future transport — uses the form `DWC_Signed`, never `DWC-Signed` or `DWC.Signed`. Sister tools in `~/Documents/Resolve-Tools/` use hyphens (`AMF-Name`, `FDL-Name`); the DWC convention deliberately diverges. Do not "normalise" it.

## What the stub data is for

Files under `Camera/`, `amf/`, `fdl/`, `resolve/`, `proxy/`, `delivery/` (repo root) are hand-crafted minimum-viable examples that make `example-clip.omc.json` and `example-reel.omc.json` validate end-to-end with no external dependencies. They exist purely to keep `dwc validate` self-contained when run from the repo root; they are not representative of real production bytes and are not shipped in the wheel.

Real production data has been tested against `/Volumes/DWC_Shuttle-04/WAR/260115_SD084` (Sony VENICE, 40 clips, MHL v1 XML with `xxhash64be`, AMF v2.0, FDL v2.0, ASC CDL v1.2). That shoot remains the reference corpus for any design change.

## External references

- **Pomfort Silverstack Scripting** — Lua 5.5.0 API shipped in Silverstack 9.2.0 (SDK v1.0 released 2026-04-15). Docs: <https://github.com/pomfort/silverstack-scripting>. Overview: <https://pomfort.com/article/how-to-use-silverstacks-scripting-feature/>. Relevant hooks for DWC integration: `onStampVideo` / `onStampAudio` (per-clip during ingest) and `onFinish` (after job completion). Supersedes the earlier assumption that Silverstack has no scriptable surface — that claim in `plans/phase-02.md` §1.1 predates the 9.2.0 release and should be treated as outdated when revisiting Silverstack integration.
- **DaVinci Resolve Scripting API** — Python + Lua. Vendor READMEs committed at `resources/documentation/DaVinciResolve21_Scripting_README.txt` (Resolve 21, dated 2025-10-07) and `resources/documentation/DaVinciResolve20_Scripting_README.txt` (Resolve 20, dated 2025-08-18). Relevant entry points for DWC metadata interchange: `MediaPoolItem:SetMetadata({dict})` (built-in namespace) and `MediaPoolItem:SetThirdPartyMetadata({dict})` (separate third-party namespace — preferred for `DWC_*` fields to avoid colliding with Resolve's built-in metadata keys). `GetMediaId()` / `GetUniqueId()` provide stable per-clip identifiers for matching sidecars. No per-clip ingest hook exists — integration is either a script under `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/` invoked from Workspace → Scripts, or an external Python process driving Resolve through `RESOLVE_SCRIPT_LIB`. This is an alternative (or complement) to the ALE import path already in `plans/phase-02.md` §1 for Resolve specifically.
