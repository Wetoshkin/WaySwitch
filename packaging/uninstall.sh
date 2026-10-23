#!/bin/bash
# Удаление WaySwitch, установленного packaging/install.sh.
# Конфиг пользователя (~/.config/wayswitch) не трогаем.
set -euo pipefail
if [ "$EUID" -ne 0 ]; then echo "Запустите через sudo" >&2; exit 1; fi
if [ -z "${SUDO_USER:-}" ] || [ "$SUDO_USER" = "root" ]; then
    echo "Нужен sudo от обычного пользователя (SUDO_USER пуст)." >&2
    exit 1
fi
USER_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
USER_UID="$(id -u "$SUDO_USER")"
STATE=/var/lib/wayswitch

as_user() {
    sudo -u "$SUDO_USER" env XDG_RUNTIME_DIR="/run/user/$USER_UID" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$USER_UID/bus" HOME="$USER_HOME" "$@"
}

as_user systemctl --user disable --now wayswitch 2>/dev/null || true
as_user gnome-extensions disable wayswitch@siberia.ru 2>/dev/null || true
rm -f "$USER_HOME/.config/systemd/user/wayswitch.service"
rm -f "$USER_HOME/.config/systemd/user/graphical-session.target.wants/wayswitch.service"
rm -f "$USER_HOME/.config/systemd/user/default.target.wants/wayswitch.service"  # юнит старых версий
rm -f "$USER_HOME/.config/autostart/ru.siberia.WaySwitch.desktop"
rm -f "$USER_HOME/.config/autostart/ru.siberia.WaySwitch.Settings.desktop"
rm -rf "$USER_HOME/.local/share/gnome-shell/extensions/wayswitch@siberia.ru"
rm -rf /usr/local/lib/wayswitch /usr/local/bin/wayswitch /usr/local/bin/wayswitch-gui
rm -f /etc/udev/rules.d/60-wayswitch.rules /etc/modules-load.d/wayswitch.conf
rm -f /usr/local/share/applications/ru.siberia.WaySwitch.desktop
rm -f /usr/local/share/applications/ru.siberia.WaySwitch.Settings.desktop
rm -f /usr/local/share/icons/hicolor/scalable/apps/ru.siberia.WaySwitch.svg
rm -rf /usr/local/share/plasma/plasmoids/ru.siberia.wayswitch
rm -f /etc/xdg/autostart/wayswitch-session.desktop
rm -rf /usr/local/share/wayswitch
rm -f /usr/local/share/icons/hicolor/symbolic/apps/ru.siberia.WaySwitch-symbolic.svg
rm -f /usr/local/share/icons/hicolor/symbolic/apps/ru.siberia.WaySwitch-paused-symbolic.svg

# Мост доступности: вернуть значение, которое было до установки.
if [ -f "$STATE/a11y-prev-$SUDO_USER" ]; then
    PREV="$(cat "$STATE/a11y-prev-$SUDO_USER")"
    if [ "$PREV" = "true" ] || [ "$PREV" = "false" ]; then
        as_user gsettings set org.gnome.desktop.interface toolkit-accessibility "$PREV" \
            2>/dev/null || true
        echo "toolkit-accessibility возвращён в $PREV"
    fi
    rm -f "$STATE/a11y-prev-$SUDO_USER"
fi
rmdir "$STATE" 2>/dev/null || true

as_user systemctl --user daemon-reload 2>/dev/null || true
udevadm control --reload-rules || true
gtk-update-icon-cache -f -t /usr/local/share/icons/hicolor >/dev/null 2>&1 || true
update-desktop-database /usr/local/share/applications >/dev/null 2>&1 || true
echo "WaySwitch удалён. Настройки остались в $USER_HOME/.config/wayswitch."
