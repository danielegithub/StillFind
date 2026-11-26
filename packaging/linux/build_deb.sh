#!/bin/sh
# Crea dist/stillfind_<versione>_amd64.deb da dist/StillFind (installa in /opt/stillfind).
# Uso: packaging/linux/build_deb.sh <versione> [cartella-icona.png]
set -eu
cd "$(dirname "$0")/../.."
VERSION="${1:?versione}"
ICON="${2:-build/icons/stillfind.png}"
[ -d dist/StillFind ] || { echo "Manca dist/StillFind: esegui prima build.py" >&2; exit 1; }
ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT
mkdir -p "$ROOT/opt/stillfind" "$ROOT/usr/bin" "$ROOT/usr/share/applications" \
         "$ROOT/usr/share/icons/hicolor/256x256/apps" "$ROOT/usr/share/doc/stillfind" "$ROOT/DEBIAN"
cp -R dist/StillFind/. "$ROOT/opt/stillfind/"
ln -s /opt/stillfind/StillFind "$ROOT/usr/bin/stillfind"
cp "$ICON" "$ROOT/usr/share/icons/hicolor/256x256/apps/stillfind.png"
cp packaging/ALPHA.txt "$ROOT/usr/share/doc/stillfind/ALPHA.txt"
cp LICENSE "$ROOT/usr/share/doc/stillfind/copyright"
cat > "$ROOT/usr/share/applications/stillfind.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=StillFind
Comment=Offline document search (alpha)
Exec=/opt/stillfind/StillFind
Icon=stillfind
Terminal=false
Categories=Office;Utility;
DESK
SIZE="$(du -sk "$ROOT/opt" | cut -f1)"
cat > "$ROOT/DEBIAN/control" <<CTRL
Package: stillfind
Version: $VERSION
Section: utils
Priority: optional
Architecture: amd64
Installed-Size: $SIZE
Maintainer: Daniele Sisto
Description: StillFind - offline document search (ALPHA)
 Alpha test software: it may be unstable and is provided without any warranty.
 See /usr/share/doc/stillfind/ALPHA.txt.
CTRL
dpkg-deb --build --root-owner-group "$ROOT" "dist/stillfind_${VERSION}_amd64.deb"
echo "dist/stillfind_${VERSION}_amd64.deb"
