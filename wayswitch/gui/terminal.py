"""Обновление и удаление кнопкой: команда установки в окне терминала.

Обновить или удалить WaySwitch — та же команда, что в README
(`curl … get.sh | sh`, для удаления — с `--uninstall`). Человеку не нужно
искать её на GitHub: окно открывает терминал и сразу её запускает. Терминал
нужен, а не тихий запуск в фоне: скрипт спрашивает подтверждение и пароль
администратора и показывает, что делает.

Модуль чистый: какие программы есть в системе, спрашивается через which().
"""

from __future__ import annotations

import shlex
import shutil
from collections.abc import Callable

from wayswitch.i18n import gettext as _

GET_SH_URL = "https://raw.githubusercontent.com/Wetoshkin/WaySwitch/main/packaging/get.sh"

# Терминалы и как передать им команду: «--» / «-e» / «-x» / просто аргументами.
# Для «string» команда передаётся одной строкой (такие терминалы режут
# аргументы сами).
TERMINALS: dict[str, tuple[str, ...]] = {
    "xdg-terminal-exec": (),
    "ptyxis": ("--",),
    "kgx": ("--",),
    "gnome-terminal": ("--",),
    "konsole": ("-e",),
    "xfce4-terminal": ("-x",),
    "mate-terminal": ("-x",),
    "qterminal": ("-e",),
    "lxterminal": ("string", "-e"),
    "tilix": ("string", "-e"),
    "terminator": ("-x",),
    "foot": (),
    "kitty": (),
    "alacritty": ("-e",),
    "wezterm": ("start", "--"),
    "x-terminal-emulator": ("-e",),
    "xterm": ("-e",),
}
# Свой терминал среды — первым: в KDE с установленным gnome-terminal
# открываем всё-таки Konsole.
PREFERRED = {
    "gnome": ("ptyxis", "kgx", "gnome-terminal"),
    "kde": ("konsole",),
    "xfce": ("xfce4-terminal",),
    "mate": ("mate-terminal",),
    "lxqt": ("qterminal",),
    "budgie": ("tilix", "gnome-terminal"),
    "cinnamon": ("gnome-terminal",),
    "sway": ("foot", "alacritty", "kitty"),
    "hyprland": ("kitty", "foot", "alacritty"),
}


# Что выполнит sh, если скачать скрипт не удалось: понятное сообщение, а не
# пустота после ошибки curl.
# Начинается с перевода строки: если загрузка оборвалась посреди строки,
# сообщение не склеится с её обрывком.
NO_NETWORK = ('\nCould not download WaySwitch from GitHub. Check that the internet '
              'connection works and press the button again.')
NO_CURL = ('The curl program is required. Install it from the application center '
           '(or: sudo apt install curl / sudo dnf install curl) and press the button again.')
TITLE_UPDATE = "Updating WaySwitch"
TITLE_REMOVE = "Removing WaySwitch"
PRESS_ENTER = "Press Enter to close this window. "


def message_command(message: str) -> str:
    """Команда печати сообщения для внутреннего sh."""
    # Перевод строки отделяет команду от частично скачанного скрипта.
    return "\necho " + shlex.quote(message)


def script(uninstall: bool) -> str:
    """Скрипт для терминала: скачать get.sh, запустить, дождаться Enter."""
    args = " -s -- --uninstall" if uninstall else ""
    title = _(TITLE_REMOVE if uninstall else TITLE_UPDATE)
    no_network = message_command(_(NO_NETWORK))
    no_curl = message_command(_(NO_CURL))
    return (
        f'printf "\\n  %s\\n\\n" {shlex.quote(title)}; '
        f'URL={shlex.quote(GET_SH_URL)}; '
        'if command -v curl >/dev/null 2>&1; then '
        f'curl -fsSL "$URL" || echo {shlex.quote(no_network)}; '
        f'else echo {shlex.quote(no_curl)}; fi'
        f' | sh{args}; '
        f'printf "\\n%s " {shlex.quote(_(PRESS_ENTER))}; read _'
    )


def terminal_argv(command: str, desktop: str = "",
                  which: Callable[[str], str | None] = shutil.which) -> list[str] | None:
    """Команда запуска терминала с `sh -c command` или None — терминала нет."""
    order = list(PREFERRED.get(desktop, ())) + [t for t in TERMINALS
                                                if t not in PREFERRED.get(desktop, ())]
    for name in order:
        if name not in TERMINALS or which(name) is None:
            continue
        style = TERMINALS[name]
        if style and style[0] == "string":
            return [name, *style[1:], shlex.join(["sh", "-c", command])]
        return [name, *style, "sh", "-c", command]
    return None


def manual_command(uninstall: bool) -> str:
    """Та же команда для ручного запуска (если терминал не нашёлся)."""
    return f"curl -fsSL {GET_SH_URL} | sh" + (" -s -- --uninstall" if uninstall else "")
