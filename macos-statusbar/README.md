# DWC Status — macOS menu-bar status app

Read-only ambient signal for `dwc watch`. A filled circle in the menu bar tinted by overall health; clicking drops a summary of recent signatures, quarantined clips, and `dwc doctor` findings.

Plan §3. No authoring, no key operations, no validation overrides — all interactive changes go through the CLI. The app can't be a vector for silent misconfiguration if it can't configure anything.

## Build (local)

```bash
cd macos-statusbar
swift build -c release
./Scripts/make_app.sh                       # → "build/DWC Status.app" (unsigned)
open "build/DWC Status.app"
```

The bundled app icon (`Resources/AppIcon.icns`) is committed to the repo.
If the source logo (`resources/logos/DWC_LogoDevice.png`) ever changes,
regenerate with `./Scripts/make_icon.sh` (requires Python with Pillow,
plus the macOS-stock `sips` and `iconutil`).

The Swift sources also open cleanly in Xcode:

```bash
open Package.swift
```

Xcode sees the SwiftPM package as an editable project — useful for the SwiftUI preview / live-layout workflow.

## Requirements

- macOS 13+ (the `MenuBarExtra` scene was introduced there).
- Xcode 14+ or Swift 5.9+ on the command line. Verified against Xcode 26.1 / Swift 6.2.
- `dwc` CLI available on `PATH` or at one of: `/opt/homebrew/bin/dwc`, `/usr/local/bin/dwc`, `/opt/local/bin/dwc`. Without it, the icon stays grey.

## First launch

The app writes `~/Library/Application Support/DwcStatus/config.json` on first run and detects which onboarding state your host is in (plan §6.3). One of three panels shows, never an empty grey icon with no explanation:

| State | What the panel shows | What you do |
|---|---|---|
| **CLI missing** | "Install the DWC CLI" with `brew install digitalworkflowcompany/tap/dwc-sidecar` (copy button). Pipx fallback for non-Homebrew users. | Run the install command in Terminal. Click "Recheck". |
| **Signers unconfigured** | "Configure signing" with a button that opens Terminal at `dwc init`. | Walk through `dwc init`'s prompts. Click "Recheck". |
| **Ready** | Recent signatures, quarantine count, doctor health. | Pick the watch folder via "Choose watch folder…" if you haven't yet. |

DWC Status itself never invokes `dwc init`, generates keys, or writes signer config — those operations live in the CLI by design. The onboarding panel only points you at the right command.

If macOS asks "DWC Status wants to control Terminal" the first time you click the Open-Terminal button, allow it — that's the standard one-time AppleScript permission prompt for a sandboxed-ish menu-bar app.

Config keys (written to `config.json`):

| Key | Purpose |
|---|---|
| `watchRoot` | Directory `dwc watch` monitors |
| `dwcBinary` | Auto-discovered; override if the CLI lives somewhere unusual |
| `pollDoctorSeconds` | Default 60 |
| `pollWatchStateSeconds` | Default 5 |

## Tests

```bash
swift test                                    # Swift decoder tests
python3 ../tools/macos-statusbar/sync_fixtures.py  # regenerate fixtures
python3 ../tools/macos-statusbar/sync_fixtures.py --check   # CI drift check
```

Fixtures are generated from Python so the Swift decoders stay in lockstep with what `dwc doctor --json` and `.watch-state.json` actually emit (plan §3.8a). Don't edit the `Tests/DwcStatusTests/Fixtures/*.json` files by hand — update `tools/macos-statusbar/sync_fixtures.py` and rerun.

## Codesign + notarize (release)

Without this, macOS greets users with "damaged, move to Trash" on first launch. Non-negotiable for a production DIT-facing app.

One-time setup (you, not CI):

1. Enrol in the Apple Developer Program ($99/year).
2. Generate a **Developer ID Application** certificate in *Certificates, Identifiers & Profiles*.
3. Export the certificate + private key as a `.p12` with a password.
4. Create an app-specific password for notarytool at <https://appleid.apple.com>.

Then add these GitHub repo secrets:

| Secret | Value |
|---|---|
| `APPLE_DEVELOPER_ID_CERT_P12` | Base64-encoded `.p12` (`base64 -i cert.p12`) |
| `APPLE_DEVELOPER_ID_CERT_PASSWORD` | Password for the `.p12` |
| `APPLE_DEVELOPER_ID` | "Your Name (TEAMID)" — the identity string Codesign uses |
| `NOTARIZE_APPLE_ID` | `adam@the-dwc.com` (the developer Apple ID) |
| `NOTARIZE_TEAM_ID` | 10-char team ID from the developer portal |
| `NOTARIZE_APP_PASSWORD` | The app-specific password |

When these are set, `.github/workflows/macos-statusbar.yml` codesigns, notarizes, staples, packages a `DWC-Status-vX.Y.Z-mac.dmg`, and uploads it as a release artifact on tag pushes. Missing secrets mean CI still builds + tests, but stops short of release packaging.

## Install for end users

1. Download `DWC-Status-vX.Y.Z-mac.dmg` from the GitHub release.
2. Drag `DWC Status.app` into `/Applications`.
3. Launch. Grant Keychain access if prompted (for reading the `keys.priv.json` dev-mode keyring).
4. Click the circle, pick a watch folder.
5. Optional: the app offers to install a LaunchAgent at login on first launch — tick to have it start automatically on reboot.

Homebrew cask ships alongside the 1.0.0-mac tag: `brew install --cask dwc-sidecar-status`.

## What doesn't work yet (plan §7.1 exit criteria)

- The **Install LaunchAgent** prompt on first launch isn't implemented — a DIT who wants auto-start at login currently wires the plist manually. Follow-up: reuse `dwc init`'s launchagent template under `src/dwc_sidecar/data/templates/`.
- SwiftUI previews (plan §3.8) aren't committed. `MenuBarExtra` previews require Xcode's Preview Canvas; they don't run from the SwiftPM test target. A contributor can add `#Preview` blocks in Xcode without touching the core code.

## UNVERIFIED

- Icon legibility on Intel Macs with Monterey menu bars — the SF Symbol `circle.fill` was sized for Apple Silicon + macOS 13+ (Liquid Glass effects in 15/Sequoia). Needs a trial on an older cart Mac.
