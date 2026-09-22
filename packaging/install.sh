#!/bin/bash
# Установка WaySwitch: пакеты, код в /usr/local, правило udev, user-сервис
# для вызвавшего пользователя, расширение GNOME Shell в его профиль.
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
    echo "Запустите через sudo: sudo $0" >&2
    exit 1
fi
if [ -z "${SUDO_USER:-}" ] || [ "$SUDO_USER" = "root" ]; then
    echo "Нужен sudo от обычного пользователя (SUDO_USER пуст)." >&2
    exit 1
fi

SRC="$(cd "$(dirname "$0")/.." && pwd)"
LIB=/usr/local/lib/wayswitch
BIN=/usr/local/bin
USER_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"

echo "== Пакеты"
if command -v apt-get >/dev/null; then
    apt-get install -y python3 python3-evdev python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 \
        gir1.2-ibus-1.0 libxkbcommon0
elif command -v dnf >/dev/null; then
    dnf install -y python3 python3-evdev python3-gobject gtk4 libadwaita ibus libxkbcommon
else
    echo "Неизвестный пакетный менеджер: поставьте python3-evdev, PyGObject, GTK4, libadwaita, IBus вручную."
fi

echo "== Код → $LIB"
rm -rf "$LIB"
mkdir -p "$LIB"
cp -r "$SRC/wayswitch" "$LIB/"
find "$LIB" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
cat > "$BIN/wayswitch" <<EOF
#!/bin/sh
export PYTHONPATH="$LIB\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m wayswitch "\$@"
EOF
cat > "$BIN/wayswitch-gui" <<EOF
#!/bin/sh
export PYTHONPATH="$LIB\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -c "from wayswitch.gui.app import main; raise SystemExit(main())" "\$@"
EOF
chmod +x "$BIN/wayswitch" "$BIN/wayswitch-gui"

echo "== udev и uinput"
install -m 644 "$SRC/packaging/60-wayswitch.rules" /etc/udev/rules.d/60-wayswitch.rules
mkdir -p /etc/modules-load.d
echo uinput > /etc/modules-load.d/wayswitch.conf
modprobe uinput || true
udevadm control --reload-rules || true
udevadm trigger || true

echo "== Ярлык"
install -m 644 "$SRC/packaging/ru.siberia.WaySwitch.desktop" /usr/local/share/applications/ 2>/dev/null \
    || { mkdir -p /usr/local/share/applications; install -m 644 "$SRC/packaging/ru.siberia.WaySwitch.desktop" /usr/local/share/applications/; }

echo "== systemd user-сервис для $SUDO_USER"
UNIT_DIR="$USER_HOME/.config/systemd/user"
USER_GID="$(id -gn "$SUDO_USER")"
USER_UID="$(id -u "$SUDO_USER")"
sudo -u "$SUDO_USER" mkdir -p "$UNIT_DIR"
install -o "$SUDO_USER" -g "$USER_GID" -m 644 "$SRC/packaging/wayswitch.service" "$UNIT_DIR/wayswitch.service"
sudo -u "$SUDO_USER" XDG_RUNTIME_DIR="/run/user/$USER_UID" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$USER_UID/bus" systemctl --user daemon-reload 2>/dev/null || true

echo "== Расширение GNOME Shell"
EXT_DIR="$USER_HOME/.local/share/gnome-shell/extensions/wayswitch@siberia.ru"
sudo -u "$SUDO_USER" mkdir -p "$EXT_DIR"
cp -r "$SRC"/extension/wayswitch@siberia.ru/* "$EXT_DIR/"
chown -R "$SUDO_USER:$USER_GID" "$EXT_DIR"

cat <<EOF

Готово. Дальше от имени пользователя $SUDO_USER:
  1. Перелогиньтесь (правила udev применяются к новому сеансу).
  2. wayswitch doctor
  3. systemctl --user enable --now wayswitch
  4. Необязательно: gnome-extensions enable wayswitch@siberia.ru
  5. wayswitch-gui — настройки и трей.
EOF
