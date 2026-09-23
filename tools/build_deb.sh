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

# --- код пакета ---------------------------------------------------------
PYLIB="$STAGE/usr/lib/python3/dist-packages"
mkdir -p "$PYLIB"
cp -r "$ROOT/wayswitch" "$PYLIB/wayswitch"
find "$PYLIB/wayswitch" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# --- исполняемые обёртки -------------------------------------------------
BIN="$STAGE/usr/bin"
mkdir -p "$BIN"
cat > "$BIN/wayswitch" <<'EOF'
#!/usr/bin/python3
from wayswitch.cli import main

raise SystemExit(main())
EOF
cat > "$BIN/wayswitch-gui" <<'EOF'
#!/usr/bin/python3
from wayswitch.gui.app import main

raise SystemExit(main())
EOF
chmod 755 "$BIN/wayswitch" "$BIN/wayswitch-gui"

# --- udev / uinput --------------------------------------------------------
UDEV_DIR="$STAGE/usr/lib/udev/rules.d"
mkdir -p "$UDEV_DIR"
cp "$ROOT/packaging/60-wayswitch.rules" "$UDEV_DIR/60-wayswitch.rules"

MODLOAD_DIR="$STAGE/usr/lib/modules-load.d"
mkdir -p "$MODLOAD_DIR"
echo "uinput" > "$MODLOAD_DIR/wayswitch.conf"

# --- systemd user-юнит ------------------------------------------------
SYSTEMD_DIR="$STAGE/usr/lib/systemd/user"
mkdir -p "$SYSTEMD_DIR"
sed 's#ExecStart=/usr/local/bin/wayswitch run#ExecStart=/usr/bin/wayswitch run#' \
    "$ROOT/packaging/wayswitch.service" > "$SYSTEMD_DIR/wayswitch.service"

# --- расширение GNOME Shell -----------------------------------------------
EXT_DIR="$STAGE/usr/share/gnome-shell/extensions/wayswitch@siberia.ru"
mkdir -p "$EXT_DIR"
cp -r "$ROOT"/extension/wayswitch@siberia.ru/. "$EXT_DIR/"

# --- .desktop ---------------------------------------------------------
APPS_DIR="$STAGE/usr/share/applications"
mkdir -p "$APPS_DIR"
cp "$ROOT/packaging/ru.siberia.WaySwitch.desktop" "$APPS_DIR/ru.siberia.WaySwitch.desktop"

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
