#!/bin/bash
# Удаление WaySwitch. Конфиг пользователя (~/.config/wayswitch) не трогаем.
set -euo pipefail
if [ "$EUID" -ne 0 ]; then echo "Запустите через sudo" >&2; exit 1; fi
USER_HOME="$(getent passwd "${SUDO_USER:-root}" | cut -d: -f6)"
sudo -u "${SUDO_USER:-root}" systemctl --user disable --now wayswitch 2>/dev/null || true
rm -f "$USER_HOME/.config/systemd/user/wayswitch.service"
rm -f "$USER_HOME/.config/autostart/ru.siberia.WaySwitch.desktop"
rm -rf "$USER_HOME/.local/share/gnome-shell/extensions/wayswitch@siberia.ru"
rm -rf /usr/local/lib/wayswitch /usr/local/bin/wayswitch /usr/local/bin/wayswitch-gui
rm -f /etc/udev/rules.d/60-wayswitch.rules /etc/modules-load.d/wayswitch.conf
rm -f /usr/local/share/applications/ru.siberia.WaySwitch.desktop
udevadm control --reload-rules || true
echo "WaySwitch удалён."
