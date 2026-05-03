#!/usr/bin/env bash
# make_icon.sh — regenerate Resources/AppIcon.icns from the source DWC logo.
#
# One-time / occasional setup. Not part of the normal build because the
# .icns is committed (so CI doesn't need Python/PIL on the runner).
# Re-run only when the source logo changes.
#
# Source:  resources/logos/DWC_LogoDevice.png   (hexagonal, alpha)
# Output:  macos-statusbar/Resources/AppIcon.icns
#
# Requirements: macOS (sips + iconutil) and python3 with Pillow.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PACKAGE_ROOT="$(cd "$HERE/.." && pwd)"
REPO_ROOT="$(cd "$PACKAGE_ROOT/.." && pwd)"

SRC="$REPO_ROOT/resources/logos/DWC_LogoDevice.png"
OUT="$PACKAGE_ROOT/Resources/AppIcon.icns"

if [[ ! -f "$SRC" ]]; then
    echo "ERROR: source logo not found: $SRC" >&2
    exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 1. Composite hexagon onto a 1024×1024 transparent square at 88% safe area.
echo "→ building 1024×1024 master from $SRC"
python3 - <<PY
from PIL import Image
src = Image.open("$SRC").convert("RGBA")
TARGET, SAFE = 1024, 880
sw, sh = src.size
scale = min(SAFE / sw, SAFE / sh)
nw, nh = round(sw * scale), round(sh * scale)
fit = src.resize((nw, nh), Image.LANCZOS)
canvas = Image.new("RGBA", (TARGET, TARGET), (0, 0, 0, 0))
canvas.paste(fit, ((TARGET - nw) // 2, (TARGET - nh) // 2), fit)
canvas.save("$TMP/master.png")
PY

# 2. Build the .iconset directory with Apple's required filename pairs.
ICONSET="$TMP/AppIcon.iconset"
mkdir -p "$ICONSET"
for size in 16 32 64 128 256 512 1024; do
    sips -z "$size" "$size" "$TMP/master.png" \
         --out "$TMP/icon_${size}x${size}.png" >/dev/null
done
cp "$TMP/icon_16x16.png"     "$ICONSET/icon_16x16.png"
cp "$TMP/icon_32x32.png"     "$ICONSET/icon_16x16@2x.png"
cp "$TMP/icon_32x32.png"     "$ICONSET/icon_32x32.png"
cp "$TMP/icon_64x64.png"     "$ICONSET/icon_32x32@2x.png"
cp "$TMP/icon_128x128.png"   "$ICONSET/icon_128x128.png"
cp "$TMP/icon_256x256.png"   "$ICONSET/icon_128x128@2x.png"
cp "$TMP/icon_256x256.png"   "$ICONSET/icon_256x256.png"
cp "$TMP/icon_512x512.png"   "$ICONSET/icon_256x256@2x.png"
cp "$TMP/icon_512x512.png"   "$ICONSET/icon_512x512.png"
cp "$TMP/icon_1024x1024.png" "$ICONSET/icon_512x512@2x.png"

# 3. Compile to .icns.
mkdir -p "$(dirname "$OUT")"
iconutil -c icns -o "$OUT" "$ICONSET"
ls -lh "$OUT"
echo "→ done: $OUT"
