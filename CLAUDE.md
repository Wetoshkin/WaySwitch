# CLAUDE.md

Инструкции для агентов и разработчиков, работающих в этом репозитории.

## Что это за проект

WaySwitch — аналог Punto Switcher для GNOME на Wayland: демон на Python
читает клавиатуру через `evdev`, решает по словарю и символьным триграммам,
что слово набрано не в той раскладке (ru ↔ en), переключает раскладку через
IBus/расширение GNOME Shell и перепечатывает слово через `uinput`. Плюс
ручной жест (двойной/тройной Shift), откат с обучением, GTK4-окно настроек
и трей. Подробное обоснование решений — в спецификации, пошаговый план
реализации — в плане (пути ниже).

Разработка ведётся на Windows; живой GNOME пока не тестировался — см.
`docs/testing-vm.md`.

## Карта модулей

| Модуль | Роль |
|---|---|
| `wayswitch/keycodes.py` | имена/коды клавиш evdev, таблицы модификаторов |
| `wayswitch/config.py` | `~/.config/wayswitch/config.toml` — загрузка, валидация, запись; `exceptions.txt` |
| `wayswitch/keymap.py` | раскладки: `Keymap` (интерфейс), `XkbKeymap` — обёртка над `libxkbcommon` через ctypes |
| `wayswitch/ngram.py` | символьные триграммы, логвероятность строки в языке |
| `wayswitch/detector.py` | решение `keep`/`fix` по слову: словарь → триграммы → контекст; ранний триггер URL |
| `wayswitch/buffer.py` | буфер слова/фразы: Shift/CapsLock, правила сброса, `held_physical()` |
| `wayswitch/gesture.py` | распознавание двойного/тройного Shift |
| `wayswitch/actuator.py` | план действий (`plan_fix`) и его исполнение; `UInputTypist` — печать через `uinput` |
| `wayswitch/controller.py` | оркестрация без привязки к GLib: события клавиш → детектор → действия |
| `wayswitch/keys.py` | обход `/dev/input/event*`, hot-plug, классификация устройств, `SYN_DROPPED`/`ENODEV` |
| `wayswitch/session.py` | охрана сессии: блокировка экрана (`org.gnome.ScreenSaver`), `logind` |
| `wayswitch/daemon.py` | сборка демона: GLib main loop, устройства, бэкенд, контроллер, D-Bus |
| `wayswitch/dbus_service.py` | D-Bus сервис `ru.siberia.WaySwitch` (`Pause`/`Resume`/`FixLastWord`/`GetStatus`/…) |
| `wayswitch/cli.py` | подкоманды `wayswitch` |
| `wayswitch/doctor.py` | проверки окружения для `wayswitch doctor` |
| `wayswitch/backends/base.py` | интерфейс `LayoutBackend` |
| `wayswitch/backends/gnome_ibus.py` | бэкенд через `IBus.Bus` |
| `wayswitch/backends/gnome_shell.py` | бэкенд через расширение (`ru.siberia.WaySwitch.Shell`), `ShellTypist` |
| `wayswitch/backends/hotkey.py` | резервный бэкенд: эмуляция системного хоткея переключения раскладки |
| `wayswitch/backends/select.py` | выбор бэкенда при старте (`shell` → `ibus` → `hotkey`) |
| `wayswitch/gui/app.py` | `Adw.Application`, окно настроек, тест-поле |
| `wayswitch/gui/tray.py` | `StatusNotifierItem` через `Gio.DBus` |
| `wayswitch/gui/autostart.py` | автозапуск сервиса и трея (`systemctl --user`, XDG autostart) |
| `wayswitch/gui/daemon_proxy.py` | клиент D-Bus демона для GUI |
| `extension/wayswitch@siberia.ru/` | расширение GNOME Shell (ESM, `shell-version` 46–50) |
| `packaging/` | udev-правило, systemd-юнит, `.desktop`, `install.sh`/`uninstall.sh` |
| `tools/build_data.py` | сборка `wayswitch/data/*.gz` (словари, триграммы) из открытого корпуса |
| `tests/` | pytest; `tests/e2e_vm.py` — сквозной тест, гоняется руками в VM |

## Чистые модули

`keycodes`, `config`, `keymap` (кроме `XkbKeymap`), `ngram`, `detector`,
`buffer`, `gesture`, `actuator` (кроме `UInputTypist`), `controller` —
**никогда не импортируют `evdev`/`gi` на уровне модуля**. Это то, что
позволяет разрабатывать и тестировать логику на Windows без живого GNOME.
Любой новый импорт `evdev`/`gi.repository` в этих модулях должен быть
локальным (внутри функции), а не в начале файла.

## Тесты

```bash
pip install pytest ruff
python -m pytest -q
python -m ruff check .
```

На Windows пропускаются (`skipif`) только два теста `keymap.py` против
настоящего `libxkbcommon` в `tests/test_keymap_xkb.py`, которым нужна
`libxkbcommon.so.0` — всё остальное должно быть зелёным. В CI (GitHub
Actions, `ubuntu-latest`) библиотека есть, эти тесты тоже выполняются.
Не полагайтесь на конкретное число тестов в документации — оно растёт с
каждой задачей; ориентир — сама команда `python -m pytest -q`.
`tests/e2e_vm.py` не часть `pytest` — отдельный скрипт, запускается
руками на живой VM с открытым текстовым полем в фокусе.

## Данные

`wayswitch/data/{ru,en}.words.gz` и `{ru,en}.ngrams.json.gz` пересобираются
командой:

```bash
python tools/build_data.py
```

Источник — открытый частотный корпус (см. `wayswitch/data/LICENSE`,
CC-BY-SA 4.0, не MIT). Сборка детерминирована.

## Спецификация, план, прогресс

- Спецификация (архитектура, решения и почему): `docs/superpowers/specs/2026-09-23-wayswitch-v2-design.md`.
- План реализации по задачам: `docs/superpowers/plans/2026-09-23-wayswitch-v2.md`.
- Трекер прогресса — обновлять после каждой задачи: `docs/PROGRESS.md`.
- Рабочий журнал SDD (брифы, отчёты, ревью задач) — `.superpowers/sdd/` —
  **в git не попадает** (см. `.gitignore`), это рабочие файлы сессий, не
  часть репозитория.

## Коммиты

Сообщения — на русском, в императиве («feat: …», «fix: …», «docs: …»),
без трейлеров соавторства и упоминаний инструментов. Автор — владелец
репозитория.

## Расширение GNOME Shell

`extension/wayswitch@siberia.ru/` — ESM-модуль (не legacy `imports.*`),
`metadata.json` объявляет `shell-version` (список поддерживаемых версий
GNOME Shell). При правке расширения проверять, что `shell-version` в
`metadata.json` соответствует тому, на что реально рассчитан код.
