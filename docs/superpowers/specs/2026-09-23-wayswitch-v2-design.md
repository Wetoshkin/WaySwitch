# WaySwitch v2 — проектная спецификация

Дата: 2026-09-23. Статус: утверждается.

## 1. Цель

Аналог Punto Switcher для GNOME на Wayland: демон замечает слово, набранное не в той
раскладке (ru ↔ en), и мгновенно перепечатывает его в правильной, переключая
системную раскладку. Плюс ручное исправление по жесту, откат, обучение на откатах,
окно настроек и значок в трее.

Критерии успеха первой версии:

- Исправление слова визуально выглядит как одно «моргание»: ≤ 80 мс на слово из
  10 букв на живой системе (без учёта задержек самого приложения).
- На словарных словах ложных срабатываний ≤ 0,5 %; на словах длиной ≥ 4, набранных
  не в той раскладке, полнота ≥ 95 % (проверяется автотестом на корпусе).
- Без GUI демон работает как systemd user-сервис; без демона GUI показывает
  «демон не запущен» и не падает.
- Первый запуск в VM: `wayswitch doctor` даёт понятный вердикт по каждому
  требованию, `wayswitch dry-run` показывает решения без действий.

## 2. Область

В первой версии:

- Окружение: GNOME Shell 46–49 на Wayland (Ubuntu 24.04–25.10, Fedora 40+).
- Раскладки: ровно две xkb-раскладки, одна кириллическая `ru` (любой вариант) и одна
  латинская (`us` с любым вариантом, включая dvorak). Если у пользователя другой
  набор — авторежим отключается с понятным сообщением, ручной жест работает.
- Языки движка: русский и английский.
- Всё написано с нуля; сторонние проекты в коде и документации не упоминаются.

Вне первой версии (roadmap, раздел 12): KDE, Sway/Hyprland, третьи языки, поля
паролей через AT-SPI, исправление выделенного текста, привилегированный helper.

## 3. Ключевые решения и почему

| Решение | Альтернатива | Почему так |
|---|---|---|
| Чтение клавиш через evdev (`/dev/input/event*`), клавиатуру **не захватываем** | grab + проброс через себя | Захват подменяет физическое устройство виртуальным (LED, автоповтор), зависший демон = мёртвая клавиатура. Гонка «перепечатка vs новые нажатия» решается прерыванием: физическое нажатие во время перепечатки её отменяет |
| Печать через uinput | libei/порталы, IM-движок IBus | uinput требует тех же прав, что и чтение evdev, и работает во всех приложениях. IM-движок не видит клавиш в приложениях без text-input и требует вторую реализацию для других окружений |
| Состояние раскладки — через **IBus** (`IBus.Bus`), потому что GNOME на Wayland сам управляет xkb-раскладками как движками `xkb:us::eng`, `xkb:ru::rus` | gsettings `current` (устарел, игнорируется), Shell.Eval (закрыт) | Ничего не нужно ставить; сигнал `global-engine-changed` даёт событие смены; `set_global_engine()` переключает раскладку, и Shell обновляет индикатор |
| Расширение GNOME Shell — **необязательный ускоритель**: точное состояние, синхронное переключение, печать любого keysym через `Clutter.VirtualInputDevice.notify_keyval` без ожидания смены раскладки | сделать расширение единственным путём | Расширения ломаются на каждом релизе GNOME и требуют перелогина. Базовая работа не должна от него зависеть |
| Таблицы «код клавиши → символ» берём из **libxkbcommon** для реальных раскладок пользователя (список и `xkb-options` из gsettings) | зашитая QWERTY/ЙЦУКЕН | Перепечатка идёт **по символам**, а не по скан-кодам: точка в ru лежит на другой клавише, чем в en; dvorak и `ru(phonetic)` работают сами собой |
| Ожидание смены раскладки — по **событию**, не по `sleep` | фиксированные паузы | Сигнал `global-engine-changed` + небольшой настраиваемый запас (`settle_ms`, по умолчанию 30). С расширением — ноль ожидания |
| Оценка слова на **пробеле**, обе гипотезы (текущая и другая раскладка) | триггер по триграммам внутри слова | Внутри слова нельзя отличить `,` от `б` и `.` от `ю`; на пробеле у нас полное слово и терминатор |
| Python 3.11+, зависимости только `python3-evdev` и `PyGObject` | Rust-демон | Задержка исправления — это компоситор и приложение, не интерпретатор; один стек для демона, IBus, D-Bus и GTK4 |

## 4. Архитектура

Два процесса, связь по сессионной шине D-Bus.

```
┌─ wayswitchd (systemd --user) ───────────────────────────────────────┐
│ keys.py ──events──▶ buffer.py ──на пробеле──▶ detector.py           │
│   ▲ evdev, hot-plug     ▲ Shift/Caps          │ решение              │
│   │                     │ сброс по мыши       ▼                      │
│ backends/gnome_*.py ◀──▶ daemon.py ────────▶ actuator.py (uinput)   │
│   IBus | extension | hotkey    │ D-Bus ru.siberia.WaySwitch          │
│ keymap.py (xkbcommon) ◀────────┘                                     │
└──────────────────────────────────────────────────────────────────────┘
                                   ▲ D-Bus
┌─ wayswitch-gui ──────────────────┴───────────────────────────────────┐
│ GTK4/libadwaita окно настроек · StatusNotifierItem (трей) · тест-поле│
└──────────────────────────────────────────────────────────────────────┘
```

Структура репозитория:

```
wayswitch/
  __init__.py       версия
  __main__.py       python -m wayswitch → cli
  cli.py            wayswitch run|doctor|devices|dry-run|pause|resume|fix|gui
  config.py         загрузка/валидация/запись ~/.config/wayswitch/config.toml
  keys.py           evdev: поиск устройств по capabilities, hot-plug, поток событий
  keymap.py         ctypes-обёртка libxkbcommon: код+мод → символ по группам,
                    символ → (код, shift) по группам, класс символа
  buffer.py         буфер фразы/слова, Shift/Caps, правила сброса   [чистая логика]
  detector.py       ru/en оценка, стоп-правила, порог, исключения   [чистая логика]
  actuator.py       план действий (Backspace×N, switch, символы) и его исполнение
  daemon.py         GLib main loop, оркестрация, D-Bus-сервис
  backends/
    base.py         интерфейс LayoutBackend
    gnome_ibus.py   IBus: состояние + переключение
    gnome_shell.py  наше расширение: состояние + переключение + печать keysym
    hotkey.py       резерв: эмуляция системного хоткея из gsettings
  gui/
    app.py          Adw.Application, окно настроек, тест-поле, страница статуса
    tray.py         StatusNotifierItem + dbusmenu напрямую через Gio.DBus
  data/
    ru.words.gz, en.words.gz        частотные списки (слово, ранг)
    ru.ngrams.json.gz, en.ngrams.json.gz  логвероятности символьных триграмм
extension/wayswitch@siberia.ru/     расширение GNOME Shell (ESM, 45+)
  extension.js, metadata.json
packaging/
  60-wayswitch.rules, wayswitch.service, wayswitch-gui.desktop,
  ru.siberia.WaySwitch.desktop, install.sh, uninstall.sh
tools/build_data.py                 скачивает корпуса и собирает data/
tests/                              pytest
docs/                               спецификации, планы
```

Файлы v1 (`daemon.py`, `gui.py`, `trigrams.json`, `install.sh` в корне,
`PROJECT_CONTEXT.md`) удаляются; их роль берут на себя пакет и `CLAUDE.md`
проекта. `logo.svg` и README остаются (README переписывается).

## 5. Модули

### 5.1 keys.py — устройства ввода

- Обходит `/dev/input/event*`, открывает каждое; клавиатура = есть `EV_KEY` и
  поддерживает `KEY_A`, `KEY_Z`, `KEY_SPACE`, `KEY_ENTER`; мышь/тачпад = есть
  `BTN_LEFT`. Имена не используются. Наше собственное uinput-устройство
  (`WaySwitch Virtual Keyboard`) исключается по имени.
- Hot-plug: `Gio.FileMonitor` на `/dev/input` (без pyudev), новые `event*`
  открываются с задержкой 300 мс (udev успевает выставить права).
- Каждое устройство читается через `GLib.io_add_watch(fd)`; события отдаются
  подписчику как `KeyEvent(code, value, device_id, ts)`; `value` ∈ {0, 1, 2}.
- Мышь: любое нажатие `BTN_*` → событие `PointerClick`.
- `leds(device)` → состояние CapsLock (сверяется при каждом нажатии CapsLock).
- Устройство без прав на чтение → warning один раз, продолжаем с остальными.

### 5.2 keymap.py — раскладки

- ctypes к `libxkbcommon.so.0`: `xkb_context_new`, `xkb_keymap_new_from_names`
  (rules `evdev`, `layout="us,ru"`, `variant`, `options` из gsettings
  `org.gnome.desktop.input-sources xkb-options`), `xkb_state_new`,
  `xkb_state_update_mask`, `xkb_state_key_get_utf8`.
- API:
  - `Keymap.from_names(layouts: list[LayoutSpec], options) -> Keymap`
  - `char(code, group, shift, caps) -> str` (evdev-код + 8 = xkb keycode)
  - `key_for(ch, group) -> (code, shift) | None` — обратная таблица, строится один
    раз на группу.
  - `is_letter_key(code) -> bool` — символ буквенный хотя бы в одной группе
    (так `,` `.` `;` `'` `[` `]` `` ` `` попадают в слово: в ru это б ю ж э х ъ ё).
  - `alphabet(group) -> "ru" | "latin" | other` — по символам `KEY_A..KEY_Z`.
- Все таблицы кешируются в словари; в горячем пути вызовов libxkbcommon нет.

### 5.3 buffer.py — буфер набора (чистая логика)

Состояние: список слов, каждое слово — список `KeyPress(code, shift, caps)`;
текущее незавершённое слово; метка времени последнего нажатия.

Правила:

- Буквенная клавиша (`is_letter_key`) и цифры, `-`, `=`: добавить в текущее слово.
  Цифры и `=` внутри слова не мешают буферу, ими займётся детектор.
- `KEY_SPACE`: завершить слово → событие `WordCompleted(word_keys)`. Пробел
  сохраняется как разделитель для фразы.
- `KEY_BACKSPACE`: удалить последнее нажатие; если удаляем разделитель — слово
  снова становится текущим; если буфер пуст — `reset()` (мы не знаем, что перед
  курсором).
- Полный сброс: Enter, KP_Enter, Tab, Esc, стрелки, Home/End/PgUp/PgDn, Delete,
  Insert, F1–F24, любая клавиша при зажатых Ctrl/Alt/Super, нажатие Super, клик
  мыши, пауза дольше `phrase_timeout` (по умолчанию 8 с), смена раскладки не нами.
- Shift: отслеживаем `KEY_LEFTSHIFT`/`KEY_RIGHTSHIFT` (value 1/2 — зажат, 0 —
  отпущен). CapsLock — из LED.
- Лимиты: 64 слова / 512 нажатий; старое отбрасывается.
- Методы: `feed(event) -> BufferEvent | None`, `last_word()`, `phrase()`,
  `reset()`, `replace_last_word(keys)` (после исправления буфер отражает то,
  что на экране).

### 5.4 detector.py — решение (чистая логика)

Вход: `Word(keys)`, `current_group`, `other_group`, `Keymap`, контекст (язык двух
предыдущих завершённых слов), `Exceptions`. Выход:
`Decision(action: keep | fix, target_text: str, confidence: float, reason: str)`.

Алгоритм:

1. Декодируем обе гипотезы посимвольно: `A = decode(keys, current)`,
   `B = decode(keys, other)`. Отделяем хвостовую пунктуацию: `core` + `tail`
   (хвост — не-буквы в конце строки в данной декодировке). Пример: `ghbdtn?` в en →
   A = `ghbdtn` + `?`; в ru → B = `привет` + `,`.
2. Стоп-правила (сразу `keep`): длина `core` < `min_length` (3); есть цифры;
   смешанные алфавиты в `core`; camelCase (заглавная не на первой позиции при
   строчных вокруг); похоже на URL/путь/e-mail/команду (`://`, `/`, `\`, `@`, `~`,
   `--`); `core` целиком в верхнем регистре и короче 5 (аббревиатура); `A.core` в
   личных исключениях.
3. Валидность гипотезы: `core` состоит только из букв своего алфавита (плюс `-`,
   `'`). Невалидная гипотеза не рассматривается. Обе невалидны → `keep`.
4. Словарь: `A.core.lower() ∈ dict(lang(A))` → `keep` (то, что на экране, —
   слово). Иначе `B.core.lower() ∈ dict(lang(B))` → `fix`, confidence 1.0.
5. Иначе n-граммы: `score(s, lang)` = средняя логвероятность символьных триграмм
   с паддингом `^`/`$` и сглаживанием; `Δ = score(B) − score(A)`. `fix`, если
   `Δ ≥ margin` (по умолчанию 1.5 нат/символ) **и** `score(B) ≥ floor`
   (по умолчанию −6.0: B должно быть само по себе правдоподобно).
6. Контекст: если два предыдущих слова — словарные слова языка A, требуемый
   `margin` удваивается (пользователь явно пишет на языке A).
7. `target_text = B.core + tail_B` — хвост берётся из декодировки B, потому что
   пользователь нажимал клавиши, думая, что находится в раскладке B.

Чувствительность в настройках (`conservative | normal | aggressive`) задаёт тройку
`(min_length, margin, floor)`; `normal` = (3, 1.5, −6.0).

Данные: частотные списки по 50 000 слов ru/en (источник — открытый корпус
субтитров, лицензия CC-BY-SA, указывается в `data/LICENSE`), триграммы обучаются
`tools/build_data.py` на тех же списках с весом по частоте. Загрузка при старте
≤ 150 мс, память ≤ 30 МБ.

### 5.5 actuator.py — действия

Разделено на **план** и **исполнение**, чтобы план тестировался без железа.

- `plan_fix(original_len: int, target_text: str, target_group, keymap, caps_on)`
  → `Plan([ReleaseModifiers, Backspace(n), SwitchLayout(group), Type(chars)])`,
  где `Type` уже разрешён в список `(code, shift)` через `keymap.key_for`. Символ,
  которого нет в целевой группе → план невозможен → `keep` с записью в лог.
  При включённом CapsLock `shift` для букв инвертируется.
- `n` = длина слова + 1 (пробел-терминатор уже на экране); перепечатываем слово и
  пробел.
- Исполнитель `UInputTypist`: uinput-устройство с полным набором `KEY_*`,
  создаётся один раз. `ReleaseModifiers` — отпускает все Shift/Ctrl/Alt/Super.
  `Backspace(n)` — n пар press/release с `syn()` после каждой, без пауз.
  `SwitchLayout` — `backend.set(group)` и ожидание `backend.wait_applied(group,
  timeout=500ms)` (внутри — сигнал + `settle_ms`). `Type` — press/release с syn,
  без пауз; опция `key_delay_ms` (по умолчанию 0) на случай капризных приложений.
- Исполнитель `ShellTypist` (если доступно расширение): `Backspace(n)` и `Type`
  уходят в расширение как keysym'ы; `SwitchLayout` синхронный; uinput не
  используется.
- Прерывание: исполнитель проверяет флаг `abort` между шагами; демон ставит его
  при физическом нажатии во время исполнения. При прерывании буфер сбрасывается.
- Пока исполняется план, события от нашего виртуального устройства в буфер не
  попадают (фильтр по `device_id`).

### 5.6 backends/ — раскладка

Интерфейс `LayoutBackend`:

```
layouts() -> list[LayoutSpec]        # порядок = индексы групп xkb
current() -> int | None
set(index) -> None
wait_applied(index, timeout) -> bool
on_change(callback(index, external: bool))
name -> str
```

- `gnome_ibus.py`: `IBus.Bus()` (адрес из `~/.config/ibus/bus/`, env не нужен);
  список из gsettings `org.gnome.desktop.input-sources sources`; соответствие
  движка `xkb:<layout>:<variant>:<lang>` ↔ `('xkb', 'layout+variant')`;
  `get_global_engine`, `set_global_engine`, сигнал `global-engine-changed`.
  `wait_applied` = сигнал + `settle_ms`. Только xkb-источники; при наличии других
  движков в списке — предупреждение, авторежим выключен.
- `gnome_shell.py`: D-Bus `ru.siberia.WaySwitch.Shell` на сессионной шине
  (см. раздел 7). `wait_applied` возвращает сразу.
- `hotkey.py`: читает `org.gnome.desktop.wm.keybindings switch-input-source`,
  эмулирует комбинацию через uinput, `current()` = собственный счётчик,
  `wait_applied` = `settle_ms` × 3. Используется только когда первые два
  недоступны; авторежим при нём выключен (состояние ненадёжно), ручной жест
  работает.

Выбор при старте: `gnome_shell` если имя на шине есть → `gnome_ibus` если IBus
отвечает и все источники xkb → `hotkey`. Смена доступности расширения на лету
переключает бэкенд (наблюдение за именем на шине).

### 5.7 daemon.py — оркестрация

- GLib main loop; в нём evdev, D-Bus, IBus, таймеры.
- Конечный автомат: `idle → typing → (fix running) → idle`; `paused`.
- На `WordCompleted`: если авторежим включён, бэкенд знает раскладку и это
  ru/latin пара → `detector` → при `fix` исполнить план, запомнить `LastFix(
  original_keys, target_text, group_before, ts)`, `buffer.replace_last_word()`,
  сигнал D-Bus `Corrected`.
- Жест: двойной Shift (любой из двух, второе нажатие в пределах 400 мс, без
  других клавиш между) → **ручное исправление последнего слова**: обе декодировки
  без оценки, просто перевести в другую раскладку. Если слово пустое → только
  переключить раскладку. Третье нажатие в пределах 400 мс после второго →
  **фраза**: Backspace на всю фразу, перепечатать все слова в новой раскладке
  (раскладка уже переключена вторым нажатием, ждать нечего). Текст каждого слова =
  `decode(keys, new_group)`; для уже исправленного последнего слова это верно,
  потому что `replace_last_word()` хранит клавиши, разрешённые в новой группе, —
  двойного переворота не происходит.
- Откат/обучение: жест в течение `undo_window` (5 с) после автоисправления
  переводит слово обратно **и** добавляет исходное слово в
  `~/.config/wayswitch/exceptions.txt`. Backspace откатом не считается (пользователь
  может просто убирать пробел).
- Внешняя смена раскладки (сигнал с `external=True`) → `buffer.reset()`.
- Пауза: `Pause()`/`Resume()` по D-Bus, плюс автопауза на `pause_hotkey`
  (по умолчанию не задан).
- Конфиг перечитывается по `Reload()` и по `Gio.FileMonitor` на файле.
- Логи в journal (`logging` → stderr); `--verbose` показывает каждое решение
  детектора с обеими гипотезами и оценками.

### 5.8 cli.py

- `wayswitch run [--verbose] [--dry-run]` — демон; `--dry-run` = всё как обычно,
  но вместо исполнения плана — лог «исправил бы X → Y».
- `wayswitch doctor` — проверки: сессия Wayland + GNOME, IBus отвечает и список
  движков, источники в gsettings (ровно две, ru + latin), `/dev/uinput` открывается
  на запись, клавиатуры/мыши читаются, libxkbcommon загружается и компилирует
  keymap, расширение на шине, systemd-юнит, права udev. Каждая строка — ✓/✗ и
  подсказка, что исправить. Код выхода 1 при любом ✗ из обязательных.
- `wayswitch devices` — таблица устройств с типом и правами.
- `wayswitch pause|resume|fix|status` — D-Bus вызовы к демону.
- `wayswitch gui` — запуск GUI.

## 6. Конфигурация

`~/.config/wayswitch/config.toml`, читается `tomllib`, пишется собственным
минимальным сериализатором (плоские секции, без зависимостей).

```toml
[general]
auto_correct = true
sensitivity = "normal"        # conservative | normal | aggressive
phrase_timeout_sec = 8
undo_window_sec = 5

[gesture]
manual = "double_shift"       # double_shift | pause_key | none
pause_hotkey = ""             # напр. "scroll_lock"

[typing]
settle_ms = 30                # запас после сигнала о смене раскладки (IBus)
key_delay_ms = 0

[backend]
prefer = "auto"               # auto | shell | ibus | hotkey
```

`exceptions.txt` — по слову в строке, регистр не важен. Правится и из GUI.

## 7. Расширение GNOME Shell (необязательное)

`extension/wayswitch@siberia.ru/`, ESM, `shell-version: ["46","47","48","49"]`.
Экспортирует на сессионной шине `ru.siberia.WaySwitch.Shell`,
объект `/ru/siberia/WaySwitch/Shell`:

```
GetLayout() -> (u index, s id)
SetLayout(u index)                    # InputSourceManager.inputSources[i].activate()
signal LayoutChanged(u index)         # current-source-changed
TypeText(s text)                      # VirtualInputDevice.notify_keyval по символам
Backspace(u count)
```

`notify_keyval` печатает keysym независимо от раскладки (mutter резервирует код
для отсутствующих keysym'ов), поэтому при наличии расширения ожидание смены
раскладки не нужно и uinput для печати не используется. Демон без расширения
работает полностью.

## 8. GUI

`wayswitch-gui` — `Adw.Application` (`ru.siberia.WaySwitch`), одно окно:

- Страница «Статус»: демон запущен/нет (по наличию имени на шине), бэкенд,
  текущая раскладка, последние 20 исправлений (из сигнала `Corrected`), кнопка
  «Пауза».
- Страница «Настройки»: переключатели и выпадающие списки, отражающие config.toml;
  сохранение → запись файла → `Reload()`.
- Страница «Исключения»: список слов с добавлением/удалением.
- Страница «Автозапуск»: `systemctl --user enable/disable wayswitch.service`,
  автозапуск трея через XDG autostart.
- Тест-поле: `Adw.EntryRow` «Наберите здесь `ghbdtn ` — слово должно
  исправиться», удобно для первого запуска в VM.

Трей: `StatusNotifierItem` через Gio.DBus (`org.kde.StatusNotifierWatcher`,
`com.canonical.dbusmenu`): иконка с текущим состоянием (активен/пауза), меню
«Автоисправление ✓», «Пауза», «Исправить последнее слово», «Настройки», «Выход».
На GNOME трей виден при расширении AppIndicator (в Ubuntu включено по умолчанию);
без него GUI работает как обычное окно. Библиотека appindicator не используется
(она GTK3-only).

## 9. D-Bus демона

Имя `ru.siberia.WaySwitch`, объект `/ru/siberia/WaySwitch`, интерфейс
`ru.siberia.WaySwitch.Daemon`:

```
Pause(), Resume(), FixLastWord(), FixPhrase(), Reload()
GetStatus() -> a{sv}  # active, paused, backend, layout_index, layout_id,
                      # auto_correct, corrections_total, version
signal StatusChanged(a{sv})
signal Corrected(s original, s fixed, b manual)
```

Второй экземпляр демона видит занятое имя и завершается с кодом 3.

## 10. Установка

`packaging/install.sh` (под sudo, Debian/Ubuntu и Fedora):

1. Пакеты: `python3-evdev python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-ibus-1.0
   libxkbcommon0` (dnf-эквиваленты).
2. Копирует пакет в `/usr/local/lib/wayswitch`, лаунчеры `/usr/local/bin/wayswitch`
   и `wayswitch-gui`.
3. `/etc/udev/rules.d/60-wayswitch.rules`:
   ```
   KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"
   SUBSYSTEM=="input", KERNEL=="event*", ENV{ID_INPUT_KEYBOARD}=="1", TAG+="uaccess"
   SUBSYSTEM=="input", KERNEL=="event*", ENV{ID_INPUT_MOUSE}=="1", TAG+="uaccess"
   SUBSYSTEM=="input", KERNEL=="event*", ENV{ID_INPUT_TOUCHPAD}=="1", TAG+="uaccess"
   ```
   `uaccess` даёт права пользователю активного сеанса, группы не нужны.
   `/etc/modules-load.d/wayswitch.conf` с `uinput`; `udevadm control --reload &&
   udevadm trigger`.
4. `~/.config/systemd/user/wayswitch.service` для вызвавшего пользователя
   (`After=graphical-session.target`, `Restart=on-failure`), `.desktop` для GUI и
   XDG-autostart для трея. Включение — через GUI или
   `systemctl --user enable --now wayswitch`.
5. Расширение: копируется в `~/.local/share/gnome-shell/extensions/`, включается
   пользователем (`gnome-extensions enable wayswitch@siberia.ru` после перелогина).
   Установщик только сообщает об этом.

`uninstall.sh` убирает всё перечисленное. `.deb` — следующий этап.

Замечание по безопасности: `uaccess` на клавиатуры означает, что любая программа
текущего пользователя может читать нажатия. Это цена схемы evdev; вариант с
привилегированным helper'ом — в roadmap.

## 11. Тестирование

Разработка идёт на Windows, живой GNOME появится позже в VM, поэтому:

- `buffer`, `detector`, `actuator.plan_fix`, `config`, автомат демона — чистые
  модули с pytest, работают на любой ОС. В тестах используется `FakeKeymap` с
  зашитыми таблицами us/ru (QWERTY/ЙЦУКЕН) — он же документирует ожидаемое
  поведение `keymap.py`.
- `keymap.py` тестируется против настоящего libxkbcommon: `pytest.mark.skipif`
  без библиотеки; в CI (GitHub Actions, ubuntu-latest) библиотека есть.
- Тест качества детектора: из словарей откладывается выборка (10 % слов), из неё
  генерируются «ошибочные» наборы (слово ru, набранное в en, и наоборот);
  проверяются пороги из раздела 1. Выборка фиксируется seed'ом.
- Сценарные тесты демона: `FakeDevice` подаёт последовательность `KeyEvent`,
  `FakeBackend` и `RecordingTypist` фиксируют вызовы; проверяются сценарии:
  автоисправление, `keep` на словарном слове, двойной/тройной Shift, откат с
  занесением в исключения, прерывание физическим нажатием, сброс по клику мыши,
  внешняя смена раскладки, пауза.
- Интеграция на VM (чек-лист в `docs/testing-vm.md`): `doctor` → `dry-run` в
  терминале и в браузере → боевой режим → тест-поле GUI → замер времени
  исправления по логу (`--verbose` печатает длительность плана).
- CI: `ruff`, `pytest`, сборка данных проверяется на детерминированность.

## 12. Roadmap после первой версии

1. KDE Plasma: бэкенд `org.kde.keyboard` (`getLayout/setLayout/layoutChanged`),
   `active_app()` через KWin scripting; исключения по приложениям.
2. Sway/Hyprland: IPC-бэкенды, выравнивание раскладок виртуального устройства.
3. Поля паролей и активное приложение через AT-SPI на любом окружении.
4. Исправление выделенного текста (буфер обмена через портал).
5. Третьи языки: словарь + n-граммы как подключаемый пакет, выбор целевой
   раскладки из нескольких.
6. Привилегированный helper вместо `uaccess` на клавиатуры.
7. `.deb`/`.rpm`, Flatpak невозможен (нужен доступ к `/dev/input`).

## 13. Открытые вопросы, проверяемые в VM

1. `set_global_engine()` в IBus действительно переключает раскладку GNOME Shell
   (ожидание: да). Если нет — расширение становится обязательным для авторежима.
2. Реальный `settle_ms` для IBus-пути.
3. Поведение Backspace в браузерной адресной строке с автодополнением
   (возможно, потребуется исключение по приложению — но активное окно в GNOME без
   расширения неизвестно; расширение может добавить `GetFocusedApp()`).
