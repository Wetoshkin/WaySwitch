#!/bin/bash
# Сборка Debian-пакета wayswitch_<версия>_all.deb: архитектурно-независимый
# пакет без debhelper — обычное дерево + dpkg-deb --build. Запускать из
# корня репозитория на Linux (нужны dpkg-deb, python3).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# --- версия -----------------------------------------------------------
PY_VERSION="$(grep -m1 '^version = ' pyproject.toml | sed -E 's/^version = "([^"]+)"/\1/')"
if [ -z "$PY_VERSION" ]; then
    echo "Не удалось прочитать version из pyproject.toml" >&2
    exit 1
fi
DEB_VERSION="$(python3 tools/debversion.py "$PY_VERSION")"

PKG_NAME="wayswitch_${DEB_VERSION}_all"
STAGE="$ROOT/build/deb/$PKG_NAME"
DIST_DIR="$ROOT/dist"

echo "== Версия: python=$PY_VERSION deb=$DEB_VERSION"

rm -rf "$STAGE"
mkdir -p "$STAGE"

# --- файлы пакета (общие с .rpm и AUR) -----------------------------------
bash "$ROOT/tools/stage.sh" "$STAGE" /usr/lib/python3/dist-packages
BIN="$STAGE/usr/bin"
# В Debian лицензия — в doc/<пакет>/copyright, а не в /usr/share/licenses.
rm -rf "$STAGE/usr/share/licenses"

# --- документация и лицензии ----------------------------------------------
DOC_DIR="$STAGE/usr/share/doc/wayswitch"
mkdir -p "$DOC_DIR"
cp "$ROOT/README.md" "$DOC_DIR/README.md"
{
    echo "WaySwitch — MIT, см. https://github.com/Wetoshkin/WaySwitch"
    echo
    cat "$ROOT/LICENSE"
    echo
    echo "----------------------------------------------------------------"
    echo "Словарные данные wayswitch/data/*.gz — CC-BY-SA 4.0:"
    echo
    cat "$ROOT/wayswitch/data/LICENSE"
} > "$DOC_DIR/copyright"

# --- DEBIAN/control и maintainer-скрипты ----------------------------------
DEBIAN_DIR="$STAGE/DEBIAN"
mkdir -p "$DEBIAN_DIR"
sed "s/@VERSION@/$DEB_VERSION/" "$ROOT/packaging/deb/control.in" > "$DEBIAN_DIR/control"
cp "$ROOT/packaging/deb/postinst" "$DEBIAN_DIR/postinst"
cp "$ROOT/packaging/deb/postrm" "$DEBIAN_DIR/postrm"
chmod 755 "$DEBIAN_DIR/postinst" "$DEBIAN_DIR/postrm"

# --- права ----------------------------------------------------------------
find "$STAGE" -type d -exec chmod 755 {} +
find "$STAGE" -type f -exec chmod 644 {} +
chmod 755 "$BIN/wayswitch" "$BIN/wayswitch-gui"
chmod 755 "$DEBIAN_DIR/postinst" "$DEBIAN_DIR/postrm"

# --- сборка .deb ------------------------------------------------------
mkdir -p "$DIST_DIR"
DEB_PATH="$DIST_DIR/${PKG_NAME}.deb"
dpkg-deb --build --root-owner-group "$STAGE" "$DEB_PATH"

echo "== Готово: $DEB_PATH"
dpkg-deb --info "$DEB_PATH"
dpkg-deb --contents "$DEB_PATH"
