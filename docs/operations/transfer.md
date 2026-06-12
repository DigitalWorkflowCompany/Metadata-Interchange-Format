# Facility-to-facility custody transfer

This is the flagship multi-party workflow (v0.6): an on-set DIT hands a clip's
provenance to a post facility so that, days later and on a different network, the
receiver can prove — from the bundle alone — that *these exact bytes* are what
the sender signed, and counter-sign their acceptance into the same append-only
log.

```
 on-set                                              post facility
 ┌─────────┐   offer    ┌──────────┐  ship   ┌──────────┐  verify+accept
 │  DIT    │──────────▶ │  bundle  │────────▶│ receiver │───────────────▶ signed
 │ dwc-dit │            │  .zip    │         │ dwc-post │                 acceptance
 └─────────┘            └──────────┘         └──────────┘
```

## 0. Prerequisites

Both parties' public keys are in the `keyring.json` that travels with the
bundle, and each key is **actor-bound**:

```jsonc
"dwc-dit-01":  { "publicKey": "…", "actor": "urn:email:dit@onset.com",   … },
"dwc-post-01": { "publicKey": "…", "actor": "urn:email:io@postfacility.com", … }
```

Bind a key at generation time: `dwc keygen --kid dwc-post-01 --backend pkcs11 … --actor io@postfacility.com`.
Without the binding, Stage 4 can't prove the *receiver* (not just *a* trusted
key) accepted — and the whole point of counter-signing evaporates.

## 1. Sender offers custody

```bash
dwc transfer offer clip.omc.json \
    --to urn:email:io@postfacility.com \
    --signing-kid dwc-dit-01
```

Appends a sender-signed `transfer` event that **re-commits the artifact hashes**
being handed over (so acceptance means "I verified these bytes", not "something
arrived") and rewrites the chain-head. Until it's accepted, validation reports
the offer as a `WARN — transfer in flight` (exit 0): a pending hand-off is a
legitimate state, not an error.

## 2. Sender seals a bundle

```bash
dwc bundle clip.omc.json --base-dir /Volumes/Mag_A001 --out clip.dwcbundle.zip
# → prints:  keyring fingerprint:  sha256:acda6c49…
```

The zip carries the sidecar, the keyring, the referenced files, and a manifest.
Use `--lite` to leave the camera originals out (tens of GB); the manifest records
the omission so the receiver must opt in with `--allow-missing-clips`.

**E-mail or phone the printed `sha256:` fingerprint to the receiver out-of-band.**
A bundle that vouches for its own keyring proves only internal consistency — the
fingerprint is the trust that comes from *outside* the bundle.

## 3. Ship it

Any transport — shuttle drive, Aspera, S3. The bundle is self-contained.

## 4. Receiver verifies

```bash
dwc verify clip.dwcbundle.zip --keyring-fingerprint sha256:acda6c49…
```

`dwc verify` extracts to a temp dir (zip-slip-safe), runs all ten stages against
the bundle's *own* files, and **never** picks up `keyring.json` / `keys.priv.json`
from the working directory. Trust modes, strongest first:

| Flag | Meaning |
|---|---|
| `--keyring <path>` | verify against a keyring the receiver already holds (strongest) |
| `--keyring-fingerprint <sha>` | accept the bundled keyring only if its bytes match the out-of-band fingerprint |
| `--trust-bundled-keyring` | explicitly accept the keyring inside the bundle |
| *(none)* | runs, prints a `TRUST IS SELF-CONTAINED` banner, and exits **non-zero** |

Add `--strict` to promote warnings (weak hash algs, pending transfers) to a
non-zero exit.

## 5. Receiver accepts

```bash
dwc transfer accept clip.omc.json \
    --signing-kid dwc-post-01 \
    --base-dir <receiving-root>
```

`accept` **re-runs the full validator first** (with `--require-keyring`
semantics) and refuses to counter-sign on any error — the acceptance signature
attests a *successful verification at the receiving site*. On success it appends
a receiver-signed `accept` event that commits by hash to the exact offer
(`transfer.of`) and names the offerer (`transfer.from`), then rewrites the head.

Stage 3.5 now binds the pair: the acceptor must be the offer's named receiver,
`transfer.from` must be the offerer, and the two events must be signed by
*different* kids (no self-acceptance). The completed chain proves end-to-end
chain-of-custody between the two facilities.

> The natural composition is `dwc transfer accept` over a bundle the receiver has
> already `dwc verify`-ed — verify proves the bytes, accept records that the
> verification happened and who vouched for it.
