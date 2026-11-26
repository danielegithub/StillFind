#!/bin/sh
# Crea dist/StillFind-macos.dmg da dist/StillFind.app (senza firma né notarizzazione: vedi README).
set -eu
cd "$(dirname "$0")/../.."
APP="dist/StillFind.app"
[ -d "$APP" ] || { echo "Manca $APP: esegui prima build.py" >&2; exit 1; }
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cp packaging/ALPHA.txt "$STAGE/LEGGIMI-ALPHA.txt"
rm -f dist/StillFind-macos.dmg
hdiutil create -volname "StillFind (alpha)" -srcfolder "$STAGE" -ov -format UDZO dist/StillFind-macos.dmg
echo "dist/StillFind-macos.dmg"
