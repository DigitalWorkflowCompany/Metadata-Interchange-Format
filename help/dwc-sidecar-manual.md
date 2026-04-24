# `dwc-sidecar` — User Manual

_Version 0.1 · Reference implementation of the DWC per-clip metadata sidecar format._

---

## Table of contents

1. [What `dwc-sidecar` is](#1-what-dwc-sidecar-is)
2. [Why it exists](#2-why-it-exists)
3. [How the sidecar is structured](#3-how-the-sidecar-is-structured)
4. [Installation](#4-installation)
5. [First-run sanity check](#5-first-run-sanity-check)
6. [Keys and signing](#6-keys-and-signing)
7. [The `dwc` CLI — subcommand reference](#7-the-dwc-cli--subcommand-reference)
8. [Typical production workflows](#8-typical-production-workflows)
9. [The nine validation stages](#9-the-nine-validation-stages)
10. [Troubleshooting](#10-troubleshooting)
11. [Glossary](#11-glossary)

---

## 1. What `dwc-sidecar` is

`dwc-sidecar` is a small Python package that produces and verifies a **per-clip JSON "sidecar" file** for film and television productions. Each sidecar:

- sits alongside one camera clip,
- **references** (never duplicates) the metadata files that clip already has — AMF, ASC MHL, ASC FDL, ASC CDL, ALE, DaVinci Resolve exports — by their content hash,
- carries a **signed, append-only event log** (Ed25519 signatures, hash-chained) that records who touched the clip, when, and why,
- validates end-to-end through a nine-stage check that covers schema, signatures, key validity, hash integrity, and internal consistency of the referenced files.

The sidecar is a plain JSON document built as an extension of **MovieLabs OMC v2.8** (the industry-standard Ontology for Media Creation). Anything that already speaks OMC can read a DWC sidecar without modification; DWC-specific payload lives under OMC's documented `customData` extension point.

> **Design principle:** reference canonical files by content hash, carry cryptographic provenance above them, and never re-invent what OMC already defines.

## 2. Why it exists

A modern shoot already produces an MHL, an AMF, possibly an FDL, a CDL, an ALE, and a Resolve project — but none of those files know about each other, none are signed, and none answer the question _"is this still the file the DIT handed over?"_ once the drive has crossed three vendors.

The sidecar makes every clip:

- **Discoverable** — one JSON document per clip that points at every related artefact.
- **Verifiable** — content hashes let anyone in the chain confirm bytes have not changed.
- **Tamper-evident** — a chained, signed event log means a lost or forged entry is detectable.
- **Vendor-neutral** — built on MovieLabs OMC, so no one has to adopt a proprietary format.

Production benefits:

| Stage | Benefit |
|---|---|
| On set / DIT | One command emits a sidecar per clip. MHL hashes are reused, so there's no re-read of the camera card. |
| Transfer | Drives carry sidecars alongside the footage. A receiving vendor can verify the whole delivery with one command. |
| Post | Colour, VFX, editorial each append signed events (`lock`, `approved-for-vfx`, `conformed`, …). The history grows; earlier entries stay verifiable. |
| QC / Delivery | A single `dwc validate` exit code tells you whether the package is internally consistent. |

## 3. How the sidecar is structured

```
OMC v2.8 Asset JSON (envelope, unmodified)
 └─ assetFC.functionalProperties.customData[]   ← OMC extension point
      ├─ dwc.sidecar.artifacts   — declared hashes of referenced files
      ├─ dwc.sidecar.events      — Ed25519-signed, hash-chained log
      └─ dwc.sidecar.locks       — derived view of 'lock' events
```

Nothing is added at the top level of the OMC asset (OMC sets `unevaluatedProperties: false`, so top-level extensions would fail validation). All DWC payload is namespaced under `dwc.*`.

- **Artifacts** list each referenced file by `domain` (e.g. `amf`, `mhl`, `fdl`, `cdl`, `ale`, `clip`), a `uri` relative to the production root, an algorithm name (`sha256`, `xxh64`, `blake3`, `c4`, …), and the hash value.
- **Events** are small JSON objects with `seq`, `prevHash`, `actor`, `kid` (signing key id), `body`, and `sig`. The hash is computed over the RFC 8785 canonical form of the event body; the signature is Ed25519 over those same canonical bytes.
- **Locks** are a convenience projection — `lock`-type events rolled up into the state they currently declare (e.g. "colour-lock at event 7 by kid `dwc-colorist-01`").

## 4. Installation

### 4.1 Requirements

- **Python 3.10+** (3.11 and 3.12 are also supported)
- **pip**
- macOS, Linux, or Windows. The `watch` daemon and the `keychain` signer backend are macOS-oriented; everything else is cross-platform.

Check what you have:

```bash
python3 --version        # must be 3.10.x or newer
pip --version
```

If your system Python is too old, install a modern one via Homebrew (`brew install python@3.12`), `pyenv`, or the installer from python.org.

### 4.2 Install from the repository

There is no PyPI release yet. Install from the Git checkout:

```bash
git clone https://github.com/DigitalWorkflowCompany/Metadata-Interchange-Format.git
cd Metadata-Interchange-Format

# Recommended: isolate into a virtualenv
python3 -m venv .venv
source .venv/bin/activate                  # Windows: .venv\Scripts\activate

# Editable install — a 'git pull' is enough to upgrade afterwards
pip install -e .
```

That puts a `dwc` command on your `PATH`.

### 4.3 Optional signer backends

The core install only bundles the dev-mode (local-file) signer. For production signers, add the relevant extra:

```bash
pip install -e ".[hsm]"       # PKCS#11 — YubiHSM 2, Nitrokey, AWS CloudHSM, SoftHSM, Thales, nShield
pip install -e ".[gcp]"       # Google Cloud KMS (EC_SIGN_ED25519)
pip install -e ".[azure]"     # Azure Key Vault Managed HSM (EDDSA)
pip install -e ".[all]"       # every cloud / HSM backend at once
pip install -e ".[dev]"       # pytest — for the test suite
```

The `vault-transit` (HashiCorp Vault) and `keychain` (macOS login Keychain) backends use the standard library only — no extra install is required.

> **AWS KMS is not supported** — KMS does not offer Ed25519 keys. For AWS HSM-grade signing, use **AWS CloudHSM** through the `pkcs11` backend.

### 4.4 Upgrading

Because the install is editable, a pull is enough:

```bash
cd Metadata-Interchange-Format
git pull
# Re-run  pip install -e .  only if pyproject.toml changed
```

## 5. First-run sanity check

From the repo root:

```bash
dwc --help         # lists all subcommands
dwc validate       # should exit 0 — validates the bundled example-clip.omc.json through all 9 stages
pytest             # optional, needs pip install -e ".[dev]"
```

`dwc validate` resolves its example inputs (`example-clip.omc.json`, `keyring.json`, `keys.priv.json`) relative to the **current working directory**, so run it from inside the repo the first time.

## 6. Keys and signing

Every event in the sidecar's log is signed with an Ed25519 key. The tool separates **public trust material**, which ships with the project, from **private key material**, which never should.

### 6.1 Trust material (safe to publish)

- `keyring.json` — list of signers with their public key, `kid` (key id), `validFrom`, `validUntil`, optional `revokedAt`. Required at validation time. Checked into the project so anyone can verify signatures.
- `revocations.json` (optional) — CRL-style revocation list, overrides `keyring.json` at validation time.

### 6.2 Private key material (must stay out of Git)

`dwc-sidecar` never inlines private keys in the sidecar. Signing goes through a **backend** chosen per-`kid`. The backend is selected by a config file pointed to by the `DWC_SIGNERS` environment variable; if unset, the CLI falls back to `JsonFileSigner` reading `keys.priv.json` in the current directory — fine for demos, **never for a real shoot**.

| Backend | When to use | Install |
|---|---|---|
| `local` | Dev / demo | (default) |
| `file` | Docker / Kubernetes secret mount, external volume | stdlib |
| `keychain` | macOS DIT carts — keys live in the login Keychain | stdlib |
| `pkcs11` | YubiHSM 2, Nitrokey, AWS CloudHSM, SoftHSM, Thales, nShield | `pip install dwc-sidecar[hsm]` |
| `gcp-kms` | Google Cloud KMS (`EC_SIGN_ED25519`) | `pip install dwc-sidecar[gcp]` |
| `vault-transit` | HashiCorp Vault Transit engine | stdlib (uses `VAULT_TOKEN`) |
| `azure-mhsm` | Azure Key Vault Managed HSM tier | `pip install dwc-sidecar[azure]` |

### 6.3 Generating keys

```bash
# Dev / demo
dwc keygen --kid dwc-dit-01 --backend local

# macOS — keys off the filesystem, in the login Keychain
dwc keygen --kid dwc-dit-01 --backend keychain --service dwc-sidecar

# Hardware token or CloudHSM
dwc keygen --kid dwc-dit-01 --backend pkcs11 \
           --module /usr/local/lib/libykcs11.dylib --slot 0
```

`dwc keygen` prints a ready-to-paste `keyring.json` entry. Paste it into the project's `keyring.json` and commit **only that file**.

Cloud-backend keys (GCP KMS, Vault, Azure MHSM) are created in their respective consoles, not through `dwc keygen`. See each backend module's docstring for the exact steps.

### 6.4 Pointing the CLI at a production signer config

```bash
export DWC_SIGNERS=/etc/dwc/signers.json
```

Example `signers.json` shape is in `src/dwc_sidecar/signers/__init__.py` — one entry per `kid`, mapping it to a backend and the backend's parameters (module path, slot, key handle, GCP resource name, Vault key name, etc.).

## 7. The `dwc` CLI — subcommand reference

`dwc --help` lists everything. Each subcommand also accepts `--help` for its own flags.

### 7.1 `dwc validate` — verify a sidecar

```bash
# Validate the bundled example (uses keys/keyring in CWD)
dwc validate

# Validate a real sidecar against a production tree
dwc validate path/to/clipname.omc.json --base-dir /Volumes/Shuttle-04/SHOOT

# Skip clip-byte re-hashing when the MHL in the sidecar already attests those hashes
dwc validate clipname.omc.json --base-dir /Volumes/Shuttle-04/SHOOT --trust-mhl

# Also byte-compare local schema copies against the published schemas at ns.the-dwc.com
dwc validate --check-hosted
```

Exits with a non-zero code equal to the total number of errors across all nine stages. Stage 9 (CDL divergence) only produces warnings — it never contributes to the exit code.

### 7.2 `dwc bootstrap` — build a sidecar from disk files (slow)

Re-reads the clip bytes to compute the clip-integrity hash. Use for one-offs.

```bash
dwc bootstrap \
  --clip  Camera/A001C001.mxf \
  --mhl   Camera/A001.mhl \
  --mhl-entry Camera/A001C001.mxf \
  --amf   amf/A001C001.amf \
  --fdl   fdl/ProductionA.fdl \
  --cdl   cdl/A001C001.cdl \
  --ale   Camera/A001.ale \
  --base-dir /Volumes/Shuttle-04/SHOOT \
  --out   sidecars/A001C001.omc.json
```

### 7.3 `dwc batch` — build sidecars for a whole tree, the slow way

```bash
dwc batch /Volumes/Shuttle-04/SHOOT [--validate]
```

Re-hashes every clip — **~450 MB/s, bounded by source-disk sequential read**. Use as a periodic audit, not for every-shoot-day ingest.

### 7.4 `dwc mhl-walk` — the production path (fast)

Lifts the clip-integrity hash directly from the MHL the DIT tool already wrote. ~900 sidecars/sec, effectively constant-time per clip.

```bash
dwc mhl-walk /Volumes/Shuttle-04/SHOOT --out-dir ./sidecars
```

**This is the workflow you should be using.** `dwc batch` is only for audit runs.

### 7.5 `dwc watch` — long-running ingest daemon

Wraps `mhl-walk` with folder polling, stability detection, dedup by MHL sha256, collision handling (`REFRESH` vs `CONFLICT`), optional post-emit validation, a quarantine directory for failures, and `.watch-state.json` for restart-safe resumption.

```bash
dwc watch /Volumes/Shuttle-04/SHOOT \
  --interval 2 \
  --stable   5 \
  [--no-validate] \
  [--quarantine-dir ./quarantine]
```

Typical use: start it on the DIT cart at load-in; it emits a sidecar per clip as soon as the MHL that covers it stops changing.

### 7.6 `dwc keygen` — generate a signing key

See [§6.3](#63-generating-keys).

### 7.7 `dwc sign-example` — regenerate the demo

Rewrites `keys.priv.json`, `keyring.json`, and the events inside `example-clip.omc.json` so the bundled example validates after you've changed the events payload. Only needed if you're modifying the reference sidecar in the repo.

Every subcommand can also be invoked as a module, which is handy for scripting:

```bash
python3 -m dwc_sidecar.validate path/to/sidecar.omc.json --base-dir /mnt/shoot
```

## 8. Typical production workflows

### 8.1 On-set / near-set (DIT cart)

1. **Offload** camera cards with your existing DIT tool. Your tool writes the MHL as usual.
2. **Run the watch daemon** on the cart:
   ```bash
   export DWC_SIGNERS=/etc/dwc/signers.json   # key material stays on this host
   dwc watch /Volumes/Shuttle-04/SHOOT --interval 2 --stable 5
   ```
3. Sidecars appear next to the clips as each MHL stabilises. Validation failures (if any) land in the quarantine directory.
4. **Hand the drive to post.** The sidecars travel with the footage; nothing is embedded in the media files themselves.

### 8.2 Colour / editorial

1. Receive the drive, mount it, and run a verification pass:
   ```bash
   for f in sidecars/*.omc.json; do
     dwc validate "$f" --base-dir /Volumes/Shuttle-04/SHOOT --trust-mhl || echo "FAIL: $f"
   done
   ```
2. Use the sidecar's `dwc.sidecar.artifacts` list to find the canonical AMF / CDL / FDL for each clip (they're referenced by hash, so you know you've got the right versions).
3. When you **lock** a look, sign and append a new event with your colour key (`kid`). The sidecar's event chain grows by one entry; earlier entries remain verifiable.

### 8.3 Delivery / QC

```bash
# Pre-master check — fast, trusts the MHL for clip hashes
dwc validate master.omc.json --base-dir /Volumes/Deliverables --trust-mhl

# Full audit — re-hashes everything on disk
dwc validate master.omc.json --base-dir /Volumes/Deliverables
```

Exit code 0 = clean. Non-zero = number of errors; stderr names the failing stage.

### 8.4 Periodic audit

Once a week (or before a long-term archive), run the expensive path:

```bash
dwc batch /Volumes/LTO-Mirror/SHOOT --validate
```

Every byte gets re-read. Any divergence from the sidecar's declared hashes surfaces as a Stage 6 failure.

## 9. The nine validation stages

`dwc validate` runs these in order. Each is independent and idempotent; their error counts sum to the exit code.

| # | Stage | What it attests |
|---|---|---|
| 1 | **OMC v2.8 structure** | The envelope conforms to the upstream MovieLabs OMC JSON Schema. |
| 2 | **DWC payload schemas** | `artifacts`, `events`, and `locks` conform to the schemas in `src/dwc_sidecar/data/schemas/`. |
| 3 | **Event chain continuity** | `seq` is monotonic and `prevHash` links every event to its predecessor. |
| 4 | **Ed25519 signatures + key validity** | Every signature verifies; every signing key is within its `validFrom`/`validUntil` window and is not revoked. |
| 5 | **Lock ↔ signed-event crosscheck** | Every entry in `dwc.sidecar.locks` has a matching signed event. |
| 6 | **Artifact file integrity** | Declared hashes match the bytes on disk. `--trust-mhl` skips this when an MHL in the same sidecar already attests the file. |
| 7 | **`x-controlledValues` enforcement** | Values drawn from a controlled vocabulary are treated as `enum` via a `jsonschema` extension. |
| 8 | **MHL inner consistency** | Re-hashes files the MHL points to against its own declarations. |
| 9 | **CDL consistency (warning only)** | Compares a standalone CDL against AMF-embedded `lookTransform`s. Divergence logs `WARN` but never fails validation — on-set and post-CDL records legitimately diverge. |

## 10. Troubleshooting

**`dwc: command not found`**
Your virtualenv is probably not active. `source .venv/bin/activate` inside the repo, or use the full path `/path/to/.venv/bin/dwc`.

**`ModuleNotFoundError: No module named 'dwc_sidecar'`**
You installed into a different Python than the one on your `PATH`. Check `which python3`, `which dwc`, and `pip show dwc-sidecar`.

**Stage 4: "key `<kid>` not in keyring"**
The signer's public key isn't listed in `keyring.json`. Run `dwc keygen --kid <kid> --backend …` on the signing host and paste the emitted entry into the project's `keyring.json`.

**Stage 4: "signature expired"**
The event was signed after the key's `validUntil`, or with a revoked key. Rotate the key, re-sign new events, and if this was an honest mistake, remove or correct the revocation entry.

**Stage 6: "hash mismatch for `amf/…`"**
The file on disk is not the one the sidecar was built from. Either the wrong file is under `--base-dir`, or the file has been modified. `dwc batch --validate` is the heavyweight way to find all such cases across a tree.

**Stage 8: "MHL declares file X but rehash differs"**
The MHL and the bytes disagree. This usually means the tree was modified after the MHL was written. Re-run the DIT tool that produced the MHL, or re-emit the sidecar.

**Stage 9: CDL warning**
Not a failure. Means your standalone CDL and the AMF-embedded `lookTransform` don't agree. Confirm whether this is intentional (on-set CDL vs post CDL) and carry on.

**`dwc watch` keeps re-emitting the same sidecar**
Check that the MHL is actually stable — `--stable 5` waits 5 seconds of no modification before treating the MHL as settled. If your filesystem's mtime granularity is coarse, raise the number.

**Hosted-schema drift failure (`--check-hosted`)**
The local schema in `src/dwc_sidecar/data/schemas/` no longer byte-matches the published copy at `ns.the-dwc.com`. Either your checkout is stale or the schema has been edited locally. Schema URLs are **version-frozen** after publication — any change requires a new version directory (`v0.2/`), not a mutation of `v0.1/`.

## 11. Glossary

- **OMC v2.8** — MovieLabs' Ontology for Media Creation, the JSON envelope format the sidecar extends.
- **AMF** — ACES Metadata File. Carries colour-pipeline configuration.
- **MHL** — Media Hash List (ASC). An authenticated inventory of files and their hashes.
- **FDL** — Framing Decision List (ASC). Records framing intent across formats.
- **CDL** — Colour Decision List (ASC). Lift / gamma / gain / saturation values for a look.
- **ALE** — Avid Log Exchange. Shot-log metadata from Avid-oriented workflows.
- **Ed25519** — elliptic-curve signature scheme used for every event signature.
- **JCS (RFC 8785)** — JSON Canonicalisation Scheme. Deterministic byte form of a JSON value, so signatures are reproducible.
- **`kid`** — Key identifier. A short string naming a signing key in `keyring.json`.
- **Sidecar** — the per-clip `*.omc.json` document `dwc-sidecar` produces.
- **Production root** — the top-level directory of a shoot. All `uri`s inside a sidecar are relative to it.

---

_For the design rationale and architectural conventions that govern future changes, see `CLAUDE.md` at the repo root._
