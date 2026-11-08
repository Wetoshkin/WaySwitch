#!/bin/bash
# Разложить файлы WaySwitch в дерево пакета: одно место для .deb, .rpm и AUR.
#
#   tools/stage.sh DESTDIR PYTHON_SITELIB
#
# DESTDIR — корень будущего пакета, PYTHON_SITELIB — каталог модулей Python
# в целевой системе (/usr/lib/python3/dist-packages в Debian,
# /usr/lib/python3.X/site-packages в Fedora и Arch).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${1:?укажите DESTDIR}"
SITELIB="${2:?укажите каталог модулей Python}"

install -d "$DEST$SITELIB"
cp -r "$ROOT/wayswitch" "$DEST$SITELIB/wayswitch"
find "$DEST$SITELIB/wayswitch" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

install -d "$DEST/usr/bin"
# Обёртки сами добавляют каталог модулей в sys.path: так пакет не зависит от
# версии Python в имени site-packages (у Fedora и Arch оно меняется с
# каждым обновлением Python, а .rpm собирается один на все версии).
for pair in "wayswitch:wayswitch.cli" "wayswitch-gui:wayswitch.gui.app"; do
    cat > "$DEST/usr/bin/${pair%%:*}" <<EOF
#!/usr/bin/python3 -P
import sys

sys.path.insert(0, "$SITELIB")
from ${pair#*:} import main  # noqa: E402

raise SystemExit(main())
EOF
done
chmod 755 "$DEST/usr/bin/wayswitch" "$DEST/usr/bin/wayswitch-gui"

install -Dm644 "$ROOT/packaging/60-wayswitch.rules" "$DEST/usr/lib/udev/rules.d/60-wayswitch.rules"
install -Dm644 "$ROOT/packaging/migrate.py" "$DEST/usr/share/wayswitch/migrate.py"
install -d "$DEST/usr/lib/modules-load.d"
echo "uinput" > "$DEST/usr/lib/modules-load.d/wayswitch.conf"

install -d "$DEST/usr/lib/systemd/user"
sed 's#ExecStart=/usr/local/bin/wayswitch run#ExecStart=/usr/bin/wayswitch run#' \
    "$ROOT/packaging/wayswitch.service" > "$DEST/usr/lib/systemd/user/wayswitch.service"

install -d "$DEST/usr/share/gnome-shell/extensions/wayswitch@wetoshkin.github.io"
cp -r "$ROOT"/extension/wayswitch@wetoshkin.github.io/. "$DEST/usr/share/gnome-shell/extensions/wayswitch@wetoshkin.github.io/"

install -Dm644 "$ROOT/packaging/wayswitch-session.desktop"     "$DEST/etc/xdg/autostart/wayswitch-session.desktop"

# Виджет для системного лотка KDE Plasma 6.
install -d "$DEST/usr/share/plasma/plasmoids"
cp -r "$ROOT/packaging/kde/io.github.Wetoshkin.wayswitch" "$DEST/usr/share/plasma/plasmoids/"

install -Dm644 "$ROOT/packaging/io.github.Wetoshkin.WaySwitch.Settings.desktop" \
    "$DEST/usr/share/applications/io.github.Wetoshkin.WaySwitch.Settings.desktop"
install -Dm644 "$ROOT/packaging/icons/io.github.Wetoshkin.WaySwitch.svg" \
    "$DEST/usr/share/icons/hicolor/scalable/apps/io.github.Wetoshkin.WaySwitch.svg"
install -d "$DEST/usr/share/icons/hicolor/symbolic/apps"
install -m644 "$ROOT"/packaging/icons/io.github.Wetoshkin.WaySwitch-*symbolic.svg \
    "$DEST/usr/share/icons/hicolor/symbolic/apps/"

install -Dm644 "$ROOT/README.md" "$DEST/usr/share/doc/wayswitch/README.md"
install -Dm644 "$ROOT/LICENSE" "$DEST/usr/share/licenses/wayswitch/LICENSE"
install -Dm644 "$ROOT/wayswitch/data/LICENSE" "$DEST/usr/share/licenses/wayswitch/DATA-LICENSE"

find "$DEST" -type d -exec chmod 755 {} +
