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
NO_NETWORK = ('\necho; echo "Не удалось скачать WaySwitch с GitHub. Проверьте, что '
              'интернет работает, и нажмите кнопку ещё раз."')
NO_CURL = ('echo "Нужна программа curl. Установите её через «Центр приложений» '
           '(или: sudo apt install curl / sudo dnf install curl) и нажмите кнопку ещё раз."')


def script(uninstall: bool) -> str:
    """Скрипт для терминала: скачать get.sh, запустить, дождаться Enter."""
    args = " -s -- --uninstall" if uninstall else ""
    title = "Удаление WaySwitch" if uninstall else "Обновление WaySwitch"
    return (
        f'printf "\\n  {title}\\n\\n"; '
        f'URL={shlex.quote(GET_SH_URL)}; '
        'if command -v curl >/dev/null 2>&1; then '
        f'curl -fsSL "$URL" || echo {shlex.quote(NO_NETWORK)}; '
        f'else echo {shlex.quote(NO_CURL)}; fi'
        f' | sh{args}; '
        'printf "\\nНажмите Enter, чтобы закрыть это окно. "; read _'
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
