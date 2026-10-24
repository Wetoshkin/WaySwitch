# CLAUDE.md

Инструкции для агентов и разработчиков, работающих в этом репозитории.

## Что это за проект

WaySwitch — аналог Punto Switcher для Wayland (GNOME 46–51, KDE Plasma 6, Sway,
Hyprland, Xfce/MATE/Cinnamon/Budgie/LXQt на labwc и Wayfire):
демон на Python читает клавиатуру через `evdev`, решает по словарю и
символьным триграммам, что слово набрано не в той раскладке (ru ↔ en),
переключает раскладку через расширение GNOME Shell или KWin и перепечатывает
слово. Плюс ручные жесты, «Исправить выделенное», откат с обучением, окно на
GTK4/libadwaita и значок в панели. Подробное обоснование решений — в спецификации, пошаговый план
реализации — в плане (пути ниже).

Разработка ведётся на Windows; живьём проверено на CachyOS, GNOME 51
(Wayland). KDE Plasma 6 живьём не проверялся. GUI можно проверить без
GNOME: в контейнере Debian с GTK4/libadwaita под Xvfb и поддельным демоном
на D-Bus (так сняты `docs/screenshots`). Модули `gui/*` импортируют `gi`
только внутри функций — это проверяет тест.

## Карта модулей

| Модуль | Роль |
|---|---|
| `wayswitch/keycodes.py` | имена/коды клавиш evdev, таблицы модификаторов, клавиши жестов |
| `wayswitch/config.py` | `~/.config/wayswitch/config.toml` — загрузка, валидация, запись; `exceptions.txt`, `apps.txt` |
| `wayswitch/keymap.py` | раскладки: `Keymap` (интерфейс), `XkbKeymap` — обёртка над `libxkbcommon` через ctypes |
| `wayswitch/ngram.py` | символьные триграммы, логвероятность строки в языке |
| `wayswitch/detector.py` | решение `keep`/`fix` по слову: словарь → триграммы → контекст; правила знаков, частиц, слипшихся слов, двух заглавных |
| `wayswitch/buffer.py` | буфер слова/фразы: Shift/CapsLock, правила сброса, `held_physical()` |
| `wayswitch/gesture.py` | двойной/тройной Shift, одиночный тап своей клавиши |
| `wayswitch/actuator.py` | план (`plan_fix`) и исполнение с точкой невозврата на первом Backspace; `UInputTypist` |
| `wayswitch/selection.py` | «Исправить выделенное»: перевод текста в другую раскладку |
| `wayswitch/textops.py` | транслитерация и число прописью для выделенного текста |
| `wayswitch/controller.py` | оркестрация без GLib: события клавиш → правила/детектор → действия, откат, выделение |
| `wayswitch/a11y.py` | at-spi: программа в фокусе и поле пароля (поток-проба, обход от активного окна) |
| `wayswitch/sound.py` | звук при исправлении |
| `wayswitch/panel.py` | модуль Waybar (`wayswitch waybar`), сводка состояния, фрагменты конфигов Sway/Hyprland |
| `wayswitch/updates.py` | проверка новых версий на GitHub Releases (только GUI, суточный кэш) |
| `wayswitch/keys.py` | обход `/dev/input/event*`, hot-plug, классификация устройств, `SYN_DROPPED`/`ENODEV` |
| `wayswitch/session.py` | охрана сессии: блокировка экрана (ScreenSaver GNOME/freedesktop, процессы swaylock/hyprlock/…), `logind` |
| `wayswitch/daemon.py` | сборка демона: GLib main loop, устройства, бэкенд, контроллер, D-Bus |
| `wayswitch/dbus_service.py` | D-Bus сервис `ru.siberia.WaySwitch` (`Pause`/`Resume`/`FixLastWord`/`FixSelection`/`GetStatus`/…) |
| `wayswitch/cli.py` | подкоманды `wayswitch` |
| `wayswitch/doctor.py` | проверки окружения для `wayswitch doctor` и шага «Проверка системы» |
| `wayswitch/backends/base.py` | интерфейс `LayoutBackend` |
| `wayswitch/backends/desktop.py` | определение среды (GNOME/KDE/прочие), Wayland |
| `wayswitch/backends/gnome_common.py` | источники ввода и хоткей из gsettings (с проверкой схемы) |
| `wayswitch/backends/gnome_shell.py` | бэкенд через расширение (`ru.siberia.WaySwitch.Shell`), `ShellTypist`, выделение |
| `wayswitch/backends/gnome_ibus.py` | бэкенд через `IBus.Bus` (на GNOME 46+ не переключает раскладку — только страховка) |
| `wayswitch/backends/kde_plasma.py` | KDE Plasma 6: D-Bus KWin `org.kde.keyboard`, варианты из `kxkbrc` |
| `wayswitch/backends/sway.py` | Sway: i3-ipc (раскладки, переключение, событие input) |
| `wayswitch/backends/hyprland.py` | Hyprland: `.socket.sock` / `.socket2.sock` |
| `wayswitch/backends/compositor.py` | labwc / Wayfire (Xfce, MATE, Budgie, LXQt): XKB из конфига, переключение сочетанием, счётчик |
| `wayswitch/backends/xkbnames.py` | описание раскладки («Russian») → идентификатор XKB по evdev.xml |
| `wayswitch/backends/hotkey.py` | резерв: эмуляция системного хоткея GNOME |
| `wayswitch/backends/select.py` | выбор бэкенда по среде |
| `wayswitch/gui/app.py` | `Adw.Application`: окна, действия и горячие клавиши, значок, уведомления, обновления |
| `wayswitch/gui/window.py` | главное окно: состояние, проверка, история; страница «не запущен» |
| `wayswitch/gui/preferences.py` | «Параметры» (`AdwPreferencesDialog`): Исправление, Исключения, Система |
| `wayswitch/gui/dialogs.py` | выбор клавиши, добавить приложение, жесты, журнал, «О WaySwitch», подтверждения |
| `wayswitch/gui/onboarding.py` | первый запуск (`AdwCarousel`, графический doctor) |
| `wayswitch/gui/texts.py` | чистые тексты и модели окна (статус, история, онбординг) — тестируются без GTK |
| `wayswitch/gui/style.py` | акцент «терракота»/«шалфей» для libadwaita 1.5 и 1.6+ |
| `wayswitch/gui/widgets.py` | строка «значение · − +», клавиша-надпись, работа в потоке |
| `wayswitch/gui/gesture_keys.py` | человеческие имена клавиш и разбор нажатия в диалоге выбора |
| `wayswitch/gui/tray.py` | `StatusNotifierItem` + dbusmenu через `Gio.DBus` |
| `wayswitch/gui/terminal.py` | обновление и удаление кнопкой: get.sh в окне терминала |
| `wayswitch/gui/autostart.py` | служба и автозапуск значка (`systemctl --user`, XDG autostart) |
| `wayswitch/gui/daemon_proxy.py` | клиент D-Bus демона для GUI |
| `extension/wayswitch@siberia.ru/` | расширение GNOME Shell (ESM, `shell-version` 46–51); печатать может только демон |
| `packaging/` | udev-правило, systemd-юнит, `.desktop`, значки, `install.sh`/`uninstall.sh`, `get.sh` (одной командой), `deb/`, `rpm/`, `arch/PKGBUILD`, `kde/` (виджет лотка Plasma 6) |
| `tools/stage.sh` | раскладка файлов пакета — общая для .deb, .rpm и AUR |
| `tools/build_rpm.sh` | сборка `.rpm` |
| `tools/aur_update.py` | sha256 архива релиза и `.SRCINFO` для AUR (`--check` — только сверка) |
| `tools/aur_publish.sh` | push PKGBUILD/.SRCINFO в AUR (см. `docs/aur.md`) |
| `tools/build_data.py` | сборка `wayswitch/data/*.gz` (словари, триграммы) из открытого корпуса |
| `tools/build_deb.sh` | сборка `.deb` |
| `tools/live_rules.py` | живой прогон правил без окон |
| `tests/` | pytest; `tests/e2e_vm.py` — сквозной тест, гоняется руками в VM |

## Чистые модули

`keycodes`, `config`, `keymap` (кроме `XkbKeymap`), `ngram`, `detector`,
`buffer`, `gesture`, `actuator` (кроме `UInputTypist`), `selection`, `textops`,
`gui/terminal`,
`updates`, `panel`, `controller`, `a11y` (кроме `query_focus`), `gui/texts`,
`gui/gesture_keys`, `backends/sway`, `backends/hyprland`, `backends/compositor`,
`backends/xkbnames` —
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
