# Signer backends

DWC sidecars are signed with Ed25519. Production private-key material is held by a *backend*, never inlined in this repo or in `keys.priv.json` on a production host. Seven backends are supported, picked per-kid through a `signers.json` config.

```
DWC_SIGNERS=/path/to/signers.json   # env var the runtime reads
```

If `DWC_SIGNERS` is unset, every kid resolves to `JsonFileSigner` reading `./keys.priv.json` — the dev default. **That's not a production posture.** `dwc doctor` Check 6 warns when `DWC_SIGNERS` is unset; Check 8 warns when `keys.priv.json` is present anywhere on a production host.

## Backend picker

| Backend         | When to use                                                                    | Secret at rest where?                  |
|-----------------|--------------------------------------------------------------------------------|----------------------------------------|
| `local`         | Local development only.                                                        | `./keys.priv.json` (plaintext)         |
| `file`          | Containers / CI; mount a secret as a file at a non-CWD path.                   | At `path` (plaintext)                  |
| `keychain`      | macOS DIT carts. Lightweight, no extra hardware, off the filesystem.           | macOS login Keychain (encrypted at rest) |
| `pkcs11`        | Hardware-grade signing on YubiHSM 2 / Nitrokey / SoftHSM / Thales / AWS CloudHSM. | Inside the HSM token; never leaves     |
| `gcp-kms`       | GCP-resident keys (`EC_SIGN_ED25519`).                                         | Google Cloud KMS                        |
| `vault-transit` | HashiCorp Vault deployments with Transit engine.                               | Vault                                   |
| `azure-mhsm`    | Azure Managed HSM tier (`EDDSA`).                                              | Azure Managed HSM                        |

**AWS KMS is not supported** — it does not offer Ed25519 keys (as of 2026). For AWS HSM-grade signing, use AWS CloudHSM via the `pkcs11` backend with the CloudHSM Client's PKCS#11 library as `module`.

## `signers.json` shape

The full schema, with all seven backends populated for illustration:

```json
{
  "dwc-dev-01":     { "type": "local" },
  "dwc-staging":    { "type": "file", "path": "/run/secrets/staging-key.json" },
  "dwc-dit-01":     { "type": "keychain", "service": "dwc-sidecar" },
  "dwc-dit-02":     { "type": "pkcs11",
                      "module": "/usr/local/lib/libykcs11.dylib",
                      "slot":   0,
                      "label":  "dwc-dit-02" },
  "dwc-color-01":   { "type": "gcp-kms",
                      "key_version": "projects/p/locations/eu-west2/keyRings/r/cryptoKeys/k/cryptoKeyVersions/1" },
  "dwc-post-01":    { "type": "vault-transit",
                      "url": "https://vault.example.com:8200",
                      "key_name": "dwc-post-01" },
  "dwc-audit-01":   { "type": "azure-mhsm",
                      "key_id": "https://myhsm.managedhsm.azure.net/keys/audit-01/abc" }
}
```

Each kid gets one backend. The runtime resolves a kid → backend at sign time; mismatching keyring kids and `signers.json` keys is a `dwc doctor` Check 6 FAIL.

---

## `local` — development only

Reads base64 Ed25519 private keys from `./keys.priv.json` (CWD-relative). **Plaintext at rest.** Add to `.gitignore`. Replace before shipping.

```json
"dwc-dev-01": { "type": "local" }
```

Generate with:

```bash
dwc keygen --kid dwc-dev-01 --backend local
```

Writes `keys.priv.json` (private) and prints a `keyring.json` entry to paste into your public keyring.

---

## `file` — portable, secret-mount friendly

Reads the same JSON shape as `local` but from an arbitrary path. Use for Docker secret mounts, Kubernetes secrets, external volumes — anywhere the file lives off the repo and outside CWD.

```json
"dwc-staging": { "type": "file", "path": "/run/secrets/staging-key.json" }
```

Generate with:

```bash
dwc keygen --kid dwc-staging --backend file --path /run/secrets/staging-key.json
```

Same security posture as `local` (plaintext at rest), just with a non-CWD path so it's not mistaken for a dev artifact.

---

## `keychain` — macOS Keychain

Stores the Ed25519 private bytes as a generic-password item in the user's login Keychain. Signing happens in-process (the `cryptography` library does the Ed25519 math; the Keychain just supplies the bytes on demand). **Stdlib only — no extra install.**

```json
"dwc-dit-01": { "type": "keychain", "service": "dwc-sidecar" }
```

Generate with:

```bash
dwc keygen --kid dwc-dit-01 --backend keychain --service dwc-sidecar
```

First call may prompt for Keychain password if the keychain is locked or the calling app is unauthorised. After Allow / Always Allow, signing is silent.

Good for DIT-on-Mac workflows where keys should be off the filesystem but Secure Enclave (which doesn't support Ed25519) is over-engineered.

---

## `pkcs11` — any PKCS#11 v3.0 token

Hardware-grade signing — private key never leaves the token. Compatible with YubiHSM 2, Nitrokey, SoftHSM, Thales, AWS CloudHSM, Entrust nShield, and other v3.0-conformant tokens.

```json
"dwc-dit-02": { "type": "pkcs11",
                "module": "/usr/local/lib/libykcs11.dylib",
                "slot":   0,
                "label":  "dwc-dit-02" }
```

Install:

```bash
pipx inject dwc-sidecar 'dwc-sidecar[hsm]'
```

PIN is read from the env var named in `pin_env` (default `DWC_PKCS11_PIN`); fall back to interactive prompt if unset.

Generate with:

```bash
dwc keygen --kid dwc-dit-02 --backend pkcs11 \
           --module /usr/local/lib/libykcs11.dylib --slot 0
```

`--slot N` and `--token-label LABEL` are alternatives — pick whichever your vendor's tooling exposes.

For AWS CloudHSM, point `module` at the CloudHSM Client's PKCS#11 library (`/opt/cloudhsm/lib/libcloudhsm_pkcs11.so` on the standard install).

---

## `gcp-kms` — Google Cloud KMS

Uses GCP KMS keys with `EC_SIGN_ED25519`. Authentication via the GCP default credential chain (service account on a GCE/GKE host, `gcloud auth application-default login` locally, etc.).

```json
"dwc-color-01": { "type": "gcp-kms",
                  "key_version": "projects/PROJECT/locations/REGION/keyRings/RING/cryptoKeys/KEY/cryptoKeyVersions/VERSION" }
```

Install:

```bash
pipx inject dwc-sidecar 'dwc-sidecar[gcp]'
```

Keys must be created externally (out of `dwc keygen`'s scope):

```bash
gcloud kms keys create dwc-color-01 \
  --location=eu-west2 --keyring=dwc-keys \
  --purpose=asymmetric-signing --default-algorithm=ec-sign-ed25519
```

Then add the resulting `key_version` to your `signers.json` and a corresponding public-key `keyring.json` entry. The public key can be pulled with `gcloud kms keys versions get-public-key`.

---

## `vault-transit` — HashiCorp Vault Transit

Vault's Transit secrets engine has native Ed25519 support. Uses stdlib `urllib` — no extra install. Token from the `VAULT_TOKEN` env var.

```json
"dwc-post-01": { "type": "vault-transit",
                 "url": "https://vault.example.com:8200",
                 "key_name": "dwc-post-01" }
```

Keys must be created externally:

```bash
vault write -f transit/keys/dwc-post-01 type=ed25519
```

The signer URL-encodes input bytes and sends them to `transit/sign/<key_name>`; Vault returns the base64-encoded signature.

Token expiry is the most common runtime failure mode here; `dwc doctor` Check 7 (signer self-test) catches it before a real signing run.

---

## `azure-mhsm` — Azure Managed HSM

Azure Key Vault **Managed HSM** tier (not standard Key Vault — Ed25519 is unsupported there). Uses the `EDDSA` algorithm. Auth via `DefaultAzureCredential` (env vars, managed identity, Azure CLI login, etc.).

```json
"dwc-audit-01": { "type": "azure-mhsm",
                  "key_id": "https://myhsm.managedhsm.azure.net/keys/audit-01/abc" }
```

Install:

```bash
pipx inject dwc-sidecar 'dwc-sidecar[azure]'
```

Keys must be created externally:

```bash
az keyvault key create --hsm-name myhsm --name audit-01 \
                       --kty OKP-HSM --curve Ed25519 --ops sign verify
```

The `key_id` is the full versioned URI (note the trailing version segment).

---

## Rotation

DWC keyrings are append-only — old kids stay in the public keyring with their original `validFrom`/`validUntil` so historical sidecars continue to verify. Rotation:

1. `dwc keygen --kid <new-kid> --backend <backend>` to generate the successor.
2. Publish the new public-key `keyring.json` entry alongside the old one.
3. Update `signers.json` so future signs use `<new-kid>`.
4. Optionally set the old kid's `validUntil` to the rotation timestamp if you want strict cutoff (otherwise old sidecars keep their original validity windows).

`dwc doctor` Check 12 warns 14 days before any kid expires — that's your rotation reminder.

## Related

- [`doctor.md`](doctor.md) — Checks 6, 7, 8, 12 audit the signer side of the install.
- [`watch.md`](watch.md) — every event written by `dwc watch` flows through one of these backends.
- [`CLAUDE.md`](../../CLAUDE.md) → "External trust surfaces" — the public-key + revocation surfaces (`keyring.json`, `revocations.json`) that pair with these backends.
