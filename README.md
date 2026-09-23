<p align="center">
  <img src="logo.svg" width="128" height="128" alt="WaySwitch">
</p>

# WaySwitch

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Platform: GNOME Wayland](https://img.shields.io/badge/platform-GNOME%20Wayland-orange.svg)](https://wayland.freedesktop.org/)

**WaySwitch** — аналог Punto Switcher для GNOME на Wayland: демон замечает
слово, набранное не в той раскладке (ru ↔ en), и мгновенно перепечатывает
его в правильной, переключая системную раскладку.

Принцип: демон читает клавиатуру напрямую через `evdev` (без захвата
устройства), по словарю и символьным триграммам решает, набрано ли слово
не в той раскладке, переключает раскладку через IBus или собственное
расширение GNOME Shell и перепечатывает слово через виртуальную клавиатуру
(`uinput`).

## Возможности

- Автоисправление слова на пробеле — по словарю и символьным триграммам.
- Ранний триггер URL: `http://`/`https://`/`ftp://`, набранные не в той
  раскладке, исправляются сразу, не дожидаясь пробела.
- Ручное исправление жестом: двойной Shift — последнее слово, тройной —
  вся фраза.
- Откат жестом и обучение: тот же двойной Shift в течение нескольких
  секунд после автоисправления возвращает слово и запоминает его как
  исключение.
- Охрана сессии: ничего не исправляется, пока экран заблокирован или
  демон на паузе.
- Окно настроек (GTK4/libadwaita) и значок в трее.
- `wayswitch doctor` — диагностика окружения перед первым запуском,
  `wayswitch dry-run` — решения без действий.

## Установка

```bash
git clone https://github.com/Wetoshkin/WaySwitch.git wayswitch
cd wayswitch
sudo packaging/install.sh
```

Затем от обычного пользователя:

```bash
# перелогиньтесь — правила udev применяются к новому сеансу
wayswitch doctor
systemctl --user enable --now wayswitch
wayswitch-gui
```

`packaging/install.sh` ставит системные пакеты (`python3-evdev`,
`python3-gi`, GTK4/libadwaita, IBus, `libxkbcommon`), копирует код в
`/usr/local/lib/wayswitch`, кладёт правило udev на `/dev/uinput` и
клавиатуры/мыши, systemd user-юнит и расширение GNOME Shell в профиль
вызвавшего пользователя (`sudo <скрипт>` от обычного пользователя, не от
root). Расширение включается отдельно: `gnome-extensions enable
wayswitch@siberia.ru` (после перелогина).

## Использование

| Жест / команда | Действие |
|---|---|
| Двойной Shift (любой, второй тап в пределах 0,4 с) | исправить последнее слово; при пустом буфере — просто переключить раскладку |
| Тройной Shift (третий тап в пределах того же окна) | исправить всю фразу в раскладке, которую выбрал двойной Shift |
| Двойной Shift в течение `undo_window_sec` после автоисправления | откатить и запомнить слово как исключение |

Жесты не работают на паузе и при заблокированном экране.

CLI:

```
wayswitch run [--verbose] [--dry-run] [--config PATH]   демон
wayswitch dry-run                                        демон в режиме наблюдения (verbose, без действий)
wayswitch doctor                                          диагностика окружения
wayswitch devices                                         список устройств ввода
wayswitch pause | resume                                  пауза/возобновление по D-Bus
wayswitch fix                                              исправить последнее слово по D-Bus
wayswitch status                                           состояние демона
wayswitch gui                                              открыть окно настроек
wayswitch version
wayswitch-gui [--tray]                                    настройки; --tray — сразу в трей, без окна
```

## Настройка

`~/.config/wayswitch/config.toml` (создаётся при первом сохранении из GUI
или руками):

```toml
[general]
auto_correct = true
sensitivity = "normal"        # conservative | normal | aggressive
phrase_timeout_sec = 8.0
undo_window_sec = 5.0

[gesture]
manual = "double_shift"       # double_shift | pause_key | none
pause_hotkey = ""             # например "scroll_lock"

[typing]
settle_ms = 30                # запас после сигнала о смене раскладки (путь IBus)
key_delay_ms = 0

[backend]
prefer = "auto"               # auto | shell | ibus | hotkey
```

`~/.config/wayswitch/exceptions.txt` — слова, которые никогда не
исправляются автоматически (по слову в строке, регистр не важен);
пополняется откатом жестом или из GUI.

## Как это работает

```
keys.py (evdev) → buffer.py (буфер слова/фразы, Shift) → на пробеле → detector.py (решение)
                                                                            │
backends/gnome_*.py (IBus | расширение | хоткей) ◀── daemon.py/controller.py ──▶ actuator.py (uinput)
```

Два процесса на сессионной шине D-Bus: демон `wayswitch run` (systemd
user-сервис `wayswitch.service`) и `wayswitch-gui` (окно настроек и трей). Состояние раскладки читается и
меняется через IBus (`IBus.Bus`, `xkb:us::eng`/`xkb:ru::rus`); необязательное
расширение GNOME Shell (`ru.siberia.WaySwitch.Shell`) даёт точное состояние,
синхронное переключение и печать без ожидания. Подробности и обоснование
решений — в спецификации `docs/superpowers/specs/2026-09-23-wayswitch-v2-design.md`.

## Ограничения

- Демон не видит текст в полях паролей (GtkPasswordEntry и т. п.) — на время
  ввода пароля используйте паузу.
- Приложения с автодополнением (например, адресная строка браузера) могут
  вести себя неожиданно при перепечатке.
- Если исправление прервано (физическое нажатие клавиши во время
  перепечатки), уже отправленные Backspace и символы не откатываются —
  попытка «починить» вслепую только усугубила бы расхождение с экраном.
- Правило udev `uaccess` даёт чтение событий клавиатуры любой программе
  текущего пользователя, не только WaySwitch — это цена схемы на `evdev`.
- Ничего из этого пока не проверялось на живой GNOME — только на юнит-тестах;
  чек-лист первого запуска в VM — `docs/testing-vm.md`.

## Разработка

```bash
pip install pytest ruff
python -m pytest -q
python -m ruff check .
```

Разработка идёт на Windows, поэтому два теста `keymap.py`, завязанных на
реальный `libxkbcommon` (`tests/test_keymap_xkb.py`), пропускаются
(`skipif`) — они выполняются в CI (GitHub Actions, `ubuntu-latest`). Всё
остальное должно быть зелёным на любой ОС. Словарные данные пересобираются
командой:

```bash
python tools/build_data.py
```

Для сессии Claude Code — `CLAUDE.md`. Первый запуск на живой системе —
`docs/testing-vm.md`. Прогресс по задачам плана — `docs/PROGRESS.md`.

## Лицензии

Код — MIT (`LICENSE`). Словарные данные и триграммы
(`wayswitch/data/*.gz`) собраны из открытого частотного корпуса
и распространяются по CC-BY-SA 4.0 — подробности в
`wayswitch/data/LICENSE`.
