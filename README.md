# dwc-sidecar

> Per-clip film-industry metadata sidecar format — composes with MovieLabs OMC v2.8.

A JSON document that references (never duplicates) AMF, ASC MHL, ASC FDL, ASC CDL, and DaVinci Resolve exports, carries an Ed25519-signed append-only provenance log, and verifies end-to-end through a nine-stage validator. Nothing at the top level is DWC-specific — all DWC fields live under OMC's documented `customData` extension point, so a DWC sidecar is still a valid OMC asset.

The design principle that governs everything: **reference canonical files by content hash, carry cryptographic provenance above them, and never re-invent what OMC already defines.**

## Install

```bash
pipx install dwc-sidecar
dwc init
```

`dwc init` walks you through generating a signing key (macOS Keychain or file-backed), writes `keyring.json` + `signers.json`, and installs a LaunchAgent (macOS) or systemd user unit (Linux) so `dwc watch` starts at login.

## Subcommands

| Command         | What it does                                                          |
|-----------------|-----------------------------------------------------------------------|
| `dwc init`      | One-command onboarding (key + keyring + signers + launch unit)        |
| `dwc validate`  | Validate a sidecar through 9 stages                                   |
| `dwc watch`     | Long-running watch-folder service; emits sidecars as clips arrive     |
| `dwc mhl-walk`  | Walk a production tree, lift hashes from the MHL (~900 sidecars/sec)  |
| `dwc batch`     | Re-hash clips from disk (audit mode, ~450 MB/s)                       |
| `dwc bootstrap` | Produce one signed sidecar from disk files                            |
| `dwc keygen`    | Generate a new Ed25519 key in any supported backend                   |

Run `dwc --help` for the full list; `dwc <cmd> --help` for per-command flags.

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

- **Schemas**: <https://ns.the-dwc.com/sidecar/v0.1/> (immutable, per-version)
- **Engineering notes**: [`CLAUDE.md`](CLAUDE.md) — architecture, the 9 validator stages, conventions a contributor should follow
- **OMC v2.8**: MovieLabs Ontology for Media Creation — the upstream envelope

## Development

```bash
pip install -e .[dev]
pytest
```

The real-corpus reference (Sony VENICE, 40 clips, ASC MHL v1 with `xxhash64be`, AMF v2.0, FDL v2.0, ASC CDL v1.2) and full architectural context live in `CLAUDE.md`.
