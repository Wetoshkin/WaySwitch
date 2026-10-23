#!/bin/bash
# Установка WaySwitch: пакеты, код в /usr/local, правило udev, user-сервис
# для вызвавшего пользователя, расширение GNOME Shell в его профиль.
#
#   sudo packaging/install.sh            — установить или обновить
#   sudo packaging/install.sh --no-a11y  — не включать мост доступности
#
# Обновление из исходников: git pull && sudo packaging/install.sh — скрипт
# идемпотентен, повторный запуск заменяет код и сохраняет настройки.
#
# На Debian/Ubuntu предпочтительнее готовый .deb-пакет
# (wayswitch_<версия>_all.deb со страницы релизов, `sudo apt install ./...`):
# он ставит зависимости через apt и чище удаляется.
set -euo pipefail

WITH_A11Y=1
for arg in "$@"; do
    case "$arg" in
        --no-a11y) WITH_A11Y=0 ;;
        *) echo "Неизвестный параметр: $arg" >&2; exit 1 ;;
    esac
done

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
STATE=/var/lib/wayswitch
BIN=/usr/local/bin
USER_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
USER_GID="$(id -gn "$SUDO_USER")"
USER_UID="$(id -u "$SUDO_USER")"

# Команда от имени пользователя с доступом к его сессии: sudo сбрасывает
# окружение, и без этих переменных gsettings и systemctl --user молча пишут
# не туда (или никуда).
as_user() {
    sudo -u "$SUDO_USER" env XDG_RUNTIME_DIR="/run/user/$USER_UID" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$USER_UID/bus" HOME="$USER_HOME" "$@"
}

echo "== Пакеты"
if command -v apt-get >/dev/null; then
    apt-get update
    apt-get install -y python3 python3-evdev python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 \
        gir1.2-ibus-1.0 libxkbcommon0
elif command -v dnf >/dev/null; then
    dnf install -y python3 python3-evdev python3-gobject gtk4 libadwaita ibus libxkbcommon
elif command -v pacman >/dev/null; then
    # Arch/CachyOS: --needed не переустанавливает уже стоящее. Без -y: обновлять
    # систему целиком — решение человека, а не установщика.
    if ! pacman -S --needed --noconfirm python python-evdev python-gobject gtk4 libadwaita \
            ibus libxkbcommon; then
        echo "pacman не смог поставить пакеты. Если ошибка 404 — сначала обновите" >&2
        echo "систему: sudo pacman -Syu, затем запустите установку снова." >&2
        exit 1
    fi
else
    echo "Неизвестный пакетный менеджер: поставьте python3-evdev, PyGObject, GTK4, libadwaita, IBus вручную."
fi

# Абсолютный путь к системному Python: если в PATH первым стоит venv или
# pyenv, `python3` окажется без PyGObject и обёртки упадут с «No module named gi».
PY=/usr/bin/python3
if [ ! -x "$PY" ]; then
    PY="$(command -v python3 || true)"
fi
if [ -z "$PY" ]; then
    echo "Не найден python3" >&2
    exit 1
fi

if [ -f /usr/lib/systemd/user/wayswitch.service ] && dpkg -s wayswitch >/dev/null 2>&1; then
    echo "   ВНИМАНИЕ: установлен и .deb-пакет wayswitch. Две копии мешают друг"
    echo "   другу — оставьте одну: sudo apt remove wayswitch или этот скрипт."
fi

echo "== Код → $LIB"
rm -rf "$LIB"
mkdir -p "$LIB"
cp -r "$SRC/wayswitch" "$LIB/"
find "$LIB" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
cat > "$BIN/wayswitch" <<EOF
#!/bin/sh
export PYTHONPATH="$LIB\${PYTHONPATH:+:\$PYTHONPATH}"
exec $PY -m wayswitch "\$@"
EOF
cat > "$BIN/wayswitch-gui" <<EOF
#!/bin/sh
export PYTHONPATH="$LIB\${PYTHONPATH:+:\$PYTHONPATH}"
exec $PY -c "from wayswitch.gui.app import main; raise SystemExit(main())" "\$@"
EOF
chmod +x "$BIN/wayswitch" "$BIN/wayswitch-gui"

echo "== udev и uinput"
install -m 644 "$SRC/packaging/60-wayswitch.rules" /etc/udev/rules.d/60-wayswitch.rules
mkdir -p /etc/modules-load.d
echo uinput > /etc/modules-load.d/wayswitch.conf
if ! modprobe uinput 2>/dev/null; then
    # Модули работающего ядра могли исчезнуть после обновления: до перезагрузки
    # /dev/uinput отдаёт ENODEV, и печатать демону нечем.
    echo "   ВНИМАНИЕ: модуль uinput сейчас не загрузился (обновлялось ядро?) —"
    echo "   он подхватится при следующей загрузке; без него демон не печатает."
fi
udevadm control --reload-rules || true
udevadm trigger --subsystem-match=input --subsystem-match=misc || true

# Среда определяет, чем переключается раскладка. Переменную берём у менеджера
# пользователя: окружение sudo её не содержит.
DESKTOP="$(as_user systemctl --user show-environment 2>/dev/null \
    | sed -n 's/^XDG_CURRENT_DESKTOP=//p' || true)"
if [ -z "$DESKTOP" ]; then
    SESSION="$(loginctl list-sessions --no-legend 2>/dev/null \
        | awk -v u="$SUDO_USER" '$3 == u {print $1; exit}' || true)"
    if [ -n "$SESSION" ]; then
        DESKTOP="$(loginctl show-session "$SESSION" -p Desktop --value 2>/dev/null || true)"
    fi
fi
case "$(echo "$DESKTOP" | tr "[:upper:]" "[:lower:]")" in
    *kde*|*plasma*) DE=kde ;;
    *gnome*)        DE=gnome ;;
    *)              DE=other ;;
esac
# Есть gnome-shell — расширение ставим в любом случае: установка могла идти
# из TTY или ssh, где среду не определить.
if [ "$DE" = "other" ] && command -v gnome-shell >/dev/null 2>&1; then
    DE=gnome
fi
echo "== Среда: ${DESKTOP:-не определена} (режим $DE)"

echo "== Мост доступности (at-spi)"
# Без него программы не отдают дерево доступности, и WaySwitch не может понять,
# что печатают в исключённой программе или в поле пароля. Прежнее значение
# сохраняем — uninstall.sh вернёт его.
mkdir -p "$STATE"
if [ "$WITH_A11Y" = 1 ] && command -v gsettings >/dev/null 2>&1; then
    PREV="$(as_user gsettings get org.gnome.desktop.interface toolkit-accessibility 2>/dev/null || true)"
    if [ -n "$PREV" ] && [ ! -f "$STATE/a11y-prev-$SUDO_USER" ]; then
        echo "$PREV" > "$STATE/a11y-prev-$SUDO_USER"
    fi
    if as_user gsettings set org.gnome.desktop.interface toolkit-accessibility true 2>/dev/null \
        && [ "$(as_user gsettings get org.gnome.desktop.interface toolkit-accessibility 2>/dev/null)" = "true" ]; then
        echo "   включено: программы отдают дерево доступности"
    else
        echo "   не удалось включить — исключения по программам и защита паролей не будут"
        echo "   работать. Вручную: gsettings set org.gnome.desktop.interface toolkit-accessibility true"
    fi
else
    echo "   пропущено (--no-a11y или нет gsettings): исключения по программам и защита"
    echo "   паролей работать не будут"
fi

echo "== Значки и ярлык"
ICONS=/usr/local/share/icons/hicolor
mkdir -p "$ICONS/scalable/apps" "$ICONS/symbolic/apps" /usr/local/share/applications
install -m 644 "$SRC/packaging/icons/ru.siberia.WaySwitch.svg" "$ICONS/scalable/apps/"
install -m 644 "$SRC/packaging/icons/ru.siberia.WaySwitch-symbolic.svg" "$ICONS/symbolic/apps/"
install -m 644 "$SRC/packaging/icons/ru.siberia.WaySwitch-paused-symbolic.svg" "$ICONS/symbolic/apps/"
# Имя записи совпадает с APP_ID окна (ru.siberia.WaySwitch.Settings): иначе
# GNOME не связывает запущенное окно с ярлыком и рисует заглушку вместо значка.
install -m 644 "$SRC/packaging/ru.siberia.WaySwitch.Settings.desktop" \
    /usr/local/share/applications/ru.siberia.WaySwitch.Settings.desktop
# Старое имя (до 2.0) — убрать, иначе в списке приложений две записи.
rm -f /usr/local/share/applications/ru.siberia.WaySwitch.desktop
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$ICONS" >/dev/null 2>&1 || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/local/share/applications >/dev/null 2>&1 || true
fi

echo "== systemd user-сервис для $SUDO_USER"
UNIT_DIR="$USER_HOME/.config/systemd/user"
sudo -u "$SUDO_USER" mkdir -p "$UNIT_DIR"
install -o "$SUDO_USER" -g "$USER_GID" -m 644 "$SRC/packaging/wayswitch.service" "$UNIT_DIR/wayswitch.service"
as_user systemctl --user daemon-reload 2>/dev/null || true

EXT_UUID=wayswitch@siberia.ru
if [ "$DE" = "gnome" ]; then
    echo "== Расширение GNOME Shell"
    EXT_DIR="$USER_HOME/.local/share/gnome-shell/extensions/$EXT_UUID"
    # Каталог — целиком заново: файлы, удалённые в новой версии, не должны остаться.
    rm -rf "$EXT_DIR"
    sudo -u "$SUDO_USER" mkdir -p "$EXT_DIR"
    cp -r "$SRC/extension/$EXT_UUID"/* "$EXT_DIR/"
    chown -R "$SUDO_USER:$USER_GID" "$EXT_DIR"
    # Включаем сразу. Только что скопированное расширение Shell на Wayland
    # увидит после перелогина, поэтому при неудаче gnome-extensions вписываем
    # его в список включённых напрямую — подхватится при следующем входе.
    if ! as_user gnome-extensions enable "$EXT_UUID" 2>/dev/null; then
        CURRENT="$(as_user gsettings get org.gnome.shell enabled-extensions 2>/dev/null || echo "@as []")"
        case "$CURRENT" in
            *"$EXT_UUID"*) ;;
            "@as []"|"[]") as_user gsettings set org.gnome.shell enabled-extensions "['$EXT_UUID']" 2>/dev/null || true ;;
            *) as_user gsettings set org.gnome.shell enabled-extensions "${CURRENT%]}, '$EXT_UUID']" 2>/dev/null || true ;;
        esac
    fi
else
    echo "== Расширение GNOME Shell не нужно (среда $DE)"
fi

if [ -f "$UNIT_DIR/../../autostart/ru.siberia.WaySwitch.desktop" ]; then
    # Автозапуск значка под старым именем (до 2.0.0a2): иначе два значка.
    mv "$UNIT_DIR/../../autostart/ru.siberia.WaySwitch.desktop" \
       "$UNIT_DIR/../../autostart/ru.siberia.WaySwitch.Settings.desktop"
fi

if as_user systemctl --user is-active --quiet wayswitch 2>/dev/null; then
    echo "== Перезапуск службы (обновление)"
    as_user systemctl --user restart wayswitch || true
fi

cat <<EOF

Готово. Дальше от имени пользователя $SUDO_USER:
  1. Выйдите из сеанса и войдите снова: правила udev и расширение применяются
     к новому сеансу.
  2. Откройте WaySwitch из меню приложений — первый запуск проверит систему
     и включит службу. Или вручную: systemctl --user enable --now wayswitch
  3. wayswitch doctor — подробная проверка в терминале.
$(if [ "$DE" = "kde" ]; then
    echo "  В KDE раскладку переключает сам KWin (org.kde.keyboard), расширение не нужно."
  elif [ "$DE" = "other" ]; then
    echo "  В этой среде раскладку прочитать неоткуда: автоисправление выключено,"
    echo "  работает только ручной жест."
  fi)
EOF
