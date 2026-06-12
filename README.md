# dwc-sidecar

> Per-clip film-industry metadata sidecar format — composes with MovieLabs OMC v2.8.

A JSON document that references (never duplicates) AMF, ASC MHL, ASC FDL, ASC CDL, and DaVinci Resolve exports, carries an Ed25519-signed append-only provenance log, and verifies end-to-end through a ten-stage validator. Nothing at the top level is DWC-specific — all DWC fields live under OMC's documented `customData` extension point, so a DWC sidecar is still a valid OMC asset.

The design principle that governs everything: **reference canonical files by content hash, carry cryptographic provenance above them, and never re-invent what OMC already defines.**

## Install

```bash
pipx install dwc-sidecar
dwc init
```

`dwc init` walks you through generating a signing key (macOS Keychain or file-backed), writes `keyring.json` + `signers.json`, and installs a LaunchAgent (macOS) or systemd user unit (Linux) so `dwc watch` starts at login.

For a from-scratch walk-through with prerequisites, expected `dwc doctor` output, and "where to go next" pointers, see [`docs/quickstart.md`](docs/quickstart.md).

## Subcommands

| Command         | What it does                                                          |
|-----------------|-----------------------------------------------------------------------|
| `dwc init`      | One-command onboarding (key + keyring + signers + launch unit)        |
| `dwc validate`  | Validate a sidecar through 10 stages                                   |
| `dwc watch`     | Long-running watch-folder service; emits sidecars as clips arrive     |
| `dwc mhl-walk`  | Walk a production tree, lift hashes from the MHL (~900 sidecars/sec)  |
| `dwc batch`     | Re-hash clips from disk (audit mode, ~450 MB/s)                       |
| `dwc bootstrap` | Produce one signed sidecar from disk files                            |
| `dwc lock`      | Create / co-sign an m-of-n threshold lock (`dwc lock cosign`)         |
| `dwc transfer`  | Two-party custody hand-off (`transfer offer` / `transfer accept`)     |
| `dwc bundle`    | Pack a sidecar + keyring + files into a sealed `.dwcbundle.zip`       |
| `dwc verify`    | Verify a sealed bundle end-to-end (never resolves CWD files)          |
| `dwc keygen`    | Generate a new Ed25519 key in any supported backend (`--actor` binds) |

Run `dwc --help` for the full list; `dwc <cmd> --help` for per-command flags.

### Multi-party trust (v0.6)

Three features turn the format from "one signer attests" into "multiple parties attest, and a recipient can check it with nothing but the bundle in hand":

- **m-of-n threshold locks** — a lock can require co-signatures from multiple roles (`--policy 2 --of dwc-color-01,dwc-post-01`). Every co-signature covers the policy itself, so editing `m`/`of` invalidates all of them. A verifier-side `policies.lock` minimum in `keyring.json` blocks policy-downgrade.
- **Two-party transfer counter-signing** — `dwc transfer offer` records a sender-signed hand-off; `dwc transfer accept` re-verifies the files at the receiving site and *then* counter-signs, so the acceptance signature attests a successful verification, not just receipt.
- **Sealed-bundle verify** — `dwc bundle` packs everything into one zip; `dwc verify` extracts it zip-slip-safe, never picks up CWD files, never fails open, and demands an explicit out-of-band trust decision for the keyring (external / fingerprint-pinned / `--trust-bundled-keyring`).

See [`docs/operations/transfer.md`](docs/operations/transfer.md) for the facility-to-facility workflow.

## Signer backends

Production key material is held by a backend, never inlined. Configure per-kid via `DWC_SIGNERS=/path/to/signers.json`.

| Backend         | Notes                                                                 |
|-----------------|-----------------------------------------------------------------------|
| `local`, `file` | Dev / portable — private key in a JSON file                           |
| `keychain`      | macOS Keychain, stdlib only — good for DIT carts                      |
| `pkcs11`        | Any PKCS#11 v3.0 token (YubiHSM, Nitrokey, AWS CloudHSM, Thales, etc.) |
| `gcp-kms`       | Google Cloud KMS `EC_SIGN_ED25519`                                    |
| `vault-transit` | HashiCorp Vault Transit engine                                        |
| `azure-mhsm`    | Azure Key Vault **Managed HSM** tier (Ed25519 via EDDSA)              |

AWS KMS is not supported — it does not offer Ed25519 keys (as of 2026). For AWS HSM-grade signing, use AWS CloudHSM through the `pkcs11` backend.

## References

- **Schemas**: <https://ns.the-dwc.com/sidecar/> (immutable, per-version; v0.2 active)
- **Engineering notes**: [`CLAUDE.md`](CLAUDE.md) — architecture, the 9 validator stages, conventions a contributor should follow
- **OMC v2.8**: MovieLabs Ontology for Media Creation — the upstream envelope

## Development

```bash
pip install -e .[dev]
pytest
```

The real-corpus reference (Sony VENICE, 40 clips, ASC MHL v1 with `xxhash64be`, AMF v2.0, FDL v2.0, ASC CDL v1.2) and full architectural context live in `CLAUDE.md`.
