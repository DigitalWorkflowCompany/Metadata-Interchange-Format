# Cutting a `DWC Status` release

Step-by-step runbook for the day you tag a `vX.Y.Z-mac` release. Reference material on what each piece does lives in [`README.md`](README.md); this file is just the procedure.

## Pre-flight checklist

Run through this once when your Apple Developer Program membership activates, then re-confirm each release:

- [ ] **Apple Developer Program membership** is active (developer.apple.com → Account → Membership status: *Active*).
- [ ] **Developer ID Application certificate** exists in your login Keychain. Verify with:
  ```bash
  security find-identity -v -p codesigning
  ```
  You're looking for a line containing `Developer ID Application: <your name> (<TEAMID>)`. If absent, generate one at developer.apple.com → Certificates → "+" → Developer ID Application, download the `.cer`, double-click to install.
- [ ] **App ID `com.the-dwc.sidecar.status`** registered at developer.apple.com → Identifiers (Apple may auto-register on first notarization; pre-registering avoids a one-time surprise).
- [ ] **App-specific password** generated at appleid.apple.com → Sign-In and Security → App-Specific Passwords. Label it something memorable like `DWC notarytool`. **Save it somewhere** — Apple won't show it again.
- [ ] **Team ID** noted (developer.apple.com → Membership Details → Team ID, 10 alphanumeric chars).
- [ ] **`.p12` export** of the cert + private key:
  - Keychain Access → "My Certificates" tab → find `Developer ID Application: …` → right-click → Export → `.p12` with a strong password.
  - Save the `.p12` and its password somewhere you can find them again — both go into GitHub secrets.
- [ ] **Six GitHub repository secrets** set in *Settings → Secrets and variables → Actions*:
  | Secret name                          | What to paste                                                      |
  |--------------------------------------|--------------------------------------------------------------------|
  | `APPLE_DEVELOPER_ID_CERT_P12`        | Output of `base64 -i cert.p12` (the whole base64 blob, no quotes)  |
  | `APPLE_DEVELOPER_ID_CERT_PASSWORD`   | The `.p12` export password                                         |
  | `APPLE_DEVELOPER_ID`                 | The cert's identity string, e.g. `Digital Workflow Company (TEAMID)` |
  | `NOTARIZE_APPLE_ID`                  | The Apple ID email tied to your Developer Program account          |
  | `NOTARIZE_TEAM_ID`                   | The 10-char Team ID                                                |
  | `NOTARIZE_APP_PASSWORD`              | The app-specific password from step 4 above                        |

Missing any one of these means the workflow logs `::warning::Apple Developer secrets missing — tag will not produce a signed DMG.` and uploads only the unsigned `.app` artifact.

## Cutting the release

When you're ready:

1. **Bump version** in two places:
   ```bash
   # APP_VERSION the workflow passes is github.ref_name, so the tag itself
   # carries the version string. The Info.plist uses ${APP_VERSION} via the
   # build script — no source edit needed.
   ```
   Confirm `macos-statusbar/Scripts/make_app.sh` reads `${APP_VERSION:-0.1.0}` and that the workflow's `env: APP_VERSION: ${{ github.ref_name }}` is intact (`.github/workflows/macos-statusbar.yml`).

2. **Tag and push**:
   ```bash
   git tag -a v1.0.0-mac -m "DwcStatus 1.0.0 — first signed + notarized release"
   git push origin v1.0.0-mac
   ```
   The `*-mac` suffix is what the workflow's `if: endsWith(github.ref, '-mac')` triggers on. Don't tag this `v1.0.0` — that name is reserved for the Python package's eventual 1.0 line.

3. **Watch the Actions run** at github.com/DigitalWorkflowCompany/Metadata-Interchange-Format/actions. The `sign-and-release` job runs on `macos-14` and takes 8–15 minutes:
   - 30 s: Import cert into ephemeral Keychain.
   - 1–2 min: `swift build -c release` + `make_app.sh` codesign.
   - **5–10 min: `xcrun notarytool submit --wait`** — Apple's queue dominates. Most variance is here.
   - 30 s: `xcrun stapler staple` + `create-dmg`.
   - 30 s: `softprops/action-gh-release` attaches the DMG to the auto-created GitHub Release.

4. **Verify the release**: when the workflow goes green, github.com/.../releases/tag/v1.0.0-mac will have a `DwcStatus-v1.0.0-mac.dmg` asset. Download on a fresh Mac (or one you haven't already run an unsigned build on), drag to `/Applications`, launch. **No "damaged, move to Trash" dialog** is the success signal.

5. **Spot-check the staple** locally:
   ```bash
   xcrun stapler validate "/Applications/DWC Status.app"
   # → "The validate action worked!"
   ```
   And, on an offline Mac (cuts off Apple's online check):
   ```bash
   spctl --assess -vv "/Applications/DWC Status.app"
   # → "accepted" + "Notarized Developer ID"
   ```

## When it fails

Most first-release failures are notarization rejections. The workflow log will end with `Status: Invalid` from `notarytool`; pull the per-submission log:

```bash
xcrun notarytool log <submission-uuid> \
    --apple-id "$NOTARIZE_APPLE_ID" \
    --team-id  "$NOTARIZE_TEAM_ID" \
    --password "$NOTARIZE_APP_PASSWORD"
```

Common causes and fixes:

- **"The signature of the binary is invalid."** → cert was exported without its private key, or the `.p12` password is wrong, or the cert is the wrong type (Developer ID *Installer* instead of Developer ID *Application*). Re-export from Keychain Access making sure to select the certificate row (not just the key), and that "Export My Certificates" is the action.
- **"The executable does not have the hardened runtime enabled."** → `make_app.sh` should already pass `--options runtime` to codesign; if this fires, the script was bypassed. Don't bypass it.
- **"The binary uses an SDK older than the 10.9 SDK."** → only triggered if you point `swift build` at an unsupported toolchain; default GitHub `macos-14` runners are fine.
- **"Team is not yet authorised for notarization."** → first-time accounts can take 24h after enrolment to clear Apple's notarization fraud-check queue. Wait, retry.
- **"Invalid Team ID."** → the `NOTARIZE_TEAM_ID` secret is wrong; check developer.apple.com → Membership Details.
- **"Authentication credentials are invalid."** → the `NOTARIZE_APP_PASSWORD` is the *Apple ID password*, not an app-specific password. Generate a fresh app-specific password and rotate the secret.

If the cert imports succeed but the build itself fails, the issue is usually `swift build` finding the wrong Xcode. The runner image gets new Xcode versions periodically; if a `MenuBarExtra` API check breaks, pin `xcode-select -s` to a known-good version in the workflow.

## Subsequent releases

After 1.0.0-mac, every release is just:

```bash
# bump CFBundleShortVersionString in Info.plist if you want — make_app.sh
# pulls APP_VERSION from the tag name automatically, so the bare default
# is fine.
git tag -a v1.0.1-mac -m "DwcStatus 1.0.1 — <one-line summary>"
git push origin v1.0.1-mac
```

The Apple-side prerequisites only need redoing when the cert expires (default 5 years). Set a calendar reminder — an expired cert means notarization fails silently with an "invalid signature" rejection at the worst possible time.

## Related

- [`README.md`](README.md) — what the app does, how to build it locally, what's outstanding from §7.1.
- [`.github/workflows/macos-statusbar.yml`](../.github/workflows/macos-statusbar.yml) — the workflow this runbook drives.
- [`Scripts/make_app.sh`](Scripts/make_app.sh) — the build + local-codesign step.
- [Plan §3](../plans/phase-02.md) — the menu-bar app's design context.
