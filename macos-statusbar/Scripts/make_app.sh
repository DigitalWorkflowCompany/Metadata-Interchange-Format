#!/usr/bin/env bash
# make_app.sh — wrap the SwiftPM release binary in a .app bundle.
#
# SwiftPM produces a bare executable; a menu-bar-only app needs the
# LSUIElement=true Info.plist flag to avoid a Dock icon, plus the
# standard app-bundle layout. This script does that wrap.
#
# Optional codesigning: if DEVELOPER_ID is set in env, the bundle is
# signed with a "Developer ID Application" identity. Notarization is
# a separate step in the release CI workflow.
#
# Usage:
#   ./Scripts/make_app.sh                 # unsigned build/DwcStatus.app
#   DEVELOPER_ID="..." ./Scripts/make_app.sh    # signed

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PACKAGE_ROOT="$(cd "$HERE/.." && pwd)"
cd "$PACKAGE_ROOT"

BUILD_CONFIG="release"
BUNDLE_NAME="DWC Status.app"
BUNDLE_ID="com.the-dwc.sidecar.status"
VERSION="${APP_VERSION:-0.1.0}"
ICON_SRC="$PACKAGE_ROOT/Resources/AppIcon.icns"

echo "→ swift build (config=$BUILD_CONFIG)…"
swift build --configuration "$BUILD_CONFIG"

BIN_PATH="$(swift build --configuration "$BUILD_CONFIG" --show-bin-path)/DwcStatus"
if [[ ! -x "$BIN_PATH" ]]; then
    echo "ERROR: built binary not found at $BIN_PATH" >&2
    exit 1
fi

APP_OUT="$PACKAGE_ROOT/build/$BUNDLE_NAME"
rm -rf "$APP_OUT"
mkdir -p "$APP_OUT/Contents/MacOS" "$APP_OUT/Contents/Resources"

cp "$BIN_PATH" "$APP_OUT/Contents/MacOS/DwcStatus"
chmod +x      "$APP_OUT/Contents/MacOS/DwcStatus"

if [[ -f "$ICON_SRC" ]]; then
    cp "$ICON_SRC" "$APP_OUT/Contents/Resources/AppIcon.icns"
else
    echo "WARNING: $ICON_SRC missing — bundle will ship without an app icon" >&2
fi

cat > "$APP_OUT/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>        <string>DwcStatus</string>
    <key>CFBundleIdentifier</key>        <string>$BUNDLE_ID</string>
    <key>CFBundleName</key>              <string>DWC Status</string>
    <key>CFBundleDisplayName</key>       <string>DWC Status</string>
    <key>CFBundleIconFile</key>          <string>AppIcon</string>
    <key>CFBundleIconName</key>          <string>AppIcon</string>
    <key>CFBundlePackageType</key>       <string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>CFBundleVersion</key>           <string>$VERSION</string>
    <key>LSMinimumSystemVersion</key>    <string>13.0</string>
    <key>LSUIElement</key>               <true/>
    <key>NSHighResolutionCapable</key>   <true/>
    <key>NSHumanReadableCopyright</key>  <string>© Digital Workflow Company</string>
</dict>
</plist>
EOF

echo "→ bundle: $APP_OUT"

# ── Optional codesign ───────────────────────────────────────────────────
if [[ -n "${DEVELOPER_ID:-}" ]]; then
    echo "→ codesign with \"Developer ID Application: $DEVELOPER_ID\""
    codesign \
        --sign "Developer ID Application: $DEVELOPER_ID" \
        --options runtime \
        --timestamp \
        --deep --force \
        "$APP_OUT"
    codesign --verify --strict --deep --verbose=1 "$APP_OUT" || {
        echo "ERROR: codesign verification failed" >&2; exit 1; }
    echo "→ signed + verified"
else
    echo "→ (unsigned — set DEVELOPER_ID to codesign)"
fi

echo "Done. Open with:  open $APP_OUT"
