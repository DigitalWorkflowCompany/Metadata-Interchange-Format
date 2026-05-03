# Quickstart

Get from a fresh Mac to a green `dwc doctor` in under three minutes.

## Prerequisites

- macOS 13+ or Linux (Ubuntu 22.04+ tested). Windows is not yet supported.
- Python 3.11+. Check with `python3 --version`.
- Xcode Command Line Tools (`xcode-select --install`) — required for the underlying Python wheels on macOS.

## Install

The fastest path on macOS is Homebrew (plan §6):

```bash
brew install digitalworkflowcompany/tap/dwc-sidecar
```

That gives you the `dwc` CLI on `PATH` with no Python toolchain in your way.

If you don't have Homebrew, or you're on Linux, install via pipx instead:

```bash
brew install pipx && pipx ensurepath    # macOS without Homebrew? skip this line
pipx install dwc-sidecar
```

Either path installs the same `dwc` CLI. `dwc --help` lists every subcommand.

## First-time setup — `dwc init`

```bash
cd /Volumes/<your-shoot-folder>     # or anywhere; init runs in CWD
dwc init
```

`dwc init` walks you through:

```
DWC sidecar setup

Host: macOS (arm64), Python 3.12.3
Working directory: /Volumes/Mag_A001/WAR_Day01

Where should your signing key live?
  1) macOS Keychain (recommended on this host)
  2) File on disk (portable, for Docker/CI)
  3) I'll configure a cloud / HSM backend myself
[1]: _

Signing kid [dwc-dit-01]: _
Keyring entry valid for [90] days: _

Install a LaunchAgent so `dwc watch` starts at login? [Y/n]: _
Watch folder [current directory]: _
```

Default answers are correct on a typical macOS DIT cart. After the prompts:

- A new Ed25519 signing key is generated in the macOS Keychain.
- `keyring.json` is written in the current directory (publish this; it has only the public key + validity window).
- `signers.json` is written next to it (this points your local `dwc` runtime at the Keychain key; do **not** commit).
- A LaunchAgent at `~/Library/LaunchAgents/com.the-dwc.sidecar.watch.plist` is installed so `dwc watch` starts at login (skip with `--no-launch-agent` if you don't want this).

For non-interactive provisioning (CI / scripted setup), pass `--yes` and the relevant flags. See `dwc init --help`.

## Verify — `dwc doctor`

```bash
dwc doctor
```

This runs a 12-check pre-flight audit against the current directory and your signing config. A clean install looks like (titles as the doctor actually prints them):

```
[PASS]  Python version              3.12.3 ≥ 3.11
[PASS]  Required packages           all six runtime deps importable
[PASS]  Hash algorithms             all algorithms in sidecars resolvable
[PASS]  keyring.json                1 kid(s)
[PASS]  Keyring validity windows    all kids in sidecars covered
[PASS]  Signer config (DWC_SIGNERS) one backend reachable
[PASS]  Signer self-test            keychain backend signed a 32-byte payload OK
[PASS]  Plaintext private keys      no plaintext key file
[PASS]  Hosted schema drift         local schemas match ns.the-dwc.com/sidecar/v0.1
[PASS]  .watch-state.json           not present (no watcher running here)
[PASS]  Sidecar parse               no *.omc.json in CWD
[PASS]  Key expiry window           all kids valid > 14d
```

`--quick` skips the two network/signer checks (Signer self-test, Hosted schema drift) for a sub-200ms run.

If any check is `[WARN]` or `[FAIL]`, the **Remedies** section underneath spells out what to fix. The most common one on a fresh install is *"Add `export DWC_SIGNERS="$PWD/signers.json"` to your shell rc"* — `dwc init` prints this as a "next step" but doesn't write to your `.zshrc`/`.bashrc` for you.

For machine-readable output (CI / menu-bar status app):

```bash
dwc doctor --json
```

## Start producing sidecars

If you accepted the LaunchAgent install in `dwc init`, watch is already running for the directory you chose. Drop production media in and sidecars appear next to each clip as soon as the matching `*.mhl` lands.

If you skipped the LaunchAgent or want to run watch in the foreground for a specific tree:

```bash
dwc watch /Volumes/Mag_A001/WAR_Day01 --interval 2 --stable 5
```

This polls every two seconds, waits for files to be stable for five seconds before signing, and writes one `<clip>.omc.json` per discovered clip plus a per-day `dwc-columns-YYYY-MM-DD.ale` for ALE-aware tools.

For a one-shot run over an existing tree without the watch loop:

```bash
dwc batch /Volumes/Mag_A001/WAR_Day01            # rehash from disk; ~450 MB/s
dwc mhl-walk /Volumes/Mag_A001/WAR_Day01         # lift hashes from MHL; ~900 sidecars/sec
```

`mhl-walk` is the production path; `batch` is the periodic audit.

## Validate

```bash
dwc validate <sidecar.omc.json> --base-dir /Volumes/Mag_A001/WAR_Day01
```

Runs all nine validator stages: OMC structure, DWC schemas, event-chain integrity, Ed25519 signatures, lock-event crosscheck, artifact file integrity, controlled-values enforcement, MHL inner consistency, and CDL consistency. Exit code is the sum of error counts.

Pass `--check-hosted` to additionally byte-compare your local schemas against the canonical copies at `ns.the-dwc.com`.

## Where to go next

- **Per-tool integrations** — `docs/integration/`. Silverstack 9.2+ (Lua), DaVinci Resolve 20/21 (Python), Avid Media Composer (ALE merge). Each doc has install steps + screenshots.
- **Operations reference** — `docs/operations/`: deeper detail on [`dwc doctor`](operations/doctor.md), [`dwc watch`](operations/watch.md), and the [signer-backend matrix](operations/signer-backends.md).
- **Architecture and conventions** — [`CLAUDE.md`](../CLAUDE.md): the 9-stage validator, the OMC composition strategy, hash registry, and the conventions a contributor should follow.
- **Schemas** — <https://ns.the-dwc.com/sidecar/v0.1/>. Immutable per version.

If something doesn't work, run `dwc doctor` first — most first-time issues are environmental and the doctor's remedy text walks you out of them.
