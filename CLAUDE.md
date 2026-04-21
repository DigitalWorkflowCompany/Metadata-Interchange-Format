# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A reference implementation of a per-clip film-industry metadata sidecar format that **composes with MovieLabs OMC v2.8** rather than replacing it. The sidecar is a JSON document that references (never duplicates) AMF, ASC MHL, ASC FDL, ASC CDL, and DaVinci Resolve exports, carries a signed append-only provenance log, and verifies end-to-end through a nine-stage validator.

The design principle that governs everything: **reference canonical files by content hash, carry cryptographic provenance above them, and never re-invent what OMC already defines.**

## Architecture

### Layering

```
OMC v2.8 JSON Schema (upstream, unmodified)         ← envelope
    │
    └─ assetFC.functionalProperties.customData[]    ← OMC extension point
            │
            ├─ dwc.sidecar.artifacts                ← hashes of referenced files
            ├─ dwc.sidecar.events                   ← Ed25519-signed, hash-chained log
            └─ dwc.sidecar.locks                    ← derived view of 'lock' events
```

All DWC-specific payload lives under OMC's documented `customData` array. Nothing is added at the top level — OMC uses `unevaluatedProperties: false`, so top-level extensions would fail validation.

### The nine validation stages (`dwc_sidecar/validate.py`)

Each stage is a function returning an error count; their sum is the exit code. Stages are independent and idempotent. Know what each attests before changing validator code:

1. **OMC v2.8 structure** — upstream JSON Schema
2. **DWC payload schemas** — `src/dwc_sidecar/data/schemas/{artifacts,events,locks}.schema.json`
3. **Event chain continuity** — `seq` monotonic, `prevHash` links
4. **Ed25519 signatures + key validity** — window + CRL-style revocation
5. **Lock ↔ signed event crosscheck** — every lock has a matching signed event
6. **Artifact file integrity** — declared hashes match bytes on disk (supports `--trust-mhl` to skip when an MHL in the same sidecar already attests)
7. **`x-controlledValues` enforcement** — treated as `enum` via jsonschema extension
8. **MHL inner consistency** — re-hash files the MHL points to against its own declarations
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

- `keyring.json` — publishable Ed25519 public keys with `validFrom` / `validUntil` / `revokedAt`
- `keys.priv.json` — demo private keys; must never be committed in a real deployment
- `revocations.json` (optional) — CRL-style, overrides keyring at validation time
- Schema `$id`s are published at `https://ns.the-dwc.com/sidecar/v0.1/...` (Cloudflare Pages, project `dwc-schemas`, sourced from the `DigitalWorkflowCompany/Metadata-Interchange-Format` GitHub repo — `tools/publish-schemas/build.py` produces the `dist/` tree and Pages deploys on push). `validate.py` resolves schemas locally by `domain` lookup, not by URL fetch; pass `--check-hosted` to additionally byte-compare each local schema against its hosted copy. CI runs the same check via `.github/workflows/hosted-schema-drift.yml`.

## Common commands

The package installs a single `dwc` CLI with subcommands. After `pip install -e .` from the repo root:

```bash
# Re-sign the stub example events (only needed after editing events)
dwc sign-example

# Validate the stub example through all 9 stages
dwc validate

# Validate any sidecar against a production root
dwc validate <sidecar.omc.json> --base-dir <production-root> [--trust-mhl]

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
4. **Schema URL stability.** The v0.1 schemas are published at `ns.the-dwc.com/sidecar/v0.1/` and their bytes are immutable. Any change — additive or breaking — must bump the version in the `$id` (→ `v0.2/`) and leave the old schema untouched at its original URL. Old versions remain hosted indefinitely. Before publication a version's URLs are editable; after publication they are frozen, and the CI drift-check (`.github/workflows/hosted-schema-drift.yml`) will fail any PR that mutates a hosted file.
5. **Leaf-element truthiness.** `xml.etree.ElementTree.Element.__bool__` returns `False` for leaf elements (no children). Use `elem is None` checks, never `elem or fallback`. See `dwc_sidecar/cdl.py:_find_any`.
6. **MHL path resolution.** Paths inside an MHL are relative to the MHL file's own directory, not the production root. Stage 8 and `dwc_sidecar/mhl_walker.py` both depend on this — preserve it if touching path handling.

## What the stub data is for

Files under `Camera/`, `amf/`, `fdl/`, `resolve/`, `proxy/`, `delivery/` (repo root) are hand-crafted minimum-viable examples that make `example-clip.omc.json` and `example-reel.omc.json` validate end-to-end with no external dependencies. They exist purely to keep `dwc validate` self-contained when run from the repo root; they are not representative of real production bytes and are not shipped in the wheel.

Real production data has been tested against `/Volumes/DWC_Shuttle-04/WAR/260115_SD084` (Sony VENICE, 40 clips, MHL v1 XML with `xxhash64be`, AMF v2.0, FDL v2.0, ASC CDL v1.2). That shoot remains the reference corpus for any design change.
