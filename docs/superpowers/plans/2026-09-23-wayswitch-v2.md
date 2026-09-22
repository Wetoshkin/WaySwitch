# WaySwitch v2 — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Демон + GUI для GNOME/Wayland, автоматически исправляющие слово, набранное не в той раскладке (ru ↔ en), с ручным жестом, откатом и обучением.

**Architecture:** Демон на Python читает клавиатуры через evdev (без захвата), решает на пробеле по словарю и символьным триграммам, переключает раскладку через IBus (или расширение GNOME Shell) и перепечатывает слово через uinput. Вся логика (буфер, детектор, план действий, оркестрация) — чистые модули без зависимостей от Linux, тестируемые на Windows; железо подключается тонкими адаптерами. GUI — отдельный процесс GTK4/libadwaita, связь по D-Bus.

**Tech Stack:** Python 3.11+, python-evdev, PyGObject (GLib/Gio/IBus/Gtk4/Adw), libxkbcommon через ctypes, pytest, ruff, GNOME Shell extension (ESM, JS).

**Spec:** `docs/superpowers/specs/2026-09-23-wayswitch-v2-design.md` — план аргументирует от спецификации, исполнитель читает обе.

## Global Constraints

- Python ≥ 3.11 (`tomllib` из стандартной библиотеки). Зависимости рантайма только `evdev` и `PyGObject`; никаких других пакетов из pip.
- Комментарии и докстринги в коде — **на русском**. Идентификаторы — английские.
- Сторонние проекты-аналоги в коде, комментариях и документации **не упоминаются**.
- Модули `keycodes`, `config`, `keymap` (кроме `XkbKeymap`), `ngram`, `detector`, `buffer`, `gesture`, `actuator` (кроме `UInputTypist`), `controller`, `doctor` (логика) не импортируют `evdev` и `gi` на уровне модуля — тесты запускаются на Windows. Всё, что трогает `evdev`/`gi`, импортирует их внутри функций/классов.
- Разработка идёт на Windows: `pytest` доступен, `evdev`/`gi`/libxkbcommon — нет. Тесты, требующие их, помечаются `pytest.mark.skipif`. Файлы для Linux (`.sh`, `.service`, `.rules`, `.js`) — LF (`.gitattributes` уже задан).
- Целевое окружение: GNOME Shell 46–49 на Wayland, две xkb-раскладки: одна `ru*`, одна латинская.
- Коммиты: сообщение на русском, в конце строка `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Автор коммита задаётся `git -c user.name="Wetoshkin" -c user.email="wetoshkin@gmail.com"`.
- Запуск тестов: `python -m pytest -q` из корня репозитория. Линтер: `python -m ruff check .` (установить `pip install ruff` при отсутствии).
- Имена на D-Bus: демон `ru.siberia.WaySwitch` (`/ru/siberia/WaySwitch`, интерфейс `ru.siberia.WaySwitch.Daemon`), расширение `ru.siberia.WaySwitch.Shell` (`/ru/siberia/WaySwitch/Shell`). Идентификатор приложения GUI `ru.siberia.WaySwitch`.
- Имя виртуальной клавиатуры uinput: `WaySwitch Virtual Keyboard`.

---

## Карта файлов

| Файл | Ответственность |
|---|---|
| `pyproject.toml` | метаданные, entry points `wayswitch`, `wayswitch-gui`, настройки ruff/pytest |
| `wayswitch/__init__.py` | `__version__` |
| `wayswitch/__main__.py` | `python -m wayswitch` → `cli.main()` |
| `wayswitch/keycodes.py` | константы Linux input-event-codes и наборы клавиш |
| `wayswitch/config.py` | dataclass-ы конфига, TOML load/save, исключения пользователя |
| `wayswitch/keymap.py` | `LayoutSpec`, абстрактный `Keymap`, `TableKeymap`, `XkbKeymap` (ctypes) |
| `wayswitch/ngram.py` | обучение и оценка символьных триграмм |
| `wayswitch/detector.py` | `LanguageModel`, `Detector`, `Decision` |
| `wayswitch/buffer.py` | `KeyPress`, `InputBuffer` |
| `wayswitch/gesture.py` | `ShiftGesture` (двойной/тройной Shift) |
| `wayswitch/actuator.py` | план действий, `execute`, `UInputTypist` |
| `wayswitch/controller.py` | оркестрация без GLib: `Controller` |
| `wayswitch/backends/base.py` | `LayoutBackend` |
| `wayswitch/backends/gnome_common.py` | gsettings: источники, xkb-options, системный хоткей |
| `wayswitch/backends/gnome_ibus.py` | бэкенд IBus |
| `wayswitch/backends/gnome_shell.py` | бэкенд расширения + `ShellTypist` |
| `wayswitch/backends/hotkey.py` | резервный бэкенд эмуляции хоткея |
| `wayswitch/backends/select.py` | `choose_backend` |
| `wayswitch/keys.py` | `DeviceWatcher`: evdev, hot-plug, мышь, LED |
| `wayswitch/session.py` | `SessionGuard`: блокировка экрана, сон |
| `wayswitch/dbus_service.py` | D-Bus-сервис демона |
| `wayswitch/daemon.py` | сборка и запуск демона в GLib |
| `wayswitch/doctor.py` | проверки окружения |
| `wayswitch/cli.py` | подкоманды |
| `wayswitch/gui/app.py`, `wayswitch/gui/tray.py` | GUI и трей |
| `wayswitch/data/*` | словари и n-граммы |
| `tools/build_data.py` | сборка данных |
| `extension/wayswitch@siberia.ru/*` | расширение GNOME Shell |
| `packaging/*` | udev, systemd, desktop, install/uninstall |
| `tests/*` | pytest |
| `.github/workflows/ci.yml` | CI |
| `README.md`, `CLAUDE.md`, `docs/testing-vm.md` | документация |

---

### Task 0: Каркас репозитория и удаление v1

**Files:**
- Delete: `daemon.py`, `gui.py`, `trigrams.json`, `install.sh`, `PROJECT_CONTEXT.md`, `requirements.txt`, `__init__.py` (корневой)
- Create: `pyproject.toml`, `LICENSE`, `wayswitch/__init__.py`, `wayswitch/__main__.py`, `wayswitch/keycodes.py`, `wayswitch/backends/__init__.py`, `wayswitch/gui/__init__.py`, `wayswitch/data/.gitkeep`, `tests/__init__.py`, `tests/test_keycodes.py`, `.github/workflows/ci.yml`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `wayswitch.keycodes` — константы `KEY_*`, `BTN_*`, наборы `SHIFT_KEYS`, `CTRL_KEYS`, `ALT_KEYS`, `META_KEYS`, `COMMAND_MODIFIERS`, `MODIFIER_KEYS`, `RESET_KEYS`, `DIGIT_KEYS`, `WORD_EXTRA_KEYS`, `KEY_NAMES: dict[str,int]`, функция `is_pointer_button(code) -> bool`, `key_name(code) -> str`.

- [ ] **Step 1: Удалить файлы v1**

```bash
git rm -q daemon.py gui.py trigrams.json install.sh PROJECT_CONTEXT.md requirements.txt __init__.py
```

- [ ] **Step 2: Создать `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "wayswitch"
version = "2.0.0a1"
description = "Автоисправление раскладки клавиатуры для GNOME/Wayland"
readme = "README.md"
requires-python = ">=3.11"
license = { text = "MIT" }
dependencies = ["evdev>=1.6", "PyGObject>=3.46"]

[project.scripts]
wayswitch = "wayswitch.cli:main"
wayswitch-gui = "wayswitch.gui.app:main"

[tool.setuptools.packages.find]
include = ["wayswitch*"]

[tool.setuptools.package-data]
wayswitch = ["data/*.gz", "data/LICENSE"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py311"
extend-exclude = ["extension", "packaging"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP"]
```

- [ ] **Step 3: `LICENSE` (MIT), `wayswitch/__init__.py`, `wayswitch/__main__.py`, пустые `__init__.py`**

`LICENSE` — стандартный текст MIT, `Copyright (c) 2026 Wetoshkin`.

`wayswitch/__init__.py`:
```python
"""WaySwitch — автоисправление раскладки для GNOME/Wayland."""

__version__ = "2.0.0a1"
```

`wayswitch/__main__.py`:
```python
from wayswitch.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

`wayswitch/backends/__init__.py`, `wayswitch/gui/__init__.py`, `tests/__init__.py` — пустые. `wayswitch/data/.gitkeep` — пустой.

- [ ] **Step 4: Написать тест для `keycodes`**

`tests/test_keycodes.py`:
```python
from wayswitch import keycodes as kc


def test_letter_codes_match_linux_headers():
    # Коды букв в input-event-codes.h не идут по алфавиту — проверяем точечно.
    assert kc.KEY_Q == 16 and kc.KEY_A == 30 and kc.KEY_Z == 44 and kc.KEY_M == 50
    assert kc.KEY_SPACE == 57 and kc.KEY_BACKSPACE == 14 and kc.KEY_LEFTMETA == 125


def test_sets_are_disjoint_where_expected():
    assert not (kc.SHIFT_KEYS & kc.COMMAND_MODIFIERS)
    assert kc.KEY_ENTER in kc.RESET_KEYS and kc.KEY_F12 in kc.RESET_KEYS
    assert kc.KEY_SPACE not in kc.RESET_KEYS


def test_pointer_buttons():
    assert kc.is_pointer_button(kc.BTN_LEFT)
    assert kc.is_pointer_button(0x117)
    assert not kc.is_pointer_button(kc.KEY_A)


def test_key_names_round_trip():
    assert kc.KEY_NAMES["space"] == kc.KEY_SPACE
    assert kc.KEY_NAMES["pause"] == kc.KEY_PAUSE
    assert kc.key_name(kc.KEY_LEFTSHIFT) == "leftshift"
```

- [ ] **Step 5: Запустить тест — убедиться, что падает**

Run: `python -m pytest tests/test_keycodes.py -q`
Expected: FAIL — `ModuleNotFoundError: wayswitch.keycodes`.

- [ ] **Step 6: Написать `wayswitch/keycodes.py`**

```python
"""Коды клавиш Linux (input-event-codes.h), нужные демону.

Константы продублированы, чтобы чистая логика не зависела от python-evdev
и тесты запускались на любой ОС.
"""

KEY_ESC = 1
KEY_1, KEY_2, KEY_3, KEY_4, KEY_5, KEY_6, KEY_7, KEY_8, KEY_9, KEY_0 = range(2, 12)
KEY_MINUS = 12
KEY_EQUAL = 13
KEY_BACKSPACE = 14
KEY_TAB = 15
KEY_Q, KEY_W, KEY_E, KEY_R, KEY_T, KEY_Y, KEY_U, KEY_I, KEY_O, KEY_P = range(16, 26)
KEY_LEFTBRACE = 26
KEY_RIGHTBRACE = 27
KEY_ENTER = 28
KEY_LEFTCTRL = 29
KEY_A, KEY_S, KEY_D, KEY_F, KEY_G, KEY_H, KEY_J, KEY_K, KEY_L = range(30, 39)
KEY_SEMICOLON = 39
KEY_APOSTROPHE = 40
KEY_GRAVE = 41
KEY_LEFTSHIFT = 42
KEY_BACKSLASH = 43
KEY_Z, KEY_X, KEY_C, KEY_V, KEY_B, KEY_N, KEY_M = range(44, 51)
KEY_COMMA = 51
KEY_DOT = 52
KEY_SLASH = 53
KEY_RIGHTSHIFT = 54
KEY_KPASTERISK = 55
KEY_LEFTALT = 56
KEY_SPACE = 57
KEY_CAPSLOCK = 58
KEY_F1, KEY_F2, KEY_F3, KEY_F4, KEY_F5, KEY_F6, KEY_F7, KEY_F8, KEY_F9, KEY_F10 = range(59, 69)
KEY_NUMLOCK = 69
KEY_SCROLLLOCK = 70
KEY_KP7, KEY_KP8, KEY_KP9, KEY_KPMINUS, KEY_KP4, KEY_KP5, KEY_KP6, KEY_KPPLUS = range(71, 79)
KEY_KP1, KEY_KP2, KEY_KP3, KEY_KP0, KEY_KPDOT = range(79, 84)
KEY_102ND = 86
KEY_F11 = 87
KEY_F12 = 88
KEY_KPENTER = 96
KEY_RIGHTCTRL = 97
KEY_KPSLASH = 98
KEY_RIGHTALT = 100
KEY_HOME = 102
KEY_UP = 103
KEY_PAGEUP = 104
KEY_LEFT = 105
KEY_RIGHT = 106
KEY_END = 107
KEY_DOWN = 108
KEY_PAGEDOWN = 109
KEY_INSERT = 110
KEY_DELETE = 111
KEY_PAUSE = 119
KEY_LEFTMETA = 125
KEY_RIGHTMETA = 126
KEY_COMPOSE = 127
KEY_F13, KEY_F14, KEY_F15, KEY_F16, KEY_F17, KEY_F18 = range(183, 189)
KEY_F19, KEY_F20, KEY_F21, KEY_F22, KEY_F23, KEY_F24 = range(189, 195)

BTN_LEFT = 0x110
BTN_RIGHT = 0x111
BTN_MIDDLE = 0x112
BTN_TASK = 0x117

# Наборы клавиш, которыми оперирует буфер и контроллер.
SHIFT_KEYS = frozenset({KEY_LEFTSHIFT, KEY_RIGHTSHIFT})
CTRL_KEYS = frozenset({KEY_LEFTCTRL, KEY_RIGHTCTRL})
ALT_KEYS = frozenset({KEY_LEFTALT, KEY_RIGHTALT})
META_KEYS = frozenset({KEY_LEFTMETA, KEY_RIGHTMETA})
COMMAND_MODIFIERS = CTRL_KEYS | ALT_KEYS | META_KEYS
MODIFIER_KEYS = SHIFT_KEYS | COMMAND_MODIFIERS
DIGIT_KEYS = frozenset(range(KEY_1, KEY_0 + 1))
# Клавиши, которые входят в слово помимо букв: цифры, дефис, равно.
WORD_EXTRA_KEYS = DIGIT_KEYS | {KEY_MINUS, KEY_EQUAL}
# Клавиши, после которых буфер бессмысленен: каретка переехала или ввод завершён.
RESET_KEYS = frozenset(
    {
        KEY_ENTER, KEY_KPENTER, KEY_TAB, KEY_ESC,
        KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT,
        KEY_HOME, KEY_END, KEY_PAGEUP, KEY_PAGEDOWN,
        KEY_DELETE, KEY_INSERT,
    }
    | set(range(KEY_F1, KEY_F10 + 1))
    | {KEY_F11, KEY_F12}
    | set(range(KEY_F13, KEY_F24 + 1))
)


def is_pointer_button(code: int) -> bool:
    """Кнопка мыши/тачпада: BTN_LEFT..BTN_TASK."""
    return BTN_LEFT <= code <= BTN_TASK


# Имена клавиш для конфига и разбора системного хоткея.
KEY_NAMES: dict[str, int] = {
    "space": KEY_SPACE, "tab": KEY_TAB, "enter": KEY_ENTER, "return": KEY_ENTER,
    "esc": KEY_ESC, "escape": KEY_ESC, "backspace": KEY_BACKSPACE,
    "insert": KEY_INSERT, "delete": KEY_DELETE, "home": KEY_HOME, "end": KEY_END,
    "pause": KEY_PAUSE, "scrolllock": KEY_SCROLLLOCK, "capslock": KEY_CAPSLOCK,
    "menu": KEY_COMPOSE, "compose": KEY_COMPOSE,
    "leftshift": KEY_LEFTSHIFT, "rightshift": KEY_RIGHTSHIFT, "shift": KEY_LEFTSHIFT,
    "leftctrl": KEY_LEFTCTRL, "rightctrl": KEY_RIGHTCTRL, "ctrl": KEY_LEFTCTRL,
    "control": KEY_LEFTCTRL,
    "leftalt": KEY_LEFTALT, "rightalt": KEY_RIGHTALT, "alt": KEY_LEFTALT,
    "leftmeta": KEY_LEFTMETA, "rightmeta": KEY_RIGHTMETA, "super": KEY_LEFTMETA,
    "meta": KEY_LEFTMETA, "win": KEY_LEFTMETA,
    "grave": KEY_GRAVE, "minus": KEY_MINUS, "equal": KEY_EQUAL,
    "semicolon": KEY_SEMICOLON, "apostrophe": KEY_APOSTROPHE, "comma": KEY_COMMA,
    "period": KEY_DOT, "slash": KEY_SLASH, "backslash": KEY_BACKSLASH,
    "bracketleft": KEY_LEFTBRACE, "bracketright": KEY_RIGHTBRACE,
}
KEY_NAMES.update({f"f{i}": code for i, code in enumerate(range(KEY_F1, KEY_F10 + 1), 1)})
KEY_NAMES.update({"f11": KEY_F11, "f12": KEY_F12})
KEY_NAMES.update({chr(ord("a") + i): c for i, c in enumerate(
    [KEY_A, KEY_B, KEY_C, KEY_D, KEY_E, KEY_F, KEY_G, KEY_H, KEY_I, KEY_J, KEY_K, KEY_L,
     KEY_M, KEY_N, KEY_O, KEY_P, KEY_Q, KEY_R, KEY_S, KEY_T, KEY_U, KEY_V, KEY_W, KEY_X,
     KEY_Y, KEY_Z])})
KEY_NAMES.update({str(i): c for i, c in zip([1, 2, 3, 4, 5, 6, 7, 8, 9, 0],
                                            range(KEY_1, KEY_0 + 1))})

_NAME_BY_CODE = {code: name for name, code in KEY_NAMES.items()}
# Для модификаторов оставляем полное имя (leftshift, а не shift).
for _name in ("leftshift", "rightshift", "leftctrl", "rightctrl", "leftalt", "rightalt",
              "leftmeta", "rightmeta", "enter", "esc", "period"):
    _NAME_BY_CODE[KEY_NAMES[_name]] = _name


def key_name(code: int) -> str:
    """Человекочитаемое имя клавиши; для неизвестных — число."""
    return _NAME_BY_CODE.get(code, str(code))
```

- [ ] **Step 7: Запустить тесты**

Run: `python -m pytest tests/test_keycodes.py -q`
Expected: 4 passed.

- [ ] **Step 8: CI и .gitignore**

`.github/workflows/ci.yml`:
```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - name: Системные библиотеки
        run: sudo apt-get update && sudo apt-get install -y libxkbcommon0 xkb-data
      - run: pip install pytest ruff evdev
      - run: python -m ruff check .
      - run: python -m pytest -q
      - name: Синтаксис расширения
        run: node --input-type=module --check < extension/wayswitch@siberia.ru/extension.js
```

Добавить в `.gitignore` строки `*.egg-info/`, `build/`, `dist/`, `.pytest_cache/`, `.ruff_cache/`.

- [ ] **Step 9: Коммит**

```bash
git add -A
git -c user.name="Wetoshkin" -c user.email="wetoshkin@gmail.com" commit -m "chore: каркас v2, коды клавиш, CI; удалён прототип v1

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 1: Конфигурация

**Files:**
- Create: `wayswitch/config.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `SENSITIVITY_NAMES = ("conservative", "normal", "aggressive")`, `MANUAL_MODES = ("double_shift", "pause_key", "none")`, `BACKEND_PREFS = ("auto", "shell", "ibus", "hotkey")`
  - dataclass-ы `General(auto_correct: bool=True, sensitivity: str="normal", phrase_timeout_sec: float=8.0, undo_window_sec: float=5.0)`, `Gesture(manual: str="double_shift", pause_hotkey: str="")`, `Typing(settle_ms: int=30, key_delay_ms: int=0)`, `Backend(prefer: str="auto")`, `Config(general, gesture, typing, backend)`
  - `class ConfigError(ValueError)`
  - `default_path() -> Path`, `exceptions_path() -> Path`
  - `from_dict(d: dict) -> Config`, `to_dict(cfg) -> dict`, `dumps(cfg) -> str`
  - `load(path: Path | None = None) -> Config` (нет файла → значения по умолчанию), `save(cfg, path=None) -> None`
  - `load_exceptions(path=None) -> set[str]`, `add_exception(word, path=None) -> None`, `remove_exception(word, path=None) -> None`

- [ ] **Step 1: Тест**

`tests/test_config.py`:
```python
from pathlib import Path

import pytest

from wayswitch import config as cfgmod


def test_defaults_when_file_missing(tmp_path: Path):
    cfg = cfgmod.load(tmp_path / "nope.toml")
    assert cfg.general.auto_correct is True
    assert cfg.general.sensitivity == "normal"
    assert cfg.typing.settle_ms == 30
    assert cfg.backend.prefer == "auto"


def test_round_trip(tmp_path: Path):
    cfg = cfgmod.load(None if False else tmp_path / "c.toml")
    cfg.general.sensitivity = "aggressive"
    cfg.gesture.pause_hotkey = "scrolllock"
    cfg.typing.settle_ms = 45
    cfgmod.save(cfg, tmp_path / "c.toml")
    text = (tmp_path / "c.toml").read_text(encoding="utf-8")
    assert '[general]' in text and 'sensitivity = "aggressive"' in text
    again = cfgmod.load(tmp_path / "c.toml")
    assert again == cfg


def test_invalid_values_raise():
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.from_dict({"general": {"sensitivity": "wild"}})
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.from_dict({"typing": {"settle_ms": -1}})
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.from_dict({"gesture": {"pause_hotkey": "nosuchkey"}})
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.from_dict({"general": {"unknown_key": 1}})


def test_unknown_section_ignored_but_unknown_key_rejected():
    cfg = cfgmod.from_dict({"future": {"x": 1}})
    assert cfg.general.auto_correct is True


def test_exceptions_file(tmp_path: Path):
    p = tmp_path / "exceptions.txt"
    assert cfgmod.load_exceptions(p) == set()
    cfgmod.add_exception("Ghbdtn", p)
    cfgmod.add_exception("hello", p)
    cfgmod.add_exception("hello", p)
    assert cfgmod.load_exceptions(p) == {"ghbdtn", "hello"}
    cfgmod.remove_exception("HELLO", p)
    assert cfgmod.load_exceptions(p) == {"ghbdtn"}
```

- [ ] **Step 2: Запустить — падает** (`ModuleNotFoundError`).

- [ ] **Step 3: Реализация `wayswitch/config.py`**

```python
"""Конфигурация демона: ~/.config/wayswitch/config.toml и список исключений.

Читаем через tomllib, пишем собственным минимальным сериализатором —
формат плоский (секция → ключ → скаляр), сторонняя библиотека не нужна.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from wayswitch.keycodes import KEY_NAMES

SENSITIVITY_NAMES = ("conservative", "normal", "aggressive")
MANUAL_MODES = ("double_shift", "pause_key", "none")
BACKEND_PREFS = ("auto", "shell", "ibus", "hotkey")


class ConfigError(ValueError):
    """Неверное значение в конфиге."""


@dataclass
class General:
    auto_correct: bool = True
    sensitivity: str = "normal"
    phrase_timeout_sec: float = 8.0
    undo_window_sec: float = 5.0


@dataclass
class Gesture:
    manual: str = "double_shift"
    pause_hotkey: str = ""


@dataclass
class Typing:
    settle_ms: int = 30
    key_delay_ms: int = 0


@dataclass
class Backend:
    prefer: str = "auto"


@dataclass
class Config:
    general: General = field(default_factory=General)
    gesture: Gesture = field(default_factory=Gesture)
    typing: Typing = field(default_factory=Typing)
    backend: Backend = field(default_factory=Backend)


_SECTIONS = {"general": General, "gesture": Gesture, "typing": Typing, "backend": Backend}


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "wayswitch"


def default_path() -> Path:
    return config_dir() / "config.toml"


def exceptions_path() -> Path:
    return config_dir() / "exceptions.txt"


def _section_from_dict(cls, data: dict):
    known = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        raise ConfigError(f"неизвестные ключи в [{cls.__name__.lower()}]: {sorted(unknown)}")
    kwargs = {}
    for name, f in known.items():
        if name not in data:
            continue
        value = data[name]
        expected = f.type if isinstance(f.type, type) else {"bool": bool, "str": str,
                                                              "int": int, "float": float}[f.type]
        if expected is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if expected is int and isinstance(value, bool):
            raise ConfigError(f"{name}: ожидалось число")
        if not isinstance(value, expected):
            raise ConfigError(f"{name}: ожидался тип {expected.__name__}")
        kwargs[name] = value
    return cls(**kwargs)


def _validate(cfg: Config) -> None:
    g, ge, t, b = cfg.general, cfg.gesture, cfg.typing, cfg.backend
    if g.sensitivity not in SENSITIVITY_NAMES:
        raise ConfigError(f"sensitivity: допустимо {SENSITIVITY_NAMES}")
    if not 1.0 <= g.phrase_timeout_sec <= 300.0:
        raise ConfigError("phrase_timeout_sec: 1..300")
    if not 0.0 <= g.undo_window_sec <= 60.0:
        raise ConfigError("undo_window_sec: 0..60")
    if ge.manual not in MANUAL_MODES:
        raise ConfigError(f"manual: допустимо {MANUAL_MODES}")
    if ge.pause_hotkey and ge.pause_hotkey.lower() not in KEY_NAMES:
        raise ConfigError(f"pause_hotkey: неизвестная клавиша {ge.pause_hotkey!r}")
    if not 0 <= t.settle_ms <= 2000:
        raise ConfigError("settle_ms: 0..2000")
    if not 0 <= t.key_delay_ms <= 100:
        raise ConfigError("key_delay_ms: 0..100")
    if b.prefer not in BACKEND_PREFS:
        raise ConfigError(f"prefer: допустимо {BACKEND_PREFS}")


def from_dict(data: dict) -> Config:
    """Собрать конфиг из словаря TOML. Неизвестные секции игнорируются,
    неизвестные ключи внутри известных секций — ошибка (опечатка)."""
    kwargs = {}
    for name, cls in _SECTIONS.items():
        section = data.get(name, {})
        if not isinstance(section, dict):
            raise ConfigError(f"[{name}] должна быть секцией")
        kwargs[name] = _section_from_dict(cls, section)
    cfg = Config(**kwargs)
    _validate(cfg)
    return cfg


def to_dict(cfg: Config) -> dict:
    return asdict(cfg)


def _toml_scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def dumps(cfg: Config) -> str:
    """Плоский TOML: [секция] и пары ключ = значение."""
    lines = ["# Конфигурация WaySwitch. Правится из GUI или руками.", ""]
    for name, section in to_dict(cfg).items():
        lines.append(f"[{name}]")
        for key, value in section.items():
            lines.append(f"{key} = {_toml_scalar(value)}")
        lines.append("")
    return "\n".join(lines)


def load(path: Path | None = None) -> Config:
    path = path or default_path()
    if not path.exists():
        return Config()
    with open(path, "rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{path}: {e}") from e
    return from_dict(data)


def save(cfg: Config, path: Path | None = None) -> None:
    _validate(cfg)
    path = path or default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(dumps(cfg), encoding="utf-8")
    os.replace(tmp, path)


def load_exceptions(path: Path | None = None) -> set[str]:
    """Слова, которые никогда не исправляются автоматически. Регистр не важен."""
    path = path or exceptions_path()
    if not path.exists():
        return set()
    words = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        word = line.strip().lower()
        if word and not word.startswith("#"):
            words.add(word)
    return words


def _write_exceptions(words: set[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(sorted(words)) + ("\n" if words else ""), encoding="utf-8")


def add_exception(word: str, path: Path | None = None) -> None:
    path = path or exceptions_path()
    words = load_exceptions(path)
    words.add(word.strip().lower())
    _write_exceptions(words, path)


def remove_exception(word: str, path: Path | None = None) -> None:
    path = path or exceptions_path()
    words = load_exceptions(path)
    words.discard(word.strip().lower())
    _write_exceptions(words, path)
```

Примечание: в `_section_from_dict` аннотации из-за `from __future__ import annotations` приходят строками — поэтому есть словарь имён типов.

- [ ] **Step 4: Запустить тесты** — `python -m pytest tests/test_config.py -q` → 5 passed.

- [ ] **Step 5: Коммит** `feat: конфигурация и список исключений`.

---
### Task 2: Раскладки — `keymap.py` и таблицы для тестов

**Files:**
- Create: `wayswitch/keymap.py`, `tests/fakes.py`, `tests/test_keymap.py`, `tests/test_keymap_xkb.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) LayoutSpec(layout: str, variant: str = "")`, `LayoutSpec.parse("us+dvorak") -> LayoutSpec("us","dvorak")`, `LayoutSpec.xkb_id -> "us+dvorak"`/`"us"`
  - `class Keymap` (абстрактный): `num_groups: int`, `char(code, group, shift=False, caps=False) -> str`, `key_for(ch, group) -> tuple[int, bool] | None`, `is_letter_key(code) -> bool`, `alphabet(group) -> str` (`"ru"`, `"latin"`, `"other"`), `decode(keys, group) -> str` (keys — объекты с полями `code, shift, caps`)
  - `class TableKeymap(Keymap)`: `TableKeymap(tables: list[dict[int, tuple[str, str]]])`
  - `class XkbKeymap(Keymap)`: `XkbKeymap.from_names(specs: list[LayoutSpec], options: list[str]) -> XkbKeymap`; `XkbKeymap.available() -> bool`
  - `tests/fakes.py`: `US_TABLE`, `RU_TABLE`, `make_keymap() -> TableKeymap` (группа 0 = us, 1 = ru), `US, RU = 0, 1`

- [ ] **Step 1: Тесты**

`tests/fakes.py` (часть 1 — таблицы; остальное добавят следующие задачи):
```python
"""Фейки для тестов: табличная раскладка us/ru, бэкенд, печатающая машинка."""

from wayswitch.keymap import TableKeymap

US, RU = 0, 1

_US_ROWS = [
    (2, "1!"), (3, "2@"), (4, "3#"), (5, "4$"), (6, "5%"), (7, "6^"), (8, "7&"),
    (9, "8*"), (10, "9("), (11, "0)"), (12, "-_"), (13, "=+"),
    (16, "qQ"), (17, "wW"), (18, "eE"), (19, "rR"), (20, "tT"), (21, "yY"), (22, "uU"),
    (23, "iI"), (24, "oO"), (25, "pP"), (26, "[{"), (27, "]}"),
    (30, "aA"), (31, "sS"), (32, "dD"), (33, "fF"), (34, "gG"), (35, "hH"), (36, "jJ"),
    (37, "kK"), (38, "lL"), (39, ";:"), (40, "'\""), (41, "`~"), (43, "\\|"),
    (44, "zZ"), (45, "xX"), (46, "cC"), (47, "vV"), (48, "bB"), (49, "nN"), (50, "mM"),
    (51, ",<"), (52, ".>"), (53, "/?"), (57, "  "),
]
_RU_ROWS = [
    (2, "1!"), (3, '2"'), (4, "3№"), (5, "4;"), (6, "5%"), (7, "6:"), (8, "7?"),
    (9, "8*"), (10, "9("), (11, "0)"), (12, "-_"), (13, "=+"),
    (16, "йЙ"), (17, "цЦ"), (18, "уУ"), (19, "кК"), (20, "еЕ"), (21, "нН"), (22, "гГ"),
    (23, "шШ"), (24, "щЩ"), (25, "зЗ"), (26, "хХ"), (27, "ъЪ"),
    (30, "фФ"), (31, "ыЫ"), (32, "вВ"), (33, "аА"), (34, "пП"), (35, "рР"), (36, "оО"),
    (37, "лЛ"), (38, "дД"), (39, "жЖ"), (40, "эЭ"), (41, "ёЁ"), (43, "\\/"),
    (44, "яЯ"), (45, "чЧ"), (46, "сС"), (47, "мМ"), (48, "иИ"), (49, "тТ"), (50, "ьЬ"),
    (51, "бБ"), (52, "юЮ"), (53, ".,"), (57, "  "),
]
US_TABLE = {code: (pair[0], pair[1]) for code, pair in _US_ROWS}
RU_TABLE = {code: (pair[0], pair[1]) for code, pair in _RU_ROWS}


def make_keymap() -> TableKeymap:
    return TableKeymap([US_TABLE, RU_TABLE])
```

`tests/test_keymap.py`:
```python
from dataclasses import dataclass

from tests.fakes import RU, US, make_keymap
from wayswitch import keycodes as kc
from wayswitch.keymap import LayoutSpec


@dataclass
class K:
    code: int
    shift: bool = False
    caps: bool = False


def test_layout_spec_parse():
    assert LayoutSpec.parse("us+dvorak") == LayoutSpec("us", "dvorak")
    assert LayoutSpec.parse("ru").xkb_id == "ru"
    assert LayoutSpec("us", "dvorak").xkb_id == "us+dvorak"


def test_char_by_group_and_shift():
    km = make_keymap()
    assert km.char(kc.KEY_G, US) == "g"
    assert km.char(kc.KEY_G, RU) == "п"
    assert km.char(kc.KEY_G, RU, shift=True) == "П"
    assert km.char(kc.KEY_SLASH, RU) == "."
    assert km.char(kc.KEY_SLASH, RU, shift=True) == ","


def test_caps_inverts_only_letters():
    km = make_keymap()
    assert km.char(kc.KEY_A, US, caps=True) == "A"
    assert km.char(kc.KEY_A, US, shift=True, caps=True) == "a"
    assert km.char(kc.KEY_1, US, caps=True) == "1"


def test_key_for_prefers_unshifted():
    km = make_keymap()
    assert km.key_for("п", RU) == (kc.KEY_G, False)
    assert km.key_for("П", RU) == (kc.KEY_G, True)
    assert km.key_for(",", RU) == (kc.KEY_SLASH, True)
    assert km.key_for(",", US) == (kc.KEY_COMMA, False)
    assert km.key_for("ж", US) is None
    assert km.key_for(" ", US) == (kc.KEY_SPACE, False)


def test_letter_keys_are_union_of_groups():
    km = make_keymap()
    # ; в us — не буква, но в ru это ж → клавиша считается буквенной.
    assert km.is_letter_key(kc.KEY_SEMICOLON)
    assert km.is_letter_key(kc.KEY_GRAVE)
    assert km.is_letter_key(kc.KEY_A)
    assert not km.is_letter_key(kc.KEY_1)
    assert not km.is_letter_key(kc.KEY_SPACE)


def test_alphabet():
    km = make_keymap()
    assert km.alphabet(US) == "latin"
    assert km.alphabet(RU) == "ru"


def test_decode():
    km = make_keymap()
    keys = [K(kc.KEY_G, shift=True), K(kc.KEY_H), K(kc.KEY_B), K(kc.KEY_SLASH, shift=True)]
    assert km.decode(keys, US) == "Ghb?"
    assert km.decode(keys, RU) == "При,"
```

`tests/test_keymap_xkb.py` (выполняется только на Linux с libxkbcommon):
```python
import pytest

from wayswitch import keycodes as kc
from wayswitch.keymap import LayoutSpec, XkbKeymap

pytestmark = pytest.mark.skipif(not XkbKeymap.available(), reason="нет libxkbcommon")


def test_xkb_us_ru_matches_tables():
    from tests.fakes import RU_TABLE, US_TABLE

    km = XkbKeymap.from_names([LayoutSpec("us"), LayoutSpec("ru")], [])
    assert km.num_groups == 2
    for code, (lo, hi) in US_TABLE.items():
        assert km.char(code, 0) == lo, code
        assert km.char(code, 0, shift=True) == hi, code
    for code, (lo, hi) in RU_TABLE.items():
        assert km.char(code, 1) == lo, code
        assert km.char(code, 1, shift=True) == hi, code
    assert km.alphabet(0) == "latin" and km.alphabet(1) == "ru"
    assert km.key_for("б", 1) == (kc.KEY_COMMA, False)


def test_xkb_dvorak_variant():
    km = XkbKeymap.from_names([LayoutSpec("us", "dvorak"), LayoutSpec("ru")], [])
    assert km.char(kc.KEY_Q, 0) == "'"
    assert km.key_for("q", 0) == (kc.KEY_X, False)
```

- [ ] **Step 2: Запустить `tests/test_keymap.py` — падает.**

- [ ] **Step 3: Реализация `wayswitch/keymap.py`**

```python
"""Раскладки: соответствие «код клавиши → символ» по группам xkb.

Источник правды на живой системе — libxkbcommon (XkbKeymap), собранный из тех
же раскладок, что использует GNOME. TableKeymap — то же самое из явных таблиц,
для тестов. Обе реализации строят таблицы один раз; в горячем пути только
словари.
"""

from __future__ import annotations

import ctypes
import ctypes.util
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cached_property

CYRILLIC = set("абвгдеёжзийклмнопрстуфхцчшщъыьэюя")
LATIN = set("abcdefghijklmnopqrstuvwxyz")
# Буквенные клавиши, по которым определяем алфавит группы.
_ALPHABET_PROBE = (30, 31, 32, 33, 34, 35, 36, 37, 38)  # KEY_A..KEY_L


@dataclass(frozen=True)
class LayoutSpec:
    layout: str
    variant: str = ""

    @classmethod
    def parse(cls, source_id: str) -> LayoutSpec:
        """'us+dvorak' (формат gsettings) → LayoutSpec."""
        layout, _, variant = source_id.partition("+")
        return cls(layout, variant)

    @property
    def xkb_id(self) -> str:
        return f"{self.layout}+{self.variant}" if self.variant else self.layout


class Keymap(ABC):
    """Абстрактная раскладка из нескольких групп."""

    @property
    @abstractmethod
    def num_groups(self) -> int: ...

    @abstractmethod
    def _table(self, group: int) -> dict[int, tuple[str, str]]:
        """Код клавиши → (без Shift, с Shift) для группы."""

    def char(self, code: int, group: int, shift: bool = False, caps: bool = False) -> str:
        pair = self._table(group).get(code)
        if pair is None:
            return ""
        ch = pair[1] if shift else pair[0]
        # CapsLock меняет регистр только у букв; с Shift — обратно в строчную.
        if caps and ch.isalpha():
            ch = ch.swapcase()
        return ch

    @cached_property
    def _reverse(self) -> list[dict[str, tuple[int, bool]]]:
        result = []
        for group in range(self.num_groups):
            rev: dict[str, tuple[int, bool]] = {}
            table = self._table(group)
            # Сначала символы с Shift, затем без — без Shift перекрывает.
            for code, (lo, hi) in table.items():
                if hi:
                    rev.setdefault(hi, (code, True))
            for code, (lo, hi) in table.items():
                if lo:
                    rev[lo] = (code, False)
            result.append(rev)
        return result

    def key_for(self, ch: str, group: int) -> tuple[int, bool] | None:
        return self._reverse[group].get(ch)

    @cached_property
    def _letter_keys(self) -> frozenset[int]:
        keys = set()
        for group in range(self.num_groups):
            for code, (lo, hi) in self._table(group).items():
                if lo.isalpha() or hi.isalpha():
                    keys.add(code)
        return frozenset(keys)

    def is_letter_key(self, code: int) -> bool:
        """Клавиша даёт букву хотя бы в одной группе (так `;` попадает в слово: в ru это ж)."""
        return code in self._letter_keys

    def alphabet(self, group: int) -> str:
        chars = {self.char(code, group).lower() for code in _ALPHABET_PROBE}
        chars.discard("")
        if chars and chars <= CYRILLIC:
            return "ru"
        if chars and chars <= LATIN:
            return "latin"
        return "other"

    def decode(self, keys: Iterable, group: int) -> str:
        return "".join(self.char(k.code, group, k.shift, k.caps) for k in keys)


class TableKeymap(Keymap):
    def __init__(self, tables: list[dict[int, tuple[str, str]]]):
        self._tables = tables

    @property
    def num_groups(self) -> int:
        return len(self._tables)

    def _table(self, group: int) -> dict[int, tuple[str, str]]:
        return self._tables[group]


# ---- libxkbcommon через ctypes -------------------------------------------------

class _XkbRuleNames(ctypes.Structure):
    _fields_ = [
        ("rules", ctypes.c_char_p), ("model", ctypes.c_char_p), ("layout", ctypes.c_char_p),
        ("variant", ctypes.c_char_p), ("options", ctypes.c_char_p),
    ]


_lib = None


def _load_lib():
    global _lib
    if _lib is None:
        name = ctypes.util.find_library("xkbcommon") or "libxkbcommon.so.0"
        lib = ctypes.CDLL(name)
        lib.xkb_context_new.restype = ctypes.c_void_p
        lib.xkb_context_new.argtypes = [ctypes.c_int]
        lib.xkb_context_unref.argtypes = [ctypes.c_void_p]
        lib.xkb_keymap_new_from_names.restype = ctypes.c_void_p
        lib.xkb_keymap_new_from_names.argtypes = [ctypes.c_void_p, ctypes.POINTER(_XkbRuleNames),
                                                  ctypes.c_int]
        lib.xkb_keymap_unref.argtypes = [ctypes.c_void_p]
        lib.xkb_keymap_num_layouts.restype = ctypes.c_uint32
        lib.xkb_keymap_num_layouts.argtypes = [ctypes.c_void_p]
        lib.xkb_keymap_mod_get_index.restype = ctypes.c_uint32
        lib.xkb_keymap_mod_get_index.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        lib.xkb_state_new.restype = ctypes.c_void_p
        lib.xkb_state_new.argtypes = [ctypes.c_void_p]
        lib.xkb_state_unref.argtypes = [ctypes.c_void_p]
        lib.xkb_state_update_mask.restype = ctypes.c_int
        lib.xkb_state_update_mask.argtypes = [ctypes.c_void_p] + [ctypes.c_uint32] * 6
        lib.xkb_state_key_get_utf8.restype = ctypes.c_int
        lib.xkb_state_key_get_utf8.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p,
                                               ctypes.c_size_t]
        _lib = lib
    return _lib


class XkbKeymap(Keymap):
    """Раскладка, скомпилированная libxkbcommon из имён xkb (как в GNOME)."""

    EVDEV_OFFSET = 8  # xkb keycode = evdev code + 8
    MAX_CODE = 255

    def __init__(self, tables: list[dict[int, tuple[str, str]]], specs: list[LayoutSpec]):
        self._tables = tables
        self.specs = specs

    @staticmethod
    def available() -> bool:
        try:
            _load_lib()
            return True
        except OSError:
            return False

    @classmethod
    def from_names(cls, specs: list[LayoutSpec], options: list[str]) -> XkbKeymap:
        lib = _load_lib()
        names = _XkbRuleNames(
            rules=b"evdev", model=b"pc105",
            layout=",".join(s.layout for s in specs).encode(),
            variant=",".join(s.variant for s in specs).encode(),
            options=",".join(options).encode(),
        )
        ctx = lib.xkb_context_new(0)
        if not ctx:
            raise RuntimeError("xkb_context_new")
        try:
            keymap = lib.xkb_keymap_new_from_names(ctx, ctypes.byref(names), 0)
            if not keymap:
                raise RuntimeError(f"xkb: не удалось собрать раскладку {names.layout!r}")
            try:
                groups = lib.xkb_keymap_num_layouts(keymap)
                shift_idx = lib.xkb_keymap_mod_get_index(keymap, b"Shift")
                tables = [cls._build_table(lib, keymap, g, 1 << shift_idx) for g in range(groups)]
            finally:
                lib.xkb_keymap_unref(keymap)
        finally:
            lib.xkb_context_unref(ctx)
        return cls(tables, specs)

    @classmethod
    def _build_table(cls, lib, keymap, group: int, shift_mask: int) -> dict[int, tuple[str, str]]:
        table: dict[int, tuple[str, str]] = {}
        buf = ctypes.create_string_buffer(16)
        for shift in (False, True):
            state = lib.xkb_state_new(keymap)
            try:
                lib.xkb_state_update_mask(state, shift_mask if shift else 0, 0, 0, 0, 0, group)
                for code in range(1, cls.MAX_CODE + 1):
                    n = lib.xkb_state_key_get_utf8(state, code + cls.EVDEV_OFFSET, buf, 16)
                    ch = buf.value[:n].decode("utf-8", "replace") if n > 0 else ""
                    lo, hi = table.get(code, ("", ""))
                    table[code] = (lo, ch) if shift else (ch, hi)
            finally:
                lib.xkb_state_unref(state)
        # Управляющие символы (Enter → \r, Tab → \t, Esc) словом не являются.
        return {c: (lo, hi) for c, (lo, hi) in table.items()
                if (lo and lo.isprintable()) or (hi and hi.isprintable())}

    @property
    def num_groups(self) -> int:
        return len(self._tables)

    def _table(self, group: int) -> dict[int, tuple[str, str]]:
        return self._tables[group]
```

- [ ] **Step 4: Запустить `python -m pytest tests/test_keymap.py tests/test_keymap_xkb.py -q`** → 7 passed, 2 skipped (на Windows).

- [ ] **Step 5: Коммит** `feat: раскладки через libxkbcommon и табличная раскладка для тестов`.

---

### Task 3: Триграммы и сборка данных

**Files:**
- Create: `wayswitch/ngram.py`, `tools/build_data.py`, `wayswitch/data/LICENSE`, `tests/test_ngram.py`
- Generate: `wayswitch/data/ru.words.gz`, `wayswitch/data/en.words.gz`, `wayswitch/data/ru.ngrams.json.gz`, `wayswitch/data/en.ngrams.json.gz`

**Interfaces:**
- Produces:
  - `ngram.train(words: Iterable[tuple[str, float]], alphabet: str) -> dict` (модель: `{"alphabet", "tri", "ctx", "floor"}`)
  - `ngram.score(model: dict, text: str) -> float` — средняя логвероятность на символ
  - `ngram.save(model, path)`, `ngram.load(path) -> dict` (gzip+json)
  - формат `*.words.gz`: текст UTF-8, строка = `слово<TAB>частота`, отсортировано по убыванию частоты; ранг = номер строки (с 1)

- [ ] **Step 1: Тест `tests/test_ngram.py`**

```python
from wayswitch import ngram

RU = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
EN = "abcdefghijklmnopqrstuvwxyz"


def _ru_model():
    words = [("привет", 100.0), ("пока", 50.0), ("спасибо", 80.0), ("буква", 20.0),
             ("книга", 30.0), ("это", 90.0), ("который", 40.0)]
    return ngram.train(words, RU)


def test_seen_word_scores_higher_than_garbage():
    m = _ru_model()
    assert ngram.score(m, "привет") > ngram.score(m, "ьоыфжз")


def test_unseen_trigram_uses_context_floor():
    m = _ru_model()
    assert m["floor"] < 0
    assert ngram.score(m, "яяя") <= m["floor"] + 1e-9 or ngram.score(m, "яяя") < -2


def test_score_is_case_insensitive():
    m = _ru_model()
    assert ngram.score(m, "Привет") == ngram.score(m, "привет")


def test_save_load_round_trip(tmp_path):
    m = _ru_model()
    p = tmp_path / "m.json.gz"
    ngram.save(m, p)
    m2 = ngram.load(p)
    assert m2 == m
```

- [ ] **Step 2: Запустить — падает.**

- [ ] **Step 3: Реализация `wayswitch/ngram.py`**

```python
"""Символьная триграммная модель с аддитивным сглаживанием.

Оценка слова — средняя логвероятность следующего символа при двух
предыдущих, с паддингом начала/конца. Модель — обычный dict, сериализуется
в gzip+json.
"""

from __future__ import annotations

import gzip
import json
import math
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

PAD_START = "^"
PAD_END = "$"
K = 0.1  # добавка сглаживания


def train(words: Iterable[tuple[str, float]], alphabet: str) -> dict:
    tri: dict[str, float] = defaultdict(float)
    ctx: dict[str, float] = defaultdict(float)
    for word, weight in words:
        s = PAD_START * 2 + word.lower() + PAD_END
        for i in range(2, len(s)):
            tri[s[i - 2:i] + s[i]] += weight
            ctx[s[i - 2:i]] += weight
    vocab = len(alphabet) + 1  # плюс символ конца слова
    model = {
        "alphabet": alphabet,
        "tri": {abc: math.log((n + K) / (ctx[abc[:2]] + K * vocab)) for abc, n in tri.items()},
        "ctx": {ab: math.log(K / (n + K * vocab)) for ab, n in ctx.items()},
        "floor": math.log(1.0 / vocab),
    }
    return model


def score(model: dict, text: str) -> float:
    """Средняя логвероятность на символ; чем выше, тем правдоподобнее слово."""
    tri, ctx, floor = model["tri"], model["ctx"], model["floor"]
    s = PAD_START * 2 + text.lower() + PAD_END
    total, n = 0.0, 0
    for i in range(2, len(s)):
        abc = s[i - 2:i] + s[i]
        lp = tri.get(abc)
        if lp is None:
            lp = ctx.get(abc[:2], floor)
        total += lp
        n += 1
    return total / n if n else floor


def save(model: dict, path: Path) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def load(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)
```

- [ ] **Step 4: Тесты проходят** (`python -m pytest tests/test_ngram.py -q`).

- [ ] **Step 5: `tools/build_data.py`**

```python
#!/usr/bin/env python3
"""Сборка словарей и триграмм из открытых частотных списков.

Источник: FrequencyWords (hermitdave), списки по 50 000 слов на основе
субтитров OpenSubtitles 2018, лицензия CC-BY-SA 4.0 (см. wayswitch/data/LICENSE).
Результат детерминирован: одинаковый вход → одинаковые файлы.
"""

from __future__ import annotations

import gzip
import math
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wayswitch import ngram  # noqa: E402

BASE = "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018"
SOURCES = {
    "ru": (f"{BASE}/ru/ru_50k.txt", "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"),
    "en": (f"{BASE}/en/en_50k.txt", "abcdefghijklmnopqrstuvwxyz"),
}
DATA = Path(__file__).resolve().parents[1] / "wayswitch" / "data"


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read().decode("utf-8")


def parse(text: str, alphabet: str) -> list[tuple[str, int]]:
    letters = set(alphabet)
    rows = []
    seen = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        word, count = parts[0].lower(), int(parts[1])
        # Только слова целиком из букв алфавита; дубликаты по регистру схлопываем.
        if not word or set(word) - letters or word in seen:
            continue
        seen.add(word)
        rows.append((word, count))
    rows.sort(key=lambda r: (-r[1], r[0]))
    return rows


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    for lang, (url, alphabet) in SOURCES.items():
        print(f"{lang}: загрузка {url}")
        rows = parse(fetch(url), alphabet)
        print(f"{lang}: {len(rows)} слов")
        with gzip.open(DATA / f"{lang}.words.gz", "wt", encoding="utf-8") as f:
            for word, count in rows:
                f.write(f"{word}\t{count}\n")
        # Тот же вес, что и в LanguageModel.from_words: log(1 + частота).
        model = ngram.train(((w, math.log1p(c)) for w, c in rows), alphabet)
        ngram.save(model, DATA / f"{lang}.ngrams.json.gz")
        print(f"{lang}: триграмм {len(model['tri'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`wayswitch/data/LICENSE`:
```
Файлы ru.words.gz, en.words.gz и производные от них ru.ngrams.json.gz,
en.ngrams.json.gz собраны из проекта FrequencyWords
(https://github.com/hermitdave/FrequencyWords), списки 2018 года на основе
корпуса субтитров OpenSubtitles. Лицензия данных: CC-BY-SA 4.0
(https://creativecommons.org/licenses/by-sa/4.0/). Код проекта WaySwitch
распространяется по MIT и на эти файлы не распространяется.
```

- [ ] **Step 6: Собрать данные**

Run: `python tools/build_data.py`
Expected: четыре файла в `wayswitch/data/`, суммарно < 3 МБ. Проверить: `python -c "import gzip; print(sum(1 for _ in gzip.open('wayswitch/data/ru.words.gz','rt',encoding='utf-8')))"` → около 45–50 тысяч. Удалить `wayswitch/data/.gitkeep`.

- [ ] **Step 7: Коммит** `feat: триграммная модель и сборка словарей ru/en` (данные коммитятся).

---
### Task 4: Детектор

**Files:**
- Create: `wayswitch/detector.py`, `tests/test_detector.py`, `tests/test_detector_quality.py`
- Modify: `tests/fakes.py` (добавить `keys_for_text`, `K`)

**Interfaces:**
- Consumes: `Keymap` (Task 2), `ngram` (Task 3), `config.SENSITIVITY_NAMES`.
- Produces:
  - `@dataclass(frozen=True) KeyPress(code: int, shift: bool = False, caps: bool = False)` — **определяется здесь**, в `wayswitch/detector.py`? Нет: определяется в `wayswitch/buffer.py` (Task 5). Чтобы не тянуть зависимость, детектор принимает любые объекты с полями `code, shift, caps`; в тестах — `tests.fakes.K`.
  - `@dataclass Sensitivity(min_length: int, margin: float, floor: float)`; `SENSITIVITY: dict[str, Sensitivity]`
  - `class LanguageModel`: поля `lang: str`, `words: frozenset[str]`, `top: frozenset[str]`, `model: dict`; `LanguageModel.load(lang: str, data_dir: Path | None = None, top_n: int = 300) -> LanguageModel`; `LanguageModel.from_words(lang, rows: list[tuple[str, float]], alphabet, top_n=300)`; `is_word(w) -> bool`
  - `@dataclass Hypothesis(text, core, tail, lang, valid, in_dict, score)`
  - `@dataclass Decision(action: str, target_text: str, confidence: float, reason: str, a: Hypothesis, b: Hypothesis)` — `action` ∈ `{"keep", "fix"}`
  - `class Detector(keymap, models: dict[str, LanguageModel], exceptions: set[str] | None = None, sensitivity: Sensitivity = SENSITIVITY["normal"])`: `decide(keys, current_group, other_group, context=(None, None)) -> Decision`, `early_url_target(keys, current_group, other_group) -> str | None`, `lang_of_group(group) -> str | None`, `word_language(text) -> str | None`, `decode(keys, group) -> str`, атрибут `exceptions: set[str]` (общий с контроллером, изменяемый)
  - `tests/fakes.py`: `K` (dataclass `code, shift=False, caps=False`), `keys_for_text(keymap, text, group) -> list[K]`

- [ ] **Step 1: Дополнить `tests/fakes.py`**

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class K:
    code: int
    shift: bool = False
    caps: bool = False


def keys_for_text(keymap, text: str, group: int) -> list[K]:
    """Нажатия, которыми текст набирается в данной группе; KeyError, если символа нет."""
    keys = []
    for ch in text:
        r = keymap.key_for(ch, group)
        if r is None:
            raise KeyError(ch)
        keys.append(K(r[0], r[1]))
    return keys
```

- [ ] **Step 2: Регрессионный тест `tests/test_detector.py`**

```python
import pytest

from tests.fakes import RU, US, K, keys_for_text, make_keymap
from wayswitch import keycodes as kc
from wayswitch.detector import SENSITIVITY, Detector, LanguageModel

RU_ALPHABET = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
EN_ALPHABET = "abcdefghijklmnopqrstuvwxyz"

RU_WORDS = [("я", 1000.0), ("не", 900.0), ("это", 800.0), ("привет", 500.0), ("мы", 700.0),
            ("буква", 100.0), ("книга", 120.0), ("эту", 300.0), ("пока", 200.0),
            ("спасибо", 250.0), ("который", 150.0), ("сон", 60.0), ("дом", 90.0),
            ("дорогой", 80.0), ("игорь", 40.0)]
EN_WORDS = [("the", 1000.0), ("hello", 500.0), ("world", 400.0), ("book", 300.0),
            ("letter", 200.0), ("this", 350.0), ("that", 340.0), ("a", 900.0), ("i", 950.0),
            ("with", 330.0), ("son", 50.0)]


@pytest.fixture
def det():
    km = make_keymap()
    models = {
        "ru": LanguageModel.from_words("ru", RU_WORDS, RU_ALPHABET, top_n=5),
        "en": LanguageModel.from_words("en", EN_WORDS, EN_ALPHABET, top_n=5),
    }
    return Detector(km, models, exceptions=set(), sensitivity=SENSITIVITY["normal"])


def typed(text, group):
    return keys_for_text(make_keymap(), text, group)


# --- исправления -------------------------------------------------------------

@pytest.mark.parametrize("typed_text,expected", [
    ("ghbdtn", "привет"),   # словарное слово
    (",erdf", "буква"),     # ведущая запятая = б
    ("'nj", "это"),         # апостроф = э
    ("'ne", "эту"),
])
def test_russian_word_typed_in_latin_layout_is_fixed(det, typed_text, expected):
    d = det.decide(typed(typed_text, US), US, RU)
    assert d.action == "fix", d.reason
    assert d.target_text == expected


def test_case_and_tail_punctuation_follow_target_layout(det):
    keys = [K(kc.KEY_G, shift=True), K(kc.KEY_H), K(kc.KEY_B), K(kc.KEY_D), K(kc.KEY_T),
            K(kc.KEY_N), K(kc.KEY_SLASH, shift=True)]
    d = det.decide(keys, US, RU)
    assert d.action == "fix"
    assert d.target_text == "Привет,"


def test_english_word_typed_in_russian_layout_is_fixed(det):
    d = det.decide(typed("руддщ", RU), RU, US)
    assert d.action == "fix" and d.target_text == "hello"


def test_unknown_but_plausible_word_fixed_by_ngrams(det):
    # «книгой» нет в словаре, но триграммы русские; латинская гипотеза — мусор.
    d = det.decide(typed("rybujq", US), US, RU)
    assert d.action == "fix" and d.target_text == "книгой"


def test_url_typed_in_russian_layout_is_fixed(det):
    d = det.decide(typed("реезыЖ..ышеюсщь", RU), RU, US)
    assert d.action == "fix" and d.target_text == "https://site.com"


def test_short_word_needs_russian_context(det):
    keys = typed("z", US)
    assert det.decide(keys, US, RU, context=(None, None)).action == "fix"
    assert det.decide(keys, US, RU, context=("ru", "ru")).action == "fix"
    assert det.decide(keys, US, RU, context=("en", "en")).action == "keep"


def test_early_url_trigger(det):
    assert det.early_url_target(typed("реезыЖ..", RU), RU, US) == "https://"
    assert det.early_url_target(typed("реезыЖ.", RU), RU, US) is None
    assert det.early_url_target(typed("https://", US), US, RU) is None


# --- сохранение --------------------------------------------------------------

@pytest.mark.parametrize("text,group", [
    ("hello", US), ("the", US), ("github.com", US), ("https", US), ("abc123", US),
    ("user@host", US), ("привет", RU), ("Привет", RU), ("эту", RU), ("сон", RU),
    ("camelCase", US), ("NASA", US), ("ab", US),
])
def test_real_words_and_tech_tokens_are_kept(det, text, group):
    other = RU if group == US else US
    d = det.decide(typed(text, group), group, other)
    assert d.action == "keep", (text, d.reason, d.target_text)


def test_exception_list_blocks_fix(det):
    det.exceptions.add("ghbdtn")
    assert det.decide(typed("ghbdtn", US), US, RU).action == "keep"


def test_context_doubles_margin_for_ngram_path(det):
    # То же слово, но после двух английских словарных слов порог удваивается.
    keys = typed("rybujq", US)
    assert det.decide(keys, US, RU).action == "fix"
    d = det.decide(keys, US, RU, context=("en", "en"))
    assert d.reason.startswith("ngram") or d.action == "keep"


def test_word_language(det):
    assert det.word_language("привет") == "ru"
    assert det.word_language("Hello") == "en"
    assert det.word_language("ghbdtn") is None
```

- [ ] **Step 3: Тест качества `tests/test_detector_quality.py`**

```python
"""Качество детектора на отложенной выборке из настоящих словарей.

10 % слов (по хешу) исключаются из словаря и из обучения n-грамм и играют
роль «неизвестных слов». Пороги из спецификации: полнота ≥ 95 % на словах
длиной ≥ 4, набранных не в той раскладке; ложных срабатываний ≤ 0.5 % на
словах, набранных правильно.
"""

import gzip
import hashlib
from pathlib import Path

import pytest

from tests.fakes import RU, US, keys_for_text, make_keymap
from wayswitch.detector import SENSITIVITY, Detector, LanguageModel

DATA = Path(__file__).resolve().parents[1] / "wayswitch" / "data"
ALPHABETS = {"ru": "абвгдеёжзийклмнопрстуфхцчшщъыьэюя", "en": "abcdefghijklmnopqrstuvwxyz"}
GROUP = {"ru": RU, "en": US}

pytestmark = pytest.mark.skipif(not (DATA / "ru.words.gz").exists(), reason="нет данных")


def _held_out(word: str) -> bool:
    return hashlib.md5(word.encode()).digest()[0] % 10 == 0


def _rows(lang):
    with gzip.open(DATA / f"{lang}.words.gz", "rt", encoding="utf-8") as f:
        return [(w, int(c)) for w, c in (line.rstrip("\n").split("\t") for line in f)]


@pytest.fixture(scope="module")
def setup():
    km = make_keymap()
    models, held = {}, {}
    for lang in ("ru", "en"):
        rows = _rows(lang)
        train = [(w, float(c)) for w, c in rows if not _held_out(w)]
        held[lang] = [w for w, _ in rows if _held_out(w) and len(w) >= 4][:3000]
        models[lang] = LanguageModel.from_words(lang, train, ALPHABETS[lang])
    return km, Detector(km, models, sensitivity=SENSITIVITY["normal"]), held


def _rates(km, det, held, lang):
    other = "en" if lang == "ru" else "ru"
    g, og = GROUP[lang], GROUP[other]
    hits = misses = false_pos = total_fp = 0
    for word in held[lang]:
        try:
            keys = keys_for_text(km, word, g)
        except KeyError:
            continue
        # Слово набрано в чужой раскладке: на экране мусор, должно исправиться в word.
        d = det.decide(keys, og, g)
        if d.action == "fix" and d.target_text == word:
            hits += 1
        else:
            misses += 1
        # Слово набрано правильно: трогать нельзя.
        total_fp += 1
        if det.decide(keys, g, og).action == "fix":
            false_pos += 1
    return hits / (hits + misses), false_pos / total_fp


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_recall_and_false_positive_rate(setup, lang):
    km, det, held = setup
    recall, fpr = _rates(km, det, held, lang)
    print(f"\n{lang}: recall={recall:.3f} fpr={fpr:.4f}")
    assert recall >= 0.95, f"{lang}: полнота {recall:.3f}"
    assert fpr <= 0.005, f"{lang}: ложные {fpr:.4f}"
```

- [ ] **Step 4: Запустить — падает (нет модуля).**

- [ ] **Step 5: Реализация `wayswitch/detector.py`**

```python
"""Решение «слово набрано не в той раскладке?» по двум гипотезам.

A — то, что сейчас на экране (декодировка в текущей группе), B — то же
нажатия в другой группе. Порядок проверок повторяет раздел 5.4 спецификации.
"""

from __future__ import annotations

import gzip
import math
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from wayswitch import ngram
from wayswitch.keymap import CYRILLIC, LATIN, Keymap

LANG_OF_ALPHABET = {"ru": "ru", "latin": "en"}
LETTERS = {"ru": CYRILLIC, "en": LATIN}
INNER_MARKS = {"-", "'"}
URL_SCHEMES = ("http://", "https://", "ftp://")
TECH_PREFIXES = URL_SCHEMES + ("ssh://", "www.", "git@")
TECH_MARKERS = ("://", "/", "\\", "@", "~", "--", ".com", ".ru", ".org", ".net", ".io", ".dev")
TECH_WORDS = {"http", "https", "ftp", "www", "ssh", "git", "sudo", "apt", "dnf"}


@dataclass(frozen=True)
class Sensitivity:
    min_length: int
    margin: float
    floor: float


SENSITIVITY = {
    "conservative": Sensitivity(4, 2.5, -5.5),
    "normal": Sensitivity(3, 1.5, -6.0),
    "aggressive": Sensitivity(3, 0.8, -7.0),
}


class LanguageModel:
    def __init__(self, lang: str, words: Iterable[str], top: Iterable[str], model: dict):
        self.lang = lang
        self.words = frozenset(words)
        self.top = frozenset(top)
        self.model = model

    @classmethod
    def from_words(cls, lang: str, rows: list[tuple[str, float]], alphabet: str,
                   top_n: int = 300) -> LanguageModel:
        ordered = sorted(rows, key=lambda r: -r[1])
        # Вес слова — log(1 + частота): частые важнее, но не подавляют остальные.
        weighted = [(w, math.log1p(c)) for w, c in ordered]
        return cls(lang, (w for w, _ in ordered), (w for w, _ in ordered[:top_n]),
                   ngram.train(weighted, alphabet))

    @classmethod
    def load(cls, lang: str, data_dir: Path | None = None, top_n: int = 300) -> LanguageModel:
        base = data_dir or Path(str(resources.files("wayswitch") / "data"))
        words = []
        with gzip.open(base / f"{lang}.words.gz", "rt", encoding="utf-8") as f:
            for line in f:
                words.append(line.split("\t", 1)[0])
        return cls(lang, words, words[:top_n], ngram.load(base / f"{lang}.ngrams.json.gz"))

    def is_word(self, word: str) -> bool:
        return word.lower() in self.words

    def score(self, text: str) -> float:
        return ngram.score(self.model, text)


@dataclass
class Hypothesis:
    text: str
    core: str
    tail: str
    lang: str | None
    valid: bool
    in_dict: bool
    score: float | None = None


@dataclass
class Decision:
    action: str
    target_text: str
    confidence: float
    reason: str
    a: Hypothesis
    b: Hypothesis


def _split_tail(text: str, letters: set[str]) -> tuple[str, str]:
    """Отделить хвостовую пунктуацию: символы в конце, не являющиеся буквами языка."""
    i = len(text)
    while i > 0 and text[i - 1].lower() not in letters and text[i - 1] not in INNER_MARKS:
        i -= 1
    return text[:i], text[i:]


def _is_camel(core: str) -> bool:
    return any(c.isupper() for c in core[1:]) and any(c.islower() for c in core)


def _mixed_alphabets(core: str) -> bool:
    low = set(core.lower())
    return bool(low & CYRILLIC) and bool(low & LATIN)


class Detector:
    def __init__(self, keymap: Keymap, models: dict[str, LanguageModel],
                 exceptions: set[str] | None = None,
                 sensitivity: Sensitivity = SENSITIVITY["normal"]):
        self.keymap = keymap
        self.models = models
        self.exceptions = exceptions if exceptions is not None else set()
        self.sensitivity = sensitivity

    # --- вспомогательное ---------------------------------------------------

    def decode(self, keys, group: int) -> str:
        return self.keymap.decode(keys, group)

    def lang_of_group(self, group: int) -> str | None:
        return LANG_OF_ALPHABET.get(self.keymap.alphabet(group))

    def word_language(self, text: str) -> str | None:
        """Язык словарного слова или None, если слово никому не известно."""
        core = text.strip().lower()
        for lang, model in self.models.items():
            if core and model.is_word(core):
                return lang
        return None

    def hypothesis(self, keys, group: int) -> Hypothesis:
        text = self.decode(keys, group)
        lang = self.lang_of_group(group)
        if lang is None or lang not in self.models:
            return Hypothesis(text, text, "", None, False, False)
        letters = LETTERS[lang]
        core, tail = _split_tail(text, letters)
        low = core.lower()
        valid = bool(low) and all(c in letters or c in INNER_MARKS for c in low) \
            and low[0] not in INNER_MARKS and low[-1] not in INNER_MARKS
        in_dict = valid and self.models[lang].is_word(low)
        return Hypothesis(text, core, tail, lang, valid, in_dict)

    # --- решения -----------------------------------------------------------

    def early_url_target(self, keys, current_group: int, other_group: int) -> str | None:
        """Слово в другой раскладке — ровно схема URL: исправлять, не дожидаясь пробела."""
        if self.lang_of_group(other_group) != "en":
            return None
        b = self.decode(keys, other_group)
        return b if b.lower() in URL_SCHEMES else None

    def decide(self, keys, current_group: int, other_group: int,
               context: tuple[str | None, str | None] = (None, None)) -> Decision:
        a = self.hypothesis(keys, current_group)
        b = self.hypothesis(keys, other_group)
        sens = self.sensitivity

        def keep(reason: str, conf: float = 0.0) -> Decision:
            return Decision("keep", a.text, conf, reason, a, b)

        def fix(reason: str, conf: float) -> Decision:
            return Decision("fix", b.core + b.tail, conf, reason, a, b)

        if a.lang is None or b.lang is None:
            return keep("unknown-alphabet")

        # 2. Технический токен в B: URL, набранный не в той раскладке.
        bl = b.text.lower()
        if (bl.startswith(TECH_PREFIXES) or "://" in bl) and not a.in_dict \
                and b.lang == "en":
            return Decision("fix", b.text, 1.0, "url", a, b)

        # 3. Стоп-правила по тому, что на экране.
        if any(c.isdigit() for c in a.text):
            return keep("digits")
        if _mixed_alphabets(a.core):
            return keep("mixed")
        if _is_camel(a.core):
            return keep("camel")
        al = a.text.lower()
        if any(m in al for m in TECH_MARKERS) or al.startswith(TECH_PREFIXES)                 or a.core.lower() in TECH_WORDS:
            return keep("tech")
        if 2 <= len(a.core) < 5 and a.core.isupper():
            return keep("abbrev")
        if a.core.lower() in self.exceptions:
            return keep("exception")

        # 4. Валидность.
        if not a.valid and not b.valid:
            return keep("invalid")

        # 5. Словарь.
        if a.valid and a.in_dict:
            return keep("dict-a", 1.0)
        short = len(b.core) < sens.min_length
        if b.valid and b.in_dict and not short:
            return fix("dict-b", 1.0)

        # 8. Короткие слова — только частотные и только в контексте своего языка.
        if short:
            if b.valid and b.core.lower() in self.models[b.lang].top and not a.in_dict \
                    and context[-1] in (None, b.lang):
                return fix("dict-short", 0.8)
            return keep("short")

        # 6–7. N-граммы.
        if not b.valid:
            return keep("b-invalid")
        b.score = self.models[b.lang].score(b.core)
        a.score = self.models[a.lang].score(a.core) if a.valid else None
        margin = sens.margin * (2.0 if context == (a.lang, a.lang) else 1.0)
        if b.score < sens.floor:
            return keep(f"ngram-floor {b.score:.2f}")
        if a.score is None:
            return fix(f"ngram-only {b.score:.2f}", 0.7)
        delta = b.score - a.score
        if delta >= margin:
            return fix(f"ngram Δ={delta:.2f}", min(1.0, delta / (2 * margin)))
        return keep(f"ngram Δ={delta:.2f} < {margin:.2f}")
```

- [ ] **Step 6: Прогнать оба теста**

Run: `python -m pytest tests/test_detector.py tests/test_detector_quality.py -q -s`
Expected: регрессионный набор — все проходят. Тест качества печатает `recall`/`fpr` для ru и en и проходит по порогам. **Если пороги не достигаются:** подбирать `SENSITIVITY["normal"]` (`margin`, `floor`) и при необходимости `K` в `ngram.py`; не ослаблять пороги теста. Зафиксировать достигнутые числа в сообщении коммита.

- [ ] **Step 7: Коммит** `feat: детектор неверной раскладки (словарь + триграммы), тесты качества`.

---

### Task 5: Буфер набора и жест Shift

**Files:**
- Create: `wayswitch/buffer.py`, `wayswitch/gesture.py`, `tests/test_buffer.py`, `tests/test_gesture.py`

**Interfaces:**
- Consumes: `Keymap.is_letter_key`, `keycodes`.
- Produces:
  - `@dataclass(frozen=True) KeyPress(code: int, shift: bool = False, caps: bool = False)`
  - `@dataclass BufferEvent(kind: str, keys: list[KeyPress])` — `kind` ∈ `{"word", "letter", "reset"}`
  - `class InputBuffer(keymap, phrase_timeout: float = 8.0, max_keys: int = 512)`:
    `feed(code, value, device_id, now) -> BufferEvent | None`, `note_release(code, device_id)`, `click()`, `reset() -> bool`, `resync(device_id, held_codes: Iterable[int])`, `device_gone(device_id)`, `set_caps(on: bool)`, `caps: bool`, `held_physical() -> set[tuple[int, int]]`, `held_non_modifier() -> bool`, `shift_held() -> bool`, `command_held() -> bool`, `current_word() -> list[KeyPress]`, `last_word_with_tail() -> tuple[list[KeyPress], int]`, `phrase() -> list[KeyPress]`, `replace_last_word(keys: list[KeyPress])`, `replace_all(keys: list[KeyPress])`, `is_empty() -> bool`
  - `class ShiftGesture(window: float = 0.4)`: `on_key(code, value, now) -> str | None` (`"word"`, `"phrase"`)

- [ ] **Step 1: Тесты `tests/test_buffer.py`**

```python
from tests.fakes import make_keymap
from wayswitch import keycodes as kc
from wayswitch.buffer import InputBuffer, KeyPress


def make():
    return InputBuffer(make_keymap(), phrase_timeout=8.0, max_keys=512)


def tap(buf, code, t=0.0, dev=1, shift=False):
    ev = None
    if shift:
        buf.feed(kc.KEY_LEFTSHIFT, 1, dev, t)
    ev = buf.feed(code, 1, dev, t) or ev
    buf.feed(code, 0, dev, t)
    if shift:
        buf.feed(kc.KEY_LEFTSHIFT, 0, dev, t)
    return ev


def test_letters_then_space_emit_word():
    b = make()
    assert tap(b, kc.KEY_G).kind == "letter"
    tap(b, kc.KEY_H)
    ev = tap(b, kc.KEY_B)
    assert ev.kind == "letter" and [k.code for k in ev.keys] == [kc.KEY_G, kc.KEY_H, kc.KEY_B]
    ev = tap(b, kc.KEY_SPACE)
    assert ev.kind == "word" and len(ev.keys) == 3
    assert b.current_word() == []
    word, tail = b.last_word_with_tail()
    assert len(word) == 3 and tail == 1


def test_shift_and_caps_recorded():
    b = make()
    tap(b, kc.KEY_G, shift=True)
    b.set_caps(True)
    tap(b, kc.KEY_H)
    keys = b.current_word()
    assert keys[0] == KeyPress(kc.KEY_G, True, False)
    assert keys[1] == KeyPress(kc.KEY_H, False, True)


def test_backspace_pops_and_crosses_space():
    b = make()
    tap(b, kc.KEY_G); tap(b, kc.KEY_SPACE)
    tap(b, kc.KEY_BACKSPACE)
    assert len(b.current_word()) == 1  # пробел стёрт, слово снова текущее
    tap(b, kc.KEY_BACKSPACE)
    assert b.is_empty()
    assert tap(b, kc.KEY_BACKSPACE) is None and b.is_empty()  # стираем чужое → пусто


def test_reset_keys_and_modifiers():
    b = make()
    for code in (kc.KEY_ENTER, kc.KEY_TAB, kc.KEY_LEFT, kc.KEY_HOME, kc.KEY_F5, kc.KEY_DELETE):
        tap(b, kc.KEY_G)
        assert tap(b, code).kind == "reset", code
        assert b.is_empty()
    tap(b, kc.KEY_G)
    b.feed(kc.KEY_LEFTCTRL, 1, 1, 0.0)
    assert b.feed(kc.KEY_C, 1, 1, 0.0).kind == "reset"
    b.feed(kc.KEY_C, 0, 1, 0.0); b.feed(kc.KEY_LEFTCTRL, 0, 1, 0.0)
    tap(b, kc.KEY_G)
    assert b.feed(kc.KEY_LEFTMETA, 1, 1, 0.0).kind == "reset"


def test_click_timeout_resync_gone():
    b = make()
    tap(b, kc.KEY_G); b.click(); assert b.is_empty()
    tap(b, kc.KEY_G, t=0.0)
    tap(b, kc.KEY_H, t=9.0)  # пауза больше phrase_timeout — старое забыто
    assert len(b.current_word()) == 1
    b.feed(kc.KEY_LEFTSHIFT, 1, 2, 9.0)
    assert b.shift_held()
    b.resync(2, [])
    assert not b.shift_held() and b.is_empty()
    b.feed(kc.KEY_LEFTSHIFT, 1, 2, 9.0)
    b.device_gone(2)
    assert not b.shift_held()


def test_autorepeat_resets():
    b = make()
    tap(b, kc.KEY_G)
    assert b.feed(kc.KEY_G, 2, 1, 0.0).kind == "reset"


def test_held_tracking_across_devices():
    b = make()
    b.feed(kc.KEY_LEFTSHIFT, 1, 1, 0.0)
    b.feed(kc.KEY_LEFTSHIFT, 1, 2, 0.0)
    b.feed(kc.KEY_LEFTSHIFT, 0, 1, 0.0)
    assert b.shift_held()
    b.note_release(kc.KEY_LEFTSHIFT, 2)
    assert not b.shift_held()
    b.feed(kc.KEY_G, 1, 1, 0.0)
    assert b.held_non_modifier()
    b.feed(kc.KEY_G, 0, 1, 0.0)
    assert not b.held_non_modifier()


def test_replace_last_word_and_phrase():
    b = make()
    for code in (kc.KEY_G, kc.KEY_H, kc.KEY_SPACE, kc.KEY_B, kc.KEY_SPACE):
        tap(b, code)
    b.replace_last_word([KeyPress(kc.KEY_A), KeyPress(kc.KEY_S)])
    word, tail = b.last_word_with_tail()
    assert [k.code for k in word] == [kc.KEY_A, kc.KEY_S] and tail == 1
    assert [k.code for k in b.phrase()] == [kc.KEY_G, kc.KEY_H, kc.KEY_SPACE, kc.KEY_A,
                                            kc.KEY_S, kc.KEY_SPACE]
    b.replace_all([KeyPress(kc.KEY_Q)])
    assert [k.code for k in b.phrase()] == [kc.KEY_Q]


def test_max_keys_drops_oldest():
    b = InputBuffer(make_keymap(), max_keys=3)
    for code in (kc.KEY_G, kc.KEY_H, kc.KEY_B, kc.KEY_D):
        tap(b, code)
    assert [k.code for k in b.phrase()] == [kc.KEY_H, kc.KEY_B, kc.KEY_D]


def test_ru_punctuation_keys_are_word_keys():
    b = make()
    tap(b, kc.KEY_COMMA); tap(b, kc.KEY_SEMICOLON); tap(b, kc.KEY_APOSTROPHE)
    assert len(b.current_word()) == 3
```

`tests/test_gesture.py`:
```python
from wayswitch import keycodes as kc
from wayswitch.gesture import ShiftGesture


def press_release(g, code, t):
    r1 = g.on_key(code, 1, t)
    r2 = g.on_key(code, 0, t + 0.05)
    return r1 or r2


def test_double_shift_on_release_of_second():
    g = ShiftGesture(window=0.4)
    assert press_release(g, kc.KEY_LEFTSHIFT, 0.0) is None
    assert g.on_key(kc.KEY_RIGHTSHIFT, 1, 0.2) is None
    assert g.on_key(kc.KEY_RIGHTSHIFT, 0, 0.25) == "word"


def test_triple_shift_is_phrase():
    g = ShiftGesture()
    press_release(g, kc.KEY_LEFTSHIFT, 0.0)
    assert press_release(g, kc.KEY_LEFTSHIFT, 0.2) == "word"
    assert press_release(g, kc.KEY_LEFTSHIFT, 0.4) == "phrase"
    assert press_release(g, kc.KEY_LEFTSHIFT, 0.6) is None  # серия закончена


def test_other_key_breaks_series():
    g = ShiftGesture()
    press_release(g, kc.KEY_LEFTSHIFT, 0.0)
    g.on_key(kc.KEY_A, 1, 0.1); g.on_key(kc.KEY_A, 0, 0.15)
    assert press_release(g, kc.KEY_LEFTSHIFT, 0.2) is None


def test_slow_taps_do_not_count():
    g = ShiftGesture(window=0.4)
    press_release(g, kc.KEY_LEFTSHIFT, 0.0)
    assert press_release(g, kc.KEY_LEFTSHIFT, 1.0) is None


def test_shift_held_for_capital_letter_is_not_a_tap():
    g = ShiftGesture()
    g.on_key(kc.KEY_LEFTSHIFT, 1, 0.0)
    g.on_key(kc.KEY_H, 1, 0.1); g.on_key(kc.KEY_H, 0, 0.15)
    g.on_key(kc.KEY_LEFTSHIFT, 0, 0.2)
    assert press_release(g, kc.KEY_LEFTSHIFT, 0.3) is None
```

- [ ] **Step 2: Запустить — падает.**

- [ ] **Step 3: Реализация `wayswitch/buffer.py`**

```python
"""Буфер набранных нажатий.

Хранятся коды клавиш с состоянием Shift/CapsLock, а не символы: какой символ
получился, зависит от раскладки, а её мы узнаём отдельно. Пробелы хранятся
как разделители — по ним фраза делится на слова.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from wayswitch import keycodes as kc
from wayswitch.keymap import Keymap


@dataclass(frozen=True)
class KeyPress:
    code: int
    shift: bool = False
    caps: bool = False


@dataclass
class BufferEvent:
    kind: str  # "word" — слово завершено пробелом; "letter" — слово растёт; "reset"
    keys: list[KeyPress] = field(default_factory=list)


class InputBuffer:
    def __init__(self, keymap: Keymap, phrase_timeout: float = 8.0, max_keys: int = 512):
        self._keymap = keymap
        self.phrase_timeout = phrase_timeout
        self.max_keys = max_keys
        self._keys: list[KeyPress] = []
        self._held: set[tuple[int, int]] = set()  # (device_id, code)
        self.caps = False
        self._last_time: float | None = None

    # --- состояние клавиш ------------------------------------------------------

    def set_caps(self, on: bool) -> None:
        self.caps = on

    def held_physical(self) -> set[tuple[int, int]]:
        return set(self._held)

    def _held_codes(self) -> set[int]:
        return {code for _, code in self._held}

    def shift_held(self) -> bool:
        return bool(self._held_codes() & kc.SHIFT_KEYS)

    def command_held(self) -> bool:
        return bool(self._held_codes() & kc.COMMAND_MODIFIERS)

    def held_non_modifier(self) -> bool:
        return bool(self._held_codes() - kc.MODIFIER_KEYS)

    def note_release(self, code: int, device_id: int) -> None:
        """Отпускание, пришедшее пока демон печатает: буфер не трогаем, зажатые обновляем."""
        self._held.discard((device_id, code))

    # --- сброс ----------------------------------------------------------------

    def is_empty(self) -> bool:
        return not self._keys

    def reset(self) -> bool:
        had = bool(self._keys)
        self._keys.clear()
        return had

    def _reset_event(self) -> BufferEvent | None:
        return BufferEvent("reset") if self.reset() else None

    def click(self) -> None:
        self.reset()

    def resync(self, device_id: int, held_codes: Iterable[int]) -> None:
        """После SYN_DROPPED: заменить зажатые клавиши устройства снимком, буфер сбросить."""
        self._held = {(d, c) for d, c in self._held if d != device_id}
        self._held |= {(device_id, c) for c in held_codes}
        self.reset()

    def device_gone(self, device_id: int) -> None:
        self._held = {(d, c) for d, c in self._held if d != device_id}
        self.reset()

    # --- ввод -----------------------------------------------------------------

    def _is_word_key(self, code: int) -> bool:
        return self._keymap.is_letter_key(code) or code in kc.WORD_EXTRA_KEYS

    def _append(self, key: KeyPress) -> None:
        self._keys.append(key)
        if len(self._keys) > self.max_keys:
            del self._keys[: len(self._keys) - self.max_keys]

    def feed(self, code: int, value: int, device_id: int, now: float) -> BufferEvent | None:
        if self._last_time is not None and self._keys \
                and now - self._last_time > self.phrase_timeout:
            self._keys.clear()
        self._last_time = now

        if value == 0:
            self._held.discard((device_id, code))
            return None
        if value == 2:
            # Автоповтор: сколько символов выдаст компоситор — неизвестно.
            if self._is_word_key(code) or code == kc.KEY_SPACE:
                return self._reset_event()
            return None

        self._held.add((device_id, code))
        if code in kc.META_KEYS:
            return self._reset_event()  # Super открывает обзор — каретка ушла
        if code in kc.MODIFIER_KEYS or code == kc.KEY_CAPSLOCK:
            return None
        if self.command_held():
            return self._reset_event()  # Ctrl+X, Alt+Tab и т. п.
        if code in kc.RESET_KEYS:
            return self._reset_event()
        if code == kc.KEY_BACKSPACE:
            if self._keys:
                self._keys.pop()
                return None
            return self._reset_event()  # стёрли то, чего не видели
        if code == kc.KEY_SPACE:
            word = self.current_word()
            self._append(KeyPress(kc.KEY_SPACE))
            return BufferEvent("word", word) if word else None
        if self._is_word_key(code):
            self._append(KeyPress(code, self.shift_held(), self.caps))
            return BufferEvent("letter", self.current_word())
        return self._reset_event()  # цифровой блок, мультимедиа и прочее

    # --- чтение ---------------------------------------------------------------

    def current_word(self) -> list[KeyPress]:
        i = len(self._keys)
        while i > 0 and self._keys[i - 1].code != kc.KEY_SPACE:
            i -= 1
        return self._keys[i:]

    def last_word_with_tail(self) -> tuple[list[KeyPress], int]:
        """Последнее слово и число пробелов после него."""
        end = len(self._keys)
        while end > 0 and self._keys[end - 1].code == kc.KEY_SPACE:
            end -= 1
        start = end
        while start > 0 and self._keys[start - 1].code != kc.KEY_SPACE:
            start -= 1
        return self._keys[start:end], len(self._keys) - end

    def phrase(self) -> list[KeyPress]:
        return list(self._keys)

    def replace_last_word(self, keys: list[KeyPress]) -> None:
        end = len(self._keys)
        while end > 0 and self._keys[end - 1].code == kc.KEY_SPACE:
            end -= 1
        start = end
        while start > 0 and self._keys[start - 1].code != kc.KEY_SPACE:
            start -= 1
        self._keys[start:end] = list(keys)

    def replace_all(self, keys: list[KeyPress]) -> None:
        self._keys = list(keys)
```

`wayswitch/gesture.py`:
```python
"""Жест ручного исправления: двойной (слово) и тройной (фраза) Shift.

Срабатывает на отпускании, чтобы синтетические символы не ушли с зажатым
физическим Shift. Любая другая клавиша между нажатиями разрывает серию.
"""

from __future__ import annotations

from wayswitch import keycodes as kc


class ShiftGesture:
    def __init__(self, window: float = 0.4):
        self.window = window
        self._count = 0
        self._last_press: float | None = None
        self._broken = False  # между нажатием и отпусканием была другая клавиша

    def on_key(self, code: int, value: int, now: float) -> str | None:
        if code in kc.SHIFT_KEYS:
            if value == 1:
                if self._last_press is not None and now - self._last_press <= self.window:
                    self._count += 1
                else:
                    self._count = 1
                self._last_press = now
                self._broken = False
            elif value == 0:
                if self._broken:
                    self._count = 0
                    self._last_press = None
                elif self._count == 2:
                    return "word"
                elif self._count >= 3:
                    self._count = 0
                    self._last_press = None
                    return "phrase"
            return None
        if value == 1:
            self._count = 0
            self._last_press = None
            self._broken = True
        return None
```

- [ ] **Step 4: Тесты проходят** (`python -m pytest tests/test_buffer.py tests/test_gesture.py -q`).

- [ ] **Step 5: Коммит** `feat: буфер набора и жест двойного/тройного Shift`.

---
### Task 6: Исполнитель (план действий и uinput)

**Files:**
- Create: `wayswitch/actuator.py`, `tests/test_actuator.py`
- Modify: `tests/fakes.py` (добавить `RecordingTypist`, `RecordingTextTypist`, `FakeBackend`)

**Interfaces:**
- Consumes: `Keymap.key_for`.
- Produces:
  - шаги: `ReleaseModifiers()`, `Backspace(count: int)`, `SwitchLayout(group: int)`, `Type(text: str, keys: list[tuple[int, bool]])`; `@dataclass Plan(steps: list)`
  - `plan_fix(delete_count: int, target_text: str, target_group: int, keymap, caps_on: bool, switch: bool = True) -> Plan | None` (`target_group` — группа, в которой набирается текст; `switch=False` — без шага `SwitchLayout`, раскладка уже нужная)
  - `keys_for_typing(text, group, keymap, caps_on) -> list[tuple[int, bool]] | None`
  - протоколы `KeyTypist` (`key(code, value)`, `syn()`), `TextTypist` (`backspace(n)`, `type_text(text)`)
  - `DONE, ABORTED, FAILED = "done", "aborted", "failed"`; `execute(plan, typist, backend, abort=lambda: False, key_delay: float = 0.0, sleep=time.sleep, switch_timeout: float = 0.5) -> str`
  - `class UInputTypist` (`NAME = "WaySwitch Virtual Keyboard"`, `key`, `syn`, `close`)
  - бэкенд для `execute` — любой объект с `set(group)` и `wait_applied(group, timeout) -> bool`
  - `tests/fakes.py`: `RecordingTypist` (`events: list[tuple[int,int] | str]`, `stuck() -> set[int]`, `taps() -> list[tuple[int,bool]]` — нажатые клавиши с признаком Shift), `RecordingTextTypist` (`calls: list`), `FakeBackend(layouts, current=0)` с `set_calls`, `fail_switch=False`

- [ ] **Step 1: Дополнить `tests/fakes.py`**

```python
from wayswitch import keycodes as kc
from wayswitch.keymap import LayoutSpec


class RecordingTypist:
    """Запоминает события uinput и умеет сказать, что осталось нажатым.

    fail_at — номер записи, которая один раз завершится OSError (временный сбой);
    следующие записи проходят, поэтому исполнитель обязан суметь всё отпустить.
    """

    def __init__(self, fail_at: int | None = None):
        self.events: list = []
        self.fail_at = fail_at
        self._writes = 0

    def key(self, code: int, value: int) -> None:
        self._writes += 1
        if self.fail_at is not None and self._writes == self.fail_at:
            raise OSError("uinput write failed")
        self.events.append((code, value))

    def syn(self) -> None:
        self.events.append("syn")

    def stuck(self) -> set[int]:
        held = set()
        for ev in self.events:
            if ev == "syn":
                continue
            code, value = ev
            (held.add if value else held.discard)(code)
        return held

    def taps(self) -> list[tuple[int, bool]]:
        """Нажатия обычных клавиш с признаком «Shift был зажат»."""
        out, shift = [], False
        for ev in self.events:
            if ev == "syn":
                continue
            code, value = ev
            if code in kc.SHIFT_KEYS:
                shift = bool(value)
            elif value == 1:
                out.append((code, shift))
        return out


class RecordingTextTypist:
    def __init__(self):
        self.calls: list = []

    def backspace(self, n: int) -> None:
        self.calls.append(("backspace", n))

    def type_text(self, text: str) -> None:
        self.calls.append(("type", text))


class FakeBackend:
    name = "fake"
    supports_auto = True

    def __init__(self, layouts=None, current=0, fail_switch=False):
        self._layouts = layouts or [LayoutSpec("us"), LayoutSpec("ru")]
        self.current_index = current
        self.set_calls: list[int] = []
        self.fail_switch = fail_switch
        self._callbacks = []

    def layouts(self):
        return list(self._layouts)

    def xkb_options(self):
        return []

    def current(self):
        return self.current_index

    def set(self, index: int) -> None:
        self.set_calls.append(index)
        if not self.fail_switch:
            self.current_index = index

    def wait_applied(self, index: int, timeout: float) -> bool:
        return self.current_index == index

    def on_change(self, cb):
        self._callbacks.append(cb)

    def emit_external_change(self, index: int):
        self.current_index = index
        for cb in self._callbacks:
            cb(index, True)

    def close(self):
        pass
```

- [ ] **Step 2: Тест `tests/test_actuator.py`**

```python
import pytest

from tests.fakes import RU, US, FakeBackend, RecordingTextTypist, RecordingTypist, make_keymap
from wayswitch import keycodes as kc
from wayswitch.actuator import (
    ABORTED, DONE, FAILED, Backspace, ReleaseModifiers, SwitchLayout, Type, execute, plan_fix,
)


def test_plan_fix_builds_steps_with_shift_and_caps():
    km = make_keymap()
    plan = plan_fix(7, "Привет, ", RU, km, caps_on=False)
    assert isinstance(plan.steps[0], ReleaseModifiers)
    assert plan.steps[1] == Backspace(7)
    assert plan.steps[2] == SwitchLayout(RU)
    t = plan.steps[3]
    assert isinstance(t, Type) and t.text == "Привет, "
    assert t.keys[0] == (kc.KEY_G, True)          # П — с Shift
    assert t.keys[1] == (kc.KEY_H, False)         # р
    assert t.keys[6] == (kc.KEY_SLASH, True)      # запятая в ru — Shift+/
    assert t.keys[7] == (kc.KEY_SPACE, False)
    caps = plan_fix(0, "Пр", RU, km, caps_on=True)
    assert caps.steps[-1].keys == [(kc.KEY_G, False), (kc.KEY_H, True)]


def test_plan_fix_without_switch_and_impossible_char():
    km = make_keymap()
    plan = plan_fix(3, "abc", US, km, caps_on=False, switch=False)
    assert not any(isinstance(s, SwitchLayout) for s in plan.steps)
    assert plan.steps[-1].keys[0] == (kc.KEY_A, False)
    assert plan_fix(1, "ж", US, km, caps_on=False) is None


def test_execute_full_sequence():
    km = make_keymap()
    typist, backend = RecordingTypist(), FakeBackend(current=US)
    plan = plan_fix(2, "пр ", RU, km, caps_on=False)
    assert execute(plan, typist, backend) == DONE
    assert backend.set_calls == [RU]
    assert typist.taps() == [(kc.KEY_BACKSPACE, False), (kc.KEY_BACKSPACE, False),
                             (kc.KEY_G, False), (kc.KEY_H, False), (kc.KEY_SPACE, False)]
    assert typist.stuck() == set()
    # первым делом отпущены все модификаторы
    assert typist.events[0] == (kc.KEY_LEFTSHIFT, 0)


def test_execute_aborts_and_releases_everything():
    km = make_keymap()
    plan = plan_fix(3, "Пр", RU, km, caps_on=False)
    calls = {"n": 0}

    def abort():
        calls["n"] += 1
        return calls["n"] >= 3

    typist = RecordingTypist()
    assert execute(plan, typist, FakeBackend(current=US), abort=abort) == ABORTED
    assert typist.stuck() == set()
    assert len(typist.taps()) < 5


def test_execute_fails_on_write_error_and_releases():
    km = make_keymap()
    plan = plan_fix(2, "Пр", RU, km, caps_on=False)
    for fail_at in range(1, 20):
        typist = RecordingTypist(fail_at=fail_at)
        result = execute(plan, typist, FakeBackend(current=US))
        assert result == FAILED
        assert typist.stuck() == set(), fail_at


def test_execute_fails_if_layout_not_applied():
    km = make_keymap()
    plan = plan_fix(1, "п", RU, km, caps_on=False)
    typist = RecordingTypist()
    assert execute(plan, typist, FakeBackend(current=US, fail_switch=True)) == FAILED
    assert typist.stuck() == set()
    assert (kc.KEY_G, 1) not in typist.events  # перепечатка не началась


def test_execute_with_text_typist():
    km = make_keymap()
    plan = plan_fix(4, "тест ", RU, km, caps_on=False)
    typist, backend = RecordingTextTypist(), FakeBackend(current=US)
    assert execute(plan, typist, backend) == DONE
    assert typist.calls == [("backspace", 4), ("type", "тест ")]
    assert backend.set_calls == [RU]


def test_key_delay_calls_sleep():
    km = make_keymap()
    plan = plan_fix(1, "п", RU, km, caps_on=False)
    slept = []
    execute(plan, RecordingTypist(), FakeBackend(current=US), key_delay=0.002,
            sleep=slept.append)
    assert slept and all(s == pytest.approx(0.002) for s in slept)
```

- [ ] **Step 3: Запустить — падает.**

- [ ] **Step 4: Реализация `wayswitch/actuator.py`**

```python
"""План исправления и его исполнение.

План строится без железа (тестируется), исполняется либо через uinput
(KeyTypist), либо через расширение GNOME Shell (TextTypist). Инвариант:
на любом выходе из execute все клавиши, нажатые виртуальным устройством,
отпущены.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from wayswitch import keycodes as kc
from wayswitch.keymap import Keymap

DONE, ABORTED, FAILED = "done", "aborted", "failed"
ALL_MODIFIERS = (kc.KEY_LEFTSHIFT, kc.KEY_RIGHTSHIFT, kc.KEY_LEFTCTRL, kc.KEY_RIGHTCTRL,
                 kc.KEY_LEFTALT, kc.KEY_RIGHTALT, kc.KEY_LEFTMETA, kc.KEY_RIGHTMETA)


@dataclass(frozen=True)
class ReleaseModifiers:
    pass


@dataclass(frozen=True)
class Backspace:
    count: int


@dataclass(frozen=True)
class SwitchLayout:
    group: int


@dataclass(frozen=True)
class Type:
    text: str
    keys: list[tuple[int, bool]]


@dataclass
class Plan:
    steps: list = field(default_factory=list)


class KeyTypist(Protocol):
    def key(self, code: int, value: int) -> None: ...
    def syn(self) -> None: ...


@runtime_checkable
class TextTypist(Protocol):
    def backspace(self, count: int) -> None: ...
    def type_text(self, text: str) -> None: ...


def keys_for_typing(text: str, group: int, keymap: Keymap,
                    caps_on: bool) -> list[tuple[int, bool]] | None:
    """Символы → (код, Shift) в группе; при CapsLock Shift у букв инвертируется."""
    keys = []
    for ch in text:
        r = keymap.key_for(ch, group)
        if r is None:
            return None
        code, shift = r
        if caps_on and ch.isalpha():
            shift = not shift
        keys.append((code, shift))
    return keys


def plan_fix(delete_count: int, target_text: str, target_group: int, keymap: Keymap,
             caps_on: bool, switch: bool = True) -> Plan | None:
    """План: отпустить модификаторы, стереть, (переключить), набрать текст в target_group."""
    keys = keys_for_typing(target_text, target_group, keymap, caps_on)
    if keys is None:
        return None
    steps: list = [ReleaseModifiers()]
    if delete_count > 0:
        steps.append(Backspace(delete_count))
    if switch:
        steps.append(SwitchLayout(target_group))
    if target_text:
        steps.append(Type(target_text, keys))
    return Plan(steps)


class _Session:
    """Учёт нажатых виртуальных клавиш для гарантированного отпускания."""

    def __init__(self, typist: KeyTypist, key_delay: float, sleep):
        self.typist = typist
        self.key_delay = key_delay
        self.sleep = sleep
        self.pressed: list[int] = []

    def press(self, code: int) -> None:
        self.pressed.append(code)
        self.typist.key(code, 1)
        self.typist.syn()
        if self.key_delay > 0:
            self.sleep(self.key_delay)

    def release(self, code: int) -> None:
        self.typist.key(code, 0)
        self.typist.syn()
        if code in self.pressed:
            self.pressed.remove(code)
        if self.key_delay > 0:
            self.sleep(self.key_delay)

    def release_all(self) -> None:
        for code in reversed(list(self.pressed)):
            try:
                self.typist.key(code, 0)
                self.typist.syn()
            except OSError:
                pass
        self.pressed.clear()


def execute(plan: Plan, typist, backend, abort: Callable[[], bool] = lambda: False,
            key_delay: float = 0.0, sleep=time.sleep, switch_timeout: float = 0.5) -> str:
    if isinstance(typist, TextTypist):
        return _execute_text(plan, typist, backend, abort, switch_timeout)
    session = _Session(typist, key_delay, sleep)
    try:
        for step in plan.steps:
            if abort():
                return ABORTED
            if isinstance(step, ReleaseModifiers):
                for code in ALL_MODIFIERS:
                    typist.key(code, 0)
                typist.syn()
            elif isinstance(step, Backspace):
                for _ in range(step.count):
                    if abort():
                        return ABORTED
                    session.press(kc.KEY_BACKSPACE)
                    session.release(kc.KEY_BACKSPACE)
            elif isinstance(step, SwitchLayout):
                backend.set(step.group)
                if not backend.wait_applied(step.group, switch_timeout):
                    return FAILED
            elif isinstance(step, Type):
                for code, shift in step.keys:
                    if abort():
                        return ABORTED
                    if shift:
                        session.press(kc.KEY_LEFTSHIFT)
                    session.press(code)
                    session.release(code)
                    if shift:
                        session.release(kc.KEY_LEFTSHIFT)
        return DONE
    except OSError:
        return FAILED
    finally:
        session.release_all()


def _execute_text(plan: Plan, typist: TextTypist, backend, abort, switch_timeout: float) -> str:
    try:
        for step in plan.steps:
            if abort():
                return ABORTED
            if isinstance(step, Backspace):
                typist.backspace(step.count)
            elif isinstance(step, SwitchLayout):
                backend.set(step.group)
                if not backend.wait_applied(step.group, switch_timeout):
                    return FAILED
            elif isinstance(step, Type):
                typist.type_text(step.text)
        return DONE
    except OSError:
        return FAILED


class UInputTypist:
    """Виртуальная клавиатура через /dev/uinput."""

    NAME = "WaySwitch Virtual Keyboard"

    def __init__(self):
        from evdev import UInput, ecodes  # импорт здесь: модуль есть только на Linux

        self._ecodes = ecodes
        self._ui = UInput({ecodes.EV_KEY: list(range(1, 128))}, name=self.NAME)

    def key(self, code: int, value: int) -> None:
        self._ui.write(self._ecodes.EV_KEY, code, value)

    def syn(self) -> None:
        self._ui.syn()

    def close(self) -> None:
        self._ui.close()
```

- [ ] **Step 5: Тесты проходят.**

- [ ] **Step 6: Коммит** `feat: план исправления и исполнение через uinput/расширение`.

---

### Task 7: Контроллер

**Files:**
- Create: `wayswitch/controller.py`, `tests/test_controller.py`

**Interfaces:**
- Consumes: `InputBuffer`, `ShiftGesture`, `Detector`, `plan_fix`/`execute`, `Config`, бэкенд (`current()`, `set()`, `layouts()`, `supports_auto`), typist.
- Produces:
  - `class Controller(config: Config, keymap, detector: Detector, backend, typist, *, clock=time.monotonic, pump=lambda: None, dry_run=False, on_corrected=None, on_status=None, persist_exception=None, sleep=time.sleep)`
  - входы: `on_key(code, value, device_id)`, `on_click()`, `on_layout_changed(index, external)`, `on_resync(device_id, held)`, `on_device_gone(device_id)`, `set_caps(on)`, `set_locked(on)`, `pause()`, `resume()`, `reload_config(config)`
  - действия: `manual_word() -> bool`, `manual_phrase() -> bool`
  - состояние: `busy: bool`, `paused: bool`, `locked: bool`, `status() -> dict` (ключи `active, paused, locked, busy, backend, layout_index, auto_correct, corrections_total, manual_total, undo_total, dry_run`)
  - `on_corrected(original: str, fixed: str, manual: bool)`; `on_status(dict)`; `persist_exception(word: str)`

- [ ] **Step 1: Тест `tests/test_controller.py`**

```python
import pytest

from tests.fakes import RU, US, FakeBackend, RecordingTypist, keys_for_text, make_keymap
from wayswitch import keycodes as kc
from wayswitch.config import Config
from wayswitch.controller import Controller
from wayswitch.detector import Detector, LanguageModel

RU_WORDS = [("я", 1000.0), ("не", 900.0), ("это", 800.0), ("привет", 500.0), ("мир", 400.0),
            ("буква", 100.0), ("книга", 120.0), ("пока", 200.0), ("спасибо", 250.0)]
EN_WORDS = [("the", 1000.0), ("hello", 500.0), ("world", 400.0), ("book", 300.0),
            ("this", 350.0), ("a", 900.0), ("i", 950.0)]


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def env():
    km = make_keymap()
    det = Detector(km, {
        "ru": LanguageModel.from_words("ru", RU_WORDS, "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"),
        "en": LanguageModel.from_words("en", EN_WORDS, "abcdefghijklmnopqrstuvwxyz"),
    })
    backend, typist, clock = FakeBackend(current=US), RecordingTypist(), Clock()
    corrected, exceptions = [], []
    ctrl = Controller(Config(), km, det, backend, typist, clock=clock,
                      on_corrected=lambda o, f, m: corrected.append((o, f, m)),
                      persist_exception=exceptions.append)
    return dict(km=km, ctrl=ctrl, backend=backend, typist=typist, clock=clock,
                corrected=corrected, exceptions=exceptions)


def type_text(env, text, group=None, dev=1):
    """Печатает текст физическими нажатиями в текущей раскладке бэкенда."""
    group = env["backend"].current() if group is None else group
    for ch in text:
        code, shift = env["km"].key_for(ch, group)
        if shift:
            env["ctrl"].on_key(kc.KEY_LEFTSHIFT, 1, dev)
        env["ctrl"].on_key(code, 1, dev)
        env["ctrl"].on_key(code, 0, dev)
        if shift:
            env["ctrl"].on_key(kc.KEY_LEFTSHIFT, 0, dev)
        env["clock"].now += 0.05


def double_shift(env, times=2):
    for _ in range(times):
        env["ctrl"].on_key(kc.KEY_RIGHTSHIFT, 1, 1)
        env["ctrl"].on_key(kc.KEY_RIGHTSHIFT, 0, 1)
        env["clock"].now += 0.1


def typed_text(env):
    """Что реально напечатал виртуальный демон (после Backspace-ов), в целевой раскладке."""
    taps = env["typist"].taps()
    out = []
    for code, shift in taps:
        if code == kc.KEY_BACKSPACE:
            out.append("\b")
        else:
            out.append(env["km"].char(code, env["backend"].current(), shift))
    return "".join(out)


def test_auto_fix_on_space(env):
    type_text(env, "ghbdtn ")
    assert env["backend"].set_calls == [RU]
    assert typed_text(env) == "\b" * 7 + "привет "
    assert env["corrected"] == [("ghbdtn", "привет", False)]
    word, tail = env["ctrl"].buffer.last_word_with_tail()
    assert env["km"].decode(word, RU) == "привет" and tail == 1
    assert env["ctrl"].status()["corrections_total"] == 1


def test_dictionary_word_kept(env):
    type_text(env, "hello ")
    assert env["typist"].events == [] and env["backend"].set_calls == []


def test_manual_double_shift_without_space(env):
    type_text(env, "ghbdtn")
    double_shift(env)
    assert typed_text(env) == "\b" * 6 + "привет"
    assert env["corrected"][-1][2] is True


def test_manual_double_shift_with_empty_buffer_just_switches(env):
    double_shift(env)
    assert env["backend"].set_calls == [RU] and env["typist"].taps() == []


def test_triple_shift_fixes_phrase(env):
    type_text(env, "ghbdtn vbh")
    double_shift(env, times=3)
    # второй Shift исправил «мир», третий — всю фразу; в итоге напечатано «привет мир»
    assert typed_text(env).endswith("привет мир")
    assert env["km"].decode(env["ctrl"].buffer.phrase(), RU) == "привет мир"


def test_undo_by_gesture_adds_exception(env):
    type_text(env, "ghbdtn ")
    env["typist"].events.clear()
    double_shift(env)
    assert typed_text(env) == "\b" * 7 + "ghbdtn "
    assert env["exceptions"] == ["ghbdtn"]
    assert env["ctrl"].status()["undo_total"] == 1
    assert env["backend"].current() == US


def test_gesture_after_undo_window_is_plain_manual(env):
    type_text(env, "ghbdtn ")
    env["clock"].now += 10
    double_shift(env)
    assert env["exceptions"] == []


def test_early_url_trigger(env):
    env["backend"].current_index = RU
    type_text(env, "реезыЖ..")
    assert typed_text(env) == "\b" * 8 + "https://"
    assert env["backend"].current() == US


def test_abort_on_physical_key_during_fix(env):
    class Interrupting(RecordingTypist):
        def key(self, code, value):
            super().key(code, value)
            if len(self.events) == 6:
                env["ctrl"].on_key(kc.KEY_A, 1, 1)  # пользователь нажал клавишу
    env["typist"] = Interrupting()
    env["ctrl"].typist = env["typist"]
    type_text(env, "ghbdtn ")
    assert env["typist"].stuck() == set()
    assert env["ctrl"].buffer.is_empty()
    assert env["corrected"] == []


def test_click_and_external_layout_change_reset(env):
    type_text(env, "ghbdtn")
    env["ctrl"].on_click()
    assert env["ctrl"].buffer.is_empty()
    type_text(env, "ghbdtn")
    env["backend"].emit_external_change(RU)
    env["ctrl"].on_layout_changed(RU, True)
    assert env["ctrl"].buffer.is_empty()


def test_pause_and_lock_disable_auto(env):
    env["ctrl"].pause()
    type_text(env, "ghbdtn ")
    assert env["typist"].events == []
    env["ctrl"].resume()
    env["ctrl"].set_locked(True)
    type_text(env, "ghbdtn ")
    assert env["typist"].events == []


def test_dry_run_does_not_touch_anything(env):
    env["ctrl"].dry_run = True
    type_text(env, "ghbdtn ")
    assert env["typist"].events == [] and env["backend"].set_calls == []


def test_held_non_modifier_key_cancels_fix(env):
    env["ctrl"].on_key(kc.KEY_Q, 1, 2)  # вторая клавиатура держит клавишу
    env["clock"].now += 1
    type_text(env, "ghbdtn ")
    assert env["typist"].events == []


def test_context_languages_tracked(env):
    type_text(env, "hello world ")
    assert list(env["ctrl"].context) == ["en", "en"]
    type_text(env, "ghbdtn ")
    assert list(env["ctrl"].context) == ["en", "ru"]


def test_timeout_clears_context(env):
    type_text(env, "hello world ")
    assert list(env["ctrl"].context) == ["en", "en"]
    env["clock"].now += 20  # пауза дольше phrase_timeout
    type_text(env, "ghbdtn ")
    assert list(env["ctrl"].context) == ["ru"]


def test_layout_change_by_daemon_does_not_reset(env):
    type_text(env, "ghbdtn ")
    env["ctrl"].on_layout_changed(RU, False)
    assert not env["ctrl"].buffer.is_empty()
```

- [ ] **Step 2: Запустить — падает.**

- [ ] **Step 3: Реализация `wayswitch/controller.py`**

```python
"""Оркестрация без привязки к GLib: события клавиш → решения → действия.

Контроллер ничего не знает о evdev, D-Bus и главном цикле. Всё внешнее
приходит через вызовы on_*, всё наружу — через typist/backend и колбэки.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from wayswitch import keycodes as kc
from wayswitch.actuator import DONE, execute, keys_for_typing, plan_fix
from wayswitch.buffer import InputBuffer, KeyPress
from wayswitch.config import Config
from wayswitch.detector import SENSITIVITY, Detector
from wayswitch.gesture import ShiftGesture

log = logging.getLogger("wayswitch")
RELEASE_WAIT = 0.3  # сколько ждать отпускания физических клавиш перед печатью


@dataclass
class LastFix:
    original_keys: list[KeyPress]
    target_keys: list[KeyPress]
    group_before: int
    time: float


class Controller:
    def __init__(self, config: Config, keymap, detector: Detector, backend, typist, *,
                 clock: Callable[[], float] = time.monotonic,
                 pump: Callable[[], None] = lambda: None,
                 dry_run: bool = False,
                 on_corrected: Callable[[str, str, bool], None] | None = None,
                 on_status: Callable[[dict], None] | None = None,
                 persist_exception: Callable[[str], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.config = config
        self.keymap = keymap
        self.detector = detector
        self.backend = backend
        self.typist = typist
        self.clock = clock
        self.pump = pump  # прокрутить очередь событий главного цикла (для отмены)
        self.dry_run = dry_run
        self.on_corrected = on_corrected
        self.on_status = on_status
        self.persist_exception = persist_exception
        self.sleep = sleep

        self.buffer = InputBuffer(keymap, config.general.phrase_timeout_sec)
        self.gesture = ShiftGesture()
        self.context: deque[str | None] = deque(maxlen=2)  # языки двух последних слов
        self.paused = False
        self.locked = False
        self.busy = False
        self._abort_requested = False
        self._last_fix: LastFix | None = None
        self.stats = {"corrections": 0, "manual": 0, "undo": 0}
        self._pause_key = kc.KEY_NAMES.get(config.gesture.pause_hotkey.lower()) \
            if config.gesture.pause_hotkey else None

    # --- конфигурация / статус ---------------------------------------------------

    def reload_config(self, config: Config) -> None:
        self.config = config
        self.buffer.phrase_timeout = config.general.phrase_timeout_sec
        self.detector.sensitivity = SENSITIVITY[config.general.sensitivity]
        self._pause_key = kc.KEY_NAMES.get(config.gesture.pause_hotkey.lower()) \
            if config.gesture.pause_hotkey else None
        self._emit_status()

    def status(self) -> dict:
        return {
            "active": self._auto_allowed(),
            "paused": self.paused,
            "locked": self.locked,
            "busy": self.busy,
            "backend": getattr(self.backend, "name", "?"),
            "layout_index": self.backend.current(),
            "auto_correct": self.config.general.auto_correct,
            "corrections_total": self.stats["corrections"],
            "manual_total": self.stats["manual"],
            "undo_total": self.stats["undo"],
            "dry_run": self.dry_run,
        }

    def _emit_status(self) -> None:
        if self.on_status:
            self.on_status(self.status())

    def pause(self) -> None:
        self.paused = True
        self.buffer.reset()
        self._emit_status()

    def resume(self) -> None:
        self.paused = False
        self._emit_status()

    def set_locked(self, on: bool) -> None:
        self.locked = on
        self.buffer.reset()
        self.context.clear()
        self._emit_status()

    def set_caps(self, on: bool) -> None:
        self.buffer.set_caps(on)

    # --- входящие события -------------------------------------------------------

    def on_key(self, code: int, value: int, device_id: int) -> None:
        if self.busy:
            # Физический ввод во время перепечатки — прервать; зажатые клавиши учесть.
            if value == 1:
                self._abort_requested = True
            elif value == 0:
                self.buffer.note_release(code, device_id)
            return
        now = self.clock()
        gesture = self.gesture.on_key(code, value, now) \
            if self.config.gesture.manual == "double_shift" else None
        event = self.buffer.feed(code, value, device_id, now)
        if value == 1 and self._pause_key is not None and code == self._pause_key:
            (self.resume if self.paused else self.pause)()
            return
        if value == 1 and self.config.gesture.manual == "pause_key" and code == kc.KEY_PAUSE:
            self.manual_word()
            return
        if gesture == "word":
            self.manual_word()
            return
        if gesture == "phrase":
            self.manual_phrase()
            return
        if event is None:
            return
        if event.kind == "reset":
            self.context.clear()
            return
        if event.reset_before:
            self.context.clear()  # буфер очищен по таймауту перед этой клавишей
        if not self._auto_allowed():
            return
        if event.kind == "word":
            self._on_word(event.keys)
        elif event.kind == "letter":
            self._on_letter(event.keys)

    def on_click(self) -> None:
        self.buffer.click()
        self.context.clear()

    def on_layout_changed(self, index: int, external: bool) -> None:
        if external:
            self.buffer.reset()
            self.context.clear()
        self._emit_status()

    def on_resync(self, device_id: int, held) -> None:
        self.buffer.resync(device_id, held)

    def on_device_gone(self, device_id: int) -> None:
        self.buffer.device_gone(device_id)

    # --- авторежим --------------------------------------------------------------

    def _auto_allowed(self) -> bool:
        return (self.config.general.auto_correct and not self.paused and not self.locked
                and getattr(self.backend, "supports_auto", False))

    def _groups(self) -> tuple[int, int] | None:
        current = self.backend.current()
        if current is None or len(self.backend.layouts()) != 2:
            return None
        return current, 1 - current

    def _on_word(self, keys: list[KeyPress]) -> None:
        groups = self._groups()
        if groups is None:
            return
        current, other = groups
        decision = self.detector.decide(keys, current, other, tuple(self.context))
        log.debug("слово %r → %s (%s)", decision.a.text, decision.action, decision.reason)
        if decision.action == "fix":
            if self._apply_fix(keys, 1, other, decision.target_text + " ", manual=False,
                               reason=decision.reason, group_before=current):
                self.context.append(decision.b.lang)
                return
        self.context.append(decision.a.lang if decision.a.in_dict else None)

    def _on_letter(self, keys: list[KeyPress]) -> None:
        groups = self._groups()
        if groups is None:
            return
        current, other = groups
        target = self.detector.early_url_target(keys, current, other)
        if target:
            self._apply_fix(keys, 0, other, target, manual=False, reason="url-early",
                            group_before=current)

    # --- ручной режим -----------------------------------------------------------

    def manual_word(self) -> bool:
        groups = self._groups()
        if groups is None:
            return False
        current, other = groups
        word, tail = self.buffer.last_word_with_tail()
        if not word:
            if not self.dry_run:
                self.backend.set(other)
                self.backend.wait_applied(other, 0.5)
            self._emit_status()
            return True
        last = self._last_fix
        undo = (last is not None and self.clock() - last.time <= self.config.general.undo_window_sec
                and word == last.target_keys)
        if undo:
            original = self.keymap.decode(last.original_keys, last.group_before)
            ok = self._apply_fix(word, tail, last.group_before, original + " " * tail,
                                 manual=True, reason="undo", group_before=current)
            if ok:
                self._last_fix = None
                self.stats["undo"] += 1
                letters = "".join(c for c in original if c.isalpha() or c in "-'").lower()
                if letters:
                    self.detector.exceptions.add(letters)
                    if self.persist_exception:
                        self.persist_exception(letters)
            return ok
        text = self.keymap.decode(word, other)
        return self._apply_fix(word, tail, other, text + " " * tail, manual=True,
                               reason="manual", group_before=current)

    def manual_phrase(self) -> bool:
        current = self.backend.current()
        keys = self.buffer.phrase()
        if current is None or not keys:
            return False
        # Раскладка уже переключена вторым Shift; декодируем всё в текущей группе.
        text = self.keymap.decode(keys, current)
        return self._apply_fix(keys, 0, current, text, manual=True, reason="phrase",
                               group_before=current, whole_phrase=True, switch=False)

    # --- исполнение -------------------------------------------------------------

    def _abort(self) -> bool:
        self.pump()
        return self._abort_requested

    def _wait_release(self) -> bool:
        deadline = self.clock() + RELEASE_WAIT
        while self.buffer.held_non_modifier():
            if self.clock() > deadline:
                return False
            self.pump()
            self.sleep(0.005)
        return True

    def _apply_fix(self, keys: list[KeyPress], tail: int, target_group: int, text: str,
                   *, manual: bool, reason: str, group_before: int,
                   whole_phrase: bool = False, switch: bool = True) -> bool:
        plan = plan_fix(len(keys) + tail, text, target_group, self.keymap, self.buffer.caps,
                        switch=switch)
        if plan is None:
            log.warning("не могу набрать %r в группе %s", text, target_group)
            return False
        original = self.keymap.decode(keys, group_before)
        if self.dry_run:
            log.info("[dry-run] %s: %r → %r", reason, original, text)
            return False
        self.busy = True
        self._abort_requested = False
        started = self.clock()
        try:
            if not self._wait_release():
                log.info("исправление отменено: зажата физическая клавиша")
                return False
            result = execute(plan, self.typist, self.backend, abort=self._abort,
                             key_delay=self.config.typing.key_delay_ms / 1000.0,
                             sleep=self.sleep)
        finally:
            self.busy = False
        if result != DONE:
            log.info("исправление %s: %s", reason, result)
            self.buffer.reset()
            self.context.clear()
            self._emit_status()
            return False
        log.info("%s: %r → %r за %.0f мс", reason, original, text.rstrip(),
                 (self.clock() - started) * 1000)
        new_keys = [KeyPress(code, shift) for code, shift in
                    keys_for_typing(text, target_group, self.keymap, False) or []]
        if whole_phrase:
            self.buffer.replace_all(new_keys)
        else:
            self.buffer.replace_last_word(new_keys[: len(new_keys) - tail] if tail else new_keys)
        word_keys = new_keys[: len(new_keys) - tail] if tail else new_keys
        if manual:
            self.stats["manual"] += 1
            if reason != "undo":
                self._last_fix = None
        else:
            self.stats["corrections"] += 1
            self._last_fix = LastFix(list(keys), word_keys, group_before, self.clock())
        if self.on_corrected:
            self.on_corrected(original, text.rstrip(), manual)
        self._emit_status()
        return True
```

- [ ] **Step 4: Тесты проходят** (`python -m pytest tests/test_controller.py -q`). Если `test_triple_shift_fixes_phrase` расходится в деталях подсчёта — проверить, что после второго Shift буфер содержит клавиши целевой группы (`replace_last_word`), и что третий Shift декодирует всю фразу в текущей группе бэкенда.

- [ ] **Step 5: Коммит** `feat: контроллер — оркестрация авто/ручного режима, откат, обучение`.

---
### Task 8: Бэкенды раскладки GNOME

**Files:**
- Create: `wayswitch/backends/base.py`, `wayswitch/backends/gnome_common.py`, `wayswitch/backends/gnome_ibus.py`, `wayswitch/backends/gnome_shell.py`, `wayswitch/backends/hotkey.py`, `wayswitch/backends/select.py`, `tests/test_backends_pure.py`

**Interfaces:**
- Consumes: `LayoutSpec`, `keycodes.KEY_NAMES`, typist (`key`, `syn`).
- Produces:
  - `class LayoutBackend(ABC)`: `name: str`, `supports_auto: bool`, `layouts() -> list[LayoutSpec]`, `xkb_options() -> list[str]`, `current() -> int | None`, `set(index: int) -> None`, `wait_applied(index, timeout) -> bool`, `on_change(cb: Callable[[int, bool], None])`, `close()`
  - `gnome_common.engine_name_for(spec: LayoutSpec) -> str` (чистая: `LayoutSpec("us","dvorak")` → `"xkb:us:dvorak:"`), `gnome_common.index_of_engine(engine: str, specs) -> int | None`, `gnome_common.parse_gnome_binding("<Super>space") -> list[int]` (коды: модификаторы + клавиша), `gnome_common.read_sources() -> list[LayoutSpec]` (raise `BackendError` при не-xkb источниках), `read_xkb_options()`, `read_switch_binding() -> list[int]`
  - `class BackendError(RuntimeError)`
  - `GnomeIbusBackend()` (name `"ibus"`, supports_auto `True`), `GnomeShellBackend()` (name `"shell"`, supports_auto `True`) + `ShellTypist(backend)` с `backspace(n)`, `type_text(text)`, `HotkeyBackend(typist, settle_ms)` (name `"hotkey"`, supports_auto `False`, `observe_physical(code, value)`)
  - `select.choose_backend(prefer: str, typist, settle_ms: int) -> LayoutBackend`

- [ ] **Step 1: Тест чистых функций `tests/test_backends_pure.py`**

```python
from wayswitch import keycodes as kc
from wayswitch.backends.gnome_common import engine_name_for, index_of_engine, parse_gnome_binding
from wayswitch.keymap import LayoutSpec


def test_engine_names():
    assert engine_name_for(LayoutSpec("us")) == "xkb:us::"
    assert engine_name_for(LayoutSpec("us", "dvorak")) == "xkb:us:dvorak:"
    specs = [LayoutSpec("us"), LayoutSpec("ru")]
    assert index_of_engine("xkb:ru::rus", specs) == 1
    assert index_of_engine("xkb:us::eng", specs) == 0
    assert index_of_engine("xkb:us:dvorak:eng", specs) is None
    assert index_of_engine("anthy", specs) is None


def test_parse_binding():
    assert parse_gnome_binding("<Super>space") == [kc.KEY_LEFTMETA, kc.KEY_SPACE]
    assert parse_gnome_binding("<Shift><Alt>Shift_L") == [kc.KEY_LEFTSHIFT, kc.KEY_LEFTALT,
                                                          kc.KEY_LEFTSHIFT]
    assert parse_gnome_binding("<Control>grave") == [kc.KEY_LEFTCTRL, kc.KEY_GRAVE]
    assert parse_gnome_binding("") == []
```

- [ ] **Step 2: Реализация**

`wayswitch/backends/base.py`:
```python
"""Интерфейс бэкенда раскладки: узнать, переключить, дождаться, подписаться."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from wayswitch.keymap import LayoutSpec


class BackendError(RuntimeError):
    pass


class LayoutBackend(ABC):
    name = "abstract"
    supports_auto = False  # можно ли доверять current() для автоисправления

    @abstractmethod
    def layouts(self) -> list[LayoutSpec]: ...

    @abstractmethod
    def xkb_options(self) -> list[str]: ...

    @abstractmethod
    def current(self) -> int | None: ...

    @abstractmethod
    def set(self, index: int) -> None: ...

    @abstractmethod
    def wait_applied(self, index: int, timeout: float) -> bool: ...

    @abstractmethod
    def on_change(self, cb: Callable[[int, bool], None]) -> None:
        """cb(index, external): external=True, если раскладку сменили не мы."""

    def close(self) -> None:
        pass
```

`wayswitch/backends/gnome_common.py`:
```python
"""Общее для GNOME: источники ввода из gsettings и разбор системного хоткея."""

from __future__ import annotations

import re

from wayswitch import keycodes as kc
from wayswitch.backends.base import BackendError
from wayswitch.keymap import LayoutSpec

SCHEMA_SOURCES = "org.gnome.desktop.input-sources"
SCHEMA_WM = "org.gnome.desktop.wm.keybindings"

_MOD_CODES = {"super": kc.KEY_LEFTMETA, "alt": kc.KEY_LEFTALT, "shift": kc.KEY_LEFTSHIFT,
              "control": kc.KEY_LEFTCTRL, "primary": kc.KEY_LEFTCTRL, "meta": kc.KEY_LEFTALT}
_KEYSYM_NAMES = {"shift_l": kc.KEY_LEFTSHIFT, "shift_r": kc.KEY_RIGHTSHIFT,
                 "alt_l": kc.KEY_LEFTALT, "alt_r": kc.KEY_RIGHTALT,
                 "control_l": kc.KEY_LEFTCTRL, "control_r": kc.KEY_RIGHTCTRL,
                 "super_l": kc.KEY_LEFTMETA, "super_r": kc.KEY_RIGHTMETA,
                 "caps_lock": kc.KEY_CAPSLOCK, "iso_next_group": kc.KEY_RIGHTALT}


def engine_name_for(spec: LayoutSpec) -> str:
    """Префикс имени IBus-движка для xkb-раскладки: xkb:<layout>:<variant>:."""
    return f"xkb:{spec.layout}:{spec.variant}:"


def index_of_engine(engine: str, specs: list[LayoutSpec]) -> int | None:
    for i, spec in enumerate(specs):
        if engine.startswith(engine_name_for(spec)):
            return i
    return None


def parse_gnome_binding(binding: str) -> list[int]:
    """'<Super>space' → [KEY_LEFTMETA, KEY_SPACE]. Неизвестное имя → BackendError."""
    if not binding:
        return []
    codes = [_MOD_CODES[m.lower()] for m in re.findall(r"<([^>]+)>", binding)
             if m.lower() in _MOD_CODES]
    key = re.sub(r"<[^>]+>", "", binding).strip().lower()
    if key:
        code = _KEYSYM_NAMES.get(key) or kc.KEY_NAMES.get(key)
        if code is None:
            raise BackendError(f"неизвестная клавиша в системном хоткее: {binding!r}")
        codes.append(code)
    return codes


def _settings(schema: str):
    from gi.repository import Gio

    return Gio.Settings.new(schema)


def read_sources() -> list[LayoutSpec]:
    raw = _settings(SCHEMA_SOURCES).get_value("sources").unpack()
    specs = []
    for kind, ident in raw:
        if kind != "xkb":
            raise BackendError(f"источник ввода {kind}:{ident} — не xkb-раскладка")
        specs.append(LayoutSpec.parse(ident))
    if not specs:
        raise BackendError("в gsettings нет источников ввода")
    return specs


def read_xkb_options() -> list[str]:
    return list(_settings(SCHEMA_SOURCES).get_strv("xkb-options"))


def read_switch_binding() -> list[int]:
    values = _settings(SCHEMA_WM).get_strv("switch-input-source")
    return parse_gnome_binding(values[0]) if values else []
```

`wayswitch/backends/gnome_ibus.py`:
```python
"""Раскладка через IBus: GNOME на Wayland ведёт xkb-раскладки как движки xkb:*."""

from __future__ import annotations

import time
from collections.abc import Callable

from wayswitch.backends import gnome_common
from wayswitch.backends.base import BackendError, LayoutBackend
from wayswitch.keymap import LayoutSpec


class GnomeIbusBackend(LayoutBackend):
    name = "ibus"
    supports_auto = True

    def __init__(self, settle_ms: int = 30):
        import gi

        gi.require_version("IBus", "1.0")
        from gi.repository import GLib, IBus

        self._GLib = GLib
        self._settle = settle_ms / 1000.0
        self._specs = gnome_common.read_sources()
        self._bus = IBus.Bus()
        if not self._bus.is_connected():
            raise BackendError("IBus не отвечает (ibus-daemon не запущен?)")
        self._bus.set_watch_ibus_signal(True)
        self._bus.connect("global-engine-changed", self._on_engine_changed)
        self._callbacks: list[Callable[[int, bool], None]] = []
        self._current: int | None = None
        self._expected: int | None = None  # индекс, который выставили мы
        self._refresh()

    def _refresh(self) -> None:
        desc = self._bus.get_global_engine()
        name = desc.get_name() if desc else ""
        self._current = gnome_common.index_of_engine(name, self._specs)

    def _on_engine_changed(self, _bus, name: str) -> None:
        index = gnome_common.index_of_engine(name, self._specs)
        external = index != self._expected
        self._expected = None
        self._current = index
        if index is not None:
            for cb in self._callbacks:
                cb(index, external)

    def layouts(self) -> list[LayoutSpec]:
        return list(self._specs)

    def xkb_options(self) -> list[str]:
        return gnome_common.read_xkb_options()

    def current(self) -> int | None:
        return self._current

    def set(self, index: int) -> None:
        self._expected = index
        if not self._bus.set_global_engine(gnome_common.engine_name_for(self._specs[index])
                                           + self._engine_suffix(index)):
            raise BackendError("IBus отказался переключить движок")

    def _engine_suffix(self, index: int) -> str:
        """IBus требует полное имя (xkb:ru::rus); ищем его в списке движков."""
        prefix = gnome_common.engine_name_for(self._specs[index])
        for desc in self._bus.list_engines():
            if desc.get_name().startswith(prefix):
                return desc.get_name()[len(prefix):]
        raise BackendError(f"в IBus нет движка для раскладки {self._specs[index].xkb_id}")

    def wait_applied(self, index: int, timeout: float) -> bool:
        ctx = self._GLib.MainContext.default()
        deadline = time.monotonic() + timeout
        while self._current != index:
            if time.monotonic() > deadline:
                return False
            ctx.iteration(False)
            time.sleep(0.001)
        # Сигнал пришёл от ibus-daemon; Shell применяет раскладку чуть позже.
        time.sleep(self._settle)
        return True

    def on_change(self, cb: Callable[[int, bool], None]) -> None:
        self._callbacks.append(cb)
```

`wayswitch/backends/gnome_shell.py`:
```python
"""Раскладка и печать через наше расширение GNOME Shell (D-Bus)."""

from __future__ import annotations

from collections.abc import Callable

from wayswitch.backends import gnome_common
from wayswitch.backends.base import BackendError, LayoutBackend
from wayswitch.keymap import LayoutSpec

BUS_NAME = "ru.siberia.WaySwitch.Shell"
OBJECT_PATH = "/ru/siberia/WaySwitch/Shell"
INTERFACE = "ru.siberia.WaySwitch.Shell"


def shell_extension_present() -> bool:
    from gi.repository import Gio

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    reply = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                          "NameHasOwner", Gio.Variant("(s)", (BUS_NAME,)),
                          Gio.VariantType("(b)"), Gio.DBusCallFlags.NONE, 1000, None)
    return bool(reply.unpack()[0])


class GnomeShellBackend(LayoutBackend):
    name = "shell"
    supports_auto = True

    def __init__(self):
        from gi.repository import Gio

        self._Gio = Gio
        self._specs = gnome_common.read_sources()
        self._proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None, BUS_NAME, OBJECT_PATH,
            INTERFACE, None)
        if self._proxy.get_name_owner() is None:
            raise BackendError("расширение WaySwitch не активно")
        self._callbacks: list[Callable[[int, bool], None]] = []
        self._expected: int | None = None
        self._proxy.connect("g-signal", self._on_signal)
        self._current = self._query()

    def _query(self) -> int | None:
        index, _ident = self._proxy.call_sync("GetLayout", None, 0, 1000, None).unpack()
        return index if 0 <= index < len(self._specs) else None

    def _on_signal(self, _proxy, _sender, signal: str, params) -> None:
        if signal != "LayoutChanged":
            return
        (index,) = params.unpack()
        external = index != self._expected
        self._expected = None
        self._current = index
        for cb in self._callbacks:
            cb(index, external)

    def layouts(self) -> list[LayoutSpec]:
        return list(self._specs)

    def xkb_options(self) -> list[str]:
        return gnome_common.read_xkb_options()

    def current(self) -> int | None:
        return self._current

    def set(self, index: int) -> None:
        self._expected = index
        self._proxy.call_sync("SetLayout", self._Gio.Variant("(u)", (index,)), 0, 1000, None)
        self._current = index

    def wait_applied(self, index: int, timeout: float) -> bool:
        return self._current == index  # SetLayout синхронен внутри Shell

    def on_change(self, cb: Callable[[int, bool], None]) -> None:
        self._callbacks.append(cb)

    def backspace(self, count: int) -> None:
        self._proxy.call_sync("Backspace", self._Gio.Variant("(u)", (count,)), 0, 2000, None)

    def type_text(self, text: str) -> None:
        self._proxy.call_sync("TypeText", self._Gio.Variant("(s)", (text,)), 0, 2000, None)


class ShellTypist:
    """Печать через расширение: keysym-ы не зависят от раскладки."""

    def __init__(self, backend: GnomeShellBackend):
        self._backend = backend

    def backspace(self, count: int) -> None:
        self._backend.backspace(count)

    def type_text(self, text: str) -> None:
        self._backend.type_text(text)
```

`wayswitch/backends/hotkey.py`:
```python
"""Резерв: эмуляция системного хоткея смены раскладки через uinput.

Состояние ведём счётчиком, поэтому оно ненадёжно — авторежим выключен,
ручной жест работает. Физические нажатия того же хоткея учитываем.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from wayswitch.backends import gnome_common
from wayswitch.backends.base import BackendError, LayoutBackend
from wayswitch.keymap import LayoutSpec


class HotkeyBackend(LayoutBackend):
    name = "hotkey"
    supports_auto = False

    def __init__(self, typist, settle_ms: int = 30, combo: list[int] | None = None,
                 specs: list[LayoutSpec] | None = None):
        self._typist = typist
        self._settle = max(settle_ms, 100) / 1000.0
        self._specs = specs or gnome_common.read_sources()
        self._combo = combo if combo is not None else gnome_common.read_switch_binding()
        if not self._combo:
            raise BackendError("системный хоткей смены раскладки не задан")
        self._current = 0  # при входе GNOME включает первый источник
        self._callbacks: list[Callable[[int, bool], None]] = []
        self._held: set[int] = set()

    def layouts(self) -> list[LayoutSpec]:
        return list(self._specs)

    def xkb_options(self) -> list[str]:
        return gnome_common.read_xkb_options()

    def current(self) -> int | None:
        return self._current

    def _advance(self, external: bool) -> None:
        self._current = (self._current + 1) % len(self._specs)
        for cb in self._callbacks:
            cb(self._current, external)

    def observe_physical(self, code: int, value: int) -> None:
        """Пользователь нажал системный хоткей сам — сдвинуть счётчик."""
        if value == 1:
            self._held.add(code)
            if code == self._combo[-1] and set(self._combo) <= self._held:
                self._advance(external=True)
        elif value == 0:
            self._held.discard(code)

    def set(self, index: int) -> None:
        while self._current != index:
            for code in self._combo:
                self._typist.key(code, 1)
            self._typist.syn()
            time.sleep(0.03)
            for code in self._combo:  # тот же порядок: модификатор отпускаем первым
                self._typist.key(code, 0)
            self._typist.syn()
            self._advance(external=False)
            time.sleep(self._settle)

    def wait_applied(self, index: int, timeout: float) -> bool:
        return self._current == index

    def on_change(self, cb: Callable[[int, bool], None]) -> None:
        self._callbacks.append(cb)
```

`wayswitch/backends/select.py`:
```python
"""Выбор бэкенда: расширение → IBus → хоткей."""

from __future__ import annotations

import logging

from wayswitch.backends.base import BackendError, LayoutBackend

log = logging.getLogger("wayswitch")


def choose_backend(prefer: str, typist, settle_ms: int) -> LayoutBackend:
    order = {"auto": ["shell", "ibus", "hotkey"], "shell": ["shell"], "ibus": ["ibus"],
             "hotkey": ["hotkey"]}[prefer]
    errors = []
    for name in order:
        try:
            if name == "shell":
                from wayswitch.backends.gnome_shell import GnomeShellBackend, shell_extension_present

                if not shell_extension_present():
                    raise BackendError("расширение не на шине")
                return GnomeShellBackend()
            if name == "ibus":
                from wayswitch.backends.gnome_ibus import GnomeIbusBackend

                return GnomeIbusBackend(settle_ms)
            if name == "hotkey":
                from wayswitch.backends.hotkey import HotkeyBackend

                return HotkeyBackend(typist, settle_ms)
        except Exception as e:  # noqa: BLE001 — любой сбой бэкенда: пробуем следующий
            errors.append(f"{name}: {e}")
            log.info("бэкенд %s недоступен: %s", name, e)
    raise BackendError("нет доступного бэкенда раскладки: " + "; ".join(errors))
```

- [ ] **Step 3: Тесты проходят** (`python -m pytest tests/test_backends_pure.py -q`), `python -m ruff check wayswitch/backends`.

- [ ] **Step 4: Коммит** `feat: бэкенды раскладки GNOME — IBus, расширение, резервный хоткей`.

---

### Task 9: Демон — устройства, сессия, D-Bus, сборка

**Files:**
- Create: `wayswitch/keys.py`, `wayswitch/session.py`, `wayswitch/dbus_service.py`, `wayswitch/daemon.py`, `tests/test_keys_pure.py`

**Interfaces:**
- Consumes: `Controller`, `choose_backend`, `XkbKeymap`, `LanguageModel.load`, `UInputTypist`, `ShellTypist`, `config`.
- Produces:
  - `keys.classify(name: str, key_caps: set[int]) -> str | None` — чистая: `"keyboard"`, `"pointer"`, `None`
  - `class DeviceWatcher(on_key, on_click, on_resync, on_gone, on_caps)`: `start()`, `stop()`, `devices() -> list[tuple[str, str, str]]` (путь, имя, тип)
  - `class SessionGuard(on_locked: Callable[[bool], None], on_sleep: Callable[[bool], None])`: `start()`, `locked: bool`
  - `dbus_service.DaemonService(controller, on_reload)`: `start() -> None` (raise `SystemExit(3)` если имя занято), `emit_corrected(original, fixed, manual)`, `emit_status(dict)`; константы `BUS_NAME`, `OBJECT_PATH`, `INTERFACE`, `INTROSPECTION_XML`
  - `daemon.run(config_path: Path | None, verbose: bool, dry_run: bool) -> int`

- [ ] **Step 1: Тест `tests/test_keys_pure.py`**

```python
from wayswitch import keycodes as kc
from wayswitch.keys import classify

KB = {kc.KEY_A, kc.KEY_Z, kc.KEY_SPACE, kc.KEY_ENTER, kc.KEY_LEFTSHIFT}


def test_classify():
    assert classify("AT Translated Set 2 keyboard", KB) == "keyboard"
    assert classify("Logitech USB Mouse", {kc.BTN_LEFT, kc.BTN_RIGHT}) == "pointer"
    assert classify("Video Bus", {kc.KEY_F1}) is None
    assert classify("WaySwitch Virtual Keyboard", KB) is None
    # Гибрид (клавиатура с кнопками) — клавиатура важнее.
    assert classify("Combo", KB | {kc.BTN_LEFT}) == "keyboard"
```

- [ ] **Step 2: Реализация**

`wayswitch/keys.py`:
```python
"""Чтение устройств ввода через evdev внутри GLib main loop.

Клавиатуры и мыши определяются по возможностям, не по имени. Hot-plug —
монитор каталога /dev/input. SYN_DROPPED → снимок зажатых клавиш.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

from wayswitch import keycodes as kc
from wayswitch.actuator import UInputTypist

log = logging.getLogger("wayswitch")
INPUT_DIR = "/dev/input"
KEYBOARD_KEYS = {kc.KEY_A, kc.KEY_Z, kc.KEY_SPACE, kc.KEY_ENTER}
OPEN_DELAY_MS = 300  # udev успевает выставить права


def classify(name: str, key_caps: set[int]) -> str | None:
    if name == UInputTypist.NAME:
        return None
    if KEYBOARD_KEYS <= key_caps:
        return "keyboard"
    if kc.BTN_LEFT in key_caps:
        return "pointer"
    return None


class DeviceWatcher:
    def __init__(self, on_key: Callable[[int, int, int], None], on_click: Callable[[], None],
                 on_resync: Callable[[int, list[int]], None], on_gone: Callable[[int], None],
                 on_caps: Callable[[bool], None]):
        self.on_key, self.on_click = on_key, on_click
        self.on_resync, self.on_gone, self.on_caps = on_resync, on_gone, on_caps
        self._devices: dict[str, tuple] = {}  # путь → (dev, device_id, kind, source_id)
        self._next_id = 1
        self._monitor = None
        self._rescans: list[int] = []

    # --- перечисление -------------------------------------------------------------

    def devices(self) -> list[tuple[str, str, str]]:
        return [(path, d[0].name, d[2]) for path, d in self._devices.items()]

    def start(self) -> None:
        from gi.repository import Gio, GLib

        self.rescan()
        self._monitor = Gio.File.new_for_path(INPUT_DIR).monitor_directory(
            Gio.FileMonitorFlags.NONE, None)
        self._monitor.connect("changed", self._on_dir_changed)
        self._GLib = GLib

    def stop(self) -> None:
        for path in list(self._devices):
            self._close(path)

    def rescan(self) -> None:
        from evdev import list_devices

        for path in list_devices():
            if path not in self._devices:
                self._open(path)

    def schedule_rescans(self, delays_sec=(5, 15, 30)) -> None:
        """После пробуждения Bluetooth-клавиатуры появляются с задержкой."""
        from gi.repository import GLib

        for d in delays_sec:
            GLib.timeout_add_seconds(d, lambda: (self.rescan(), False)[1])

    def _on_dir_changed(self, _mon, file, _other, event_type) -> None:
        from gi.repository import Gio, GLib

        name = file.get_basename() or ""
        if not name.startswith("event"):
            return
        path = os.path.join(INPUT_DIR, name)
        if event_type == Gio.FileMonitorEvent.CREATED:
            GLib.timeout_add(OPEN_DELAY_MS, lambda: (self._open(path), False)[1])
        elif event_type == Gio.FileMonitorEvent.DELETED:
            self._close(path)

    # --- устройства -----------------------------------------------------------

    def _open(self, path: str) -> None:
        from evdev import InputDevice, ecodes
        from gi.repository import GLib

        if path in self._devices:
            return
        try:
            dev = InputDevice(path)
            caps = set(dev.capabilities().get(ecodes.EV_KEY, []))
        except OSError as e:
            log.debug("не открыть %s: %s", path, e)
            return
        kind = classify(dev.name, caps)
        if kind is None:
            dev.close()
            return
        device_id = self._next_id
        self._next_id += 1
        source = GLib.io_add_watch(dev.fd, GLib.PRIORITY_DEFAULT,
                                   GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR,
                                   self._on_readable, path)
        self._devices[path] = (dev, device_id, kind, source)
        log.info("слушаю %s: %s (%s)", kind, dev.name, path)

    def _close(self, path: str) -> None:
        from gi.repository import GLib

        entry = self._devices.pop(path, None)
        if not entry:
            return
        dev, device_id, kind, source = entry
        GLib.source_remove(source)
        try:
            dev.close()
        except OSError:
            pass
        if kind == "keyboard":
            self.on_gone(device_id)
        log.info("устройство отключено: %s", path)

    def _on_readable(self, _fd, condition, path: str) -> bool:
        from evdev import ecodes
        from gi.repository import GLib

        entry = self._devices.get(path)
        if not entry:
            return False
        dev, device_id, kind, _ = entry
        if condition & (GLib.IOCondition.HUP | GLib.IOCondition.ERR):
            self._close(path)
            return False
        try:
            events = list(dev.read())
        except OSError:
            self._close(path)
            return False
        for ev in events:
            if ev.type == ecodes.EV_SYN and ev.code == ecodes.SYN_DROPPED and kind == "keyboard":
                try:
                    self.on_resync(device_id, list(dev.active_keys()))
                except OSError:
                    self._close(path)
                    return False
                continue
            if ev.type != ecodes.EV_KEY:
                continue
            if kc.is_pointer_button(ev.code):
                if ev.value == 1:
                    self.on_click()
                continue
            if kind != "keyboard":
                continue
            self.on_key(ev.code, ev.value, device_id)
            if ev.code == kc.KEY_CAPSLOCK and ev.value == 0:
                try:
                    self.on_caps(ecodes.LED_CAPSL in dev.leds())
                except OSError:
                    pass
        return True
```

`wayswitch/session.py`:
```python
"""Охрана сессии: блокировка экрана и сон.

Пока экран заблокирован, исправлять нельзя — иначе набранное до блокировки
могло бы уйти в поле пароля. Источники: org.gnome.ScreenSaver (мгновенно)
и logind (LockedHint/Active своей сессии, PrepareForSleep).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

log = logging.getLogger("wayswitch")


class SessionGuard:
    def __init__(self, on_locked: Callable[[bool], None], on_sleep: Callable[[bool], None]):
        self.on_locked = on_locked
        self.on_sleep = on_sleep
        self.locked = False
        self._screensaver_active = False
        self._logind_locked = False

    def start(self) -> None:
        from gi.repository import Gio

        try:
            self._ss = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None, "org.gnome.ScreenSaver",
                "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver", None)
            self._ss.connect("g-signal", self._on_ss_signal)
            self._screensaver_active = bool(self._ss.call_sync("GetActive", None, 0, 1000,
                                                                None).unpack()[0])
        except Exception as e:  # noqa: BLE001
            log.warning("org.gnome.ScreenSaver недоступен: %s", e)
        try:
            system = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            manager = Gio.DBusProxy.new_sync(system, Gio.DBusProxyFlags.NONE, None,
                                             "org.freedesktop.login1", "/org/freedesktop/login1",
                                             "org.freedesktop.login1.Manager", None)
            manager.connect("g-signal", self._on_manager_signal)
            (path,) = manager.call_sync("GetSessionByPID", Gio.Variant("(u)", (os.getpid(),)),
                                        0, 1000, None).unpack()
            self._session = Gio.DBusProxy.new_sync(system, Gio.DBusProxyFlags.NONE, None,
                                                   "org.freedesktop.login1", path,
                                                   "org.freedesktop.login1.Session", None)
            self._session.connect("g-properties-changed", self._on_session_props)
            self._read_session()
        except Exception as e:  # noqa: BLE001
            log.warning("logind недоступен, охрана только по ScreenSaver: %s", e)
        self._update()

    def _read_session(self) -> None:
        locked = self._session.get_cached_property("LockedHint")
        active = self._session.get_cached_property("Active")
        self._logind_locked = bool(locked and locked.unpack()) or bool(active is not None
                                                                       and not active.unpack())

    def _on_ss_signal(self, _p, _s, signal: str, params) -> None:
        if signal == "ActiveChanged":
            self._screensaver_active = bool(params.unpack()[0])
            self._update()

    def _on_session_props(self, _p, _changed, _invalidated) -> None:
        self._read_session()
        self._update()

    def _on_manager_signal(self, _p, _s, signal: str, params) -> None:
        if signal == "PrepareForSleep":
            self.on_sleep(bool(params.unpack()[0]))

    def _update(self) -> None:
        locked = self._screensaver_active or self._logind_locked
        if locked != self.locked:
            self.locked = locked
            log.info("сессия %s", "заблокирована" if locked else "активна")
            self.on_locked(locked)
```

`wayswitch/dbus_service.py`:
```python
"""D-Bus-сервис демона: управление и статус для CLI и GUI."""

from __future__ import annotations

import logging
from collections.abc import Callable

BUS_NAME = "ru.siberia.WaySwitch"
OBJECT_PATH = "/ru/siberia/WaySwitch"
INTERFACE = "ru.siberia.WaySwitch.Daemon"
INTROSPECTION_XML = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="Pause"/>
    <method name="Resume"/>
    <method name="FixLastWord"><arg type="b" name="ok" direction="out"/></method>
    <method name="FixPhrase"><arg type="b" name="ok" direction="out"/></method>
    <method name="Reload"/>
    <method name="GetStatus"><arg type="a{{sv}}" name="status" direction="out"/></method>
    <signal name="StatusChanged"><arg type="a{{sv}}" name="status"/></signal>
    <signal name="Corrected">
      <arg type="s" name="original"/><arg type="s" name="fixed"/><arg type="b" name="manual"/>
    </signal>
  </interface>
</node>
"""
log = logging.getLogger("wayswitch")


def _to_variant_dict(status: dict):
    from gi.repository import GLib

    out = {}
    for k, v in status.items():
        if isinstance(v, bool):
            out[k] = GLib.Variant("b", v)
        elif isinstance(v, int):
            out[k] = GLib.Variant("i", v)
        elif v is None:
            out[k] = GLib.Variant("i", -1)
        else:
            out[k] = GLib.Variant("s", str(v))
    return GLib.Variant("a{sv}", out)


class DaemonService:
    def __init__(self, controller, on_reload: Callable[[], None]):
        self.controller = controller
        self.on_reload = on_reload
        self._conn = None

    def start(self) -> None:
        from gi.repository import Gio

        self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        self._conn.register_object(OBJECT_PATH, node.interfaces[0], self._on_call, None, None)
        Gio.bus_own_name_on_connection(self._conn, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
                                       None, self._on_name_lost)

    def _on_name_lost(self, _conn, _name) -> None:
        log.error("имя %s занято — демон уже запущен", BUS_NAME)
        raise SystemExit(3)

    def _on_call(self, _conn, _sender, _path, _iface, method, params, invocation) -> None:
        from gi.repository import GLib

        c = self.controller
        if method == "Pause":
            c.pause(); invocation.return_value(None)
        elif method == "Resume":
            c.resume(); invocation.return_value(None)
        elif method == "FixLastWord":
            invocation.return_value(GLib.Variant("(b)", (c.manual_word(),)))
        elif method == "FixPhrase":
            invocation.return_value(GLib.Variant("(b)", (c.manual_phrase(),)))
        elif method == "Reload":
            self.on_reload(); invocation.return_value(None)
        elif method == "GetStatus":
            invocation.return_value(GLib.Variant.new_tuple(_to_variant_dict(c.status())))
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    def emit_status(self, status: dict) -> None:
        if self._conn:
            from gi.repository import GLib

            self._conn.emit_signal(None, OBJECT_PATH, INTERFACE, "StatusChanged",
                                   GLib.Variant.new_tuple(_to_variant_dict(status)))

    def emit_corrected(self, original: str, fixed: str, manual: bool) -> None:
        if self._conn:
            from gi.repository import GLib

            self._conn.emit_signal(None, OBJECT_PATH, INTERFACE, "Corrected",
                                   GLib.Variant("(ssb)", (original, fixed, manual)))
```

`wayswitch/daemon.py`:
```python
"""Сборка демона: устройства, бэкенд, контроллер, D-Bus, охрана сессии — в GLib."""

from __future__ import annotations

import logging
import signal
from pathlib import Path

from wayswitch import config as cfgmod
from wayswitch.actuator import UInputTypist
from wayswitch.backends.select import choose_backend
from wayswitch.controller import Controller
from wayswitch.dbus_service import DaemonService
from wayswitch.detector import SENSITIVITY, Detector, LanguageModel
from wayswitch.keymap import XkbKeymap
from wayswitch.keys import DeviceWatcher
from wayswitch.session import SessionGuard

log = logging.getLogger("wayswitch")


def run(config_path: Path | None, verbose: bool, dry_run: bool) -> int:
    from gi.repository import GLib

    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    config = cfgmod.load(config_path)
    try:
        typist = UInputTypist()
    except OSError as e:
        log.error("не открыть /dev/uinput: %s (правило udev? см. wayswitch doctor)", e)
        return 1
    backend = choose_backend(config.backend.prefer, typist, config.typing.settle_ms)
    log.info("бэкенд раскладки: %s, источники: %s", backend.name,
             [s.xkb_id for s in backend.layouts()])
    if backend.name == "shell":
        from wayswitch.backends.gnome_shell import ShellTypist

        typist_for_controller = ShellTypist(backend)
    else:
        typist_for_controller = typist
    keymap = XkbKeymap.from_names(backend.layouts(), backend.xkb_options())
    models = {}
    for group in range(keymap.num_groups):
        lang = {"ru": "ru", "latin": "en"}.get(keymap.alphabet(group))
        if lang and lang not in models:
            models[lang] = LanguageModel.load(lang)
    if set(models) != {"ru", "en"} or keymap.num_groups != 2:
        log.warning("нужны ровно две раскладки: русская и латинская; авторежим выключен")
        backend.supports_auto = False
    detector = Detector(keymap, models, cfgmod.load_exceptions(),
                        SENSITIVITY[config.general.sensitivity])

    ctx = GLib.MainContext.default()

    def pump() -> None:
        while ctx.pending():
            ctx.iteration(False)

    service: DaemonService | None = None
    controller = Controller(
        config, keymap, detector, backend, typist_for_controller, pump=pump, dry_run=dry_run,
        on_corrected=lambda o, f, m: service and service.emit_corrected(o, f, m),
        on_status=lambda s: service and service.emit_status(s),
        persist_exception=cfgmod.add_exception,
    )
    backend.on_change(controller.on_layout_changed)

    def on_key(code: int, value: int, device_id: int) -> None:
        observe = getattr(backend, "observe_physical", None)
        if observe:
            observe(code, value)
        controller.on_key(code, value, device_id)

    watcher = DeviceWatcher(on_key, controller.on_click, controller.on_resync,
                            controller.on_device_gone, controller.set_caps)
    watcher.start()
    if not any(kind == "keyboard" for _, _, kind in watcher.devices()):
        log.error("ни одной клавиатуры не открыто (права на /dev/input?)")
        return 1

    def on_sleep(entering: bool) -> None:
        controller.buffer.reset()
        if not entering:
            watcher.schedule_rescans()

    guard = SessionGuard(controller.set_locked, on_sleep)
    guard.start()

    def reload() -> None:
        try:
            controller.reload_config(cfgmod.load(config_path))
            detector.exceptions.clear()
            detector.exceptions.update(cfgmod.load_exceptions())
            log.info("конфиг перечитан")
        except cfgmod.ConfigError as e:
            log.error("конфиг не перечитан: %s", e)

    service = DaemonService(controller, reload)
    service.start()

    from gi.repository import Gio

    path = config_path or cfgmod.default_path()
    monitor = Gio.File.new_for_path(str(path)).monitor_file(Gio.FileMonitorFlags.NONE, None)
    monitor.connect("changed", lambda *_: reload())

    loop = GLib.MainLoop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, lambda *_: (loop.quit(), False)[1])
    log.info("WaySwitch запущен%s", " (dry-run)" if dry_run else "")
    try:
        loop.run()
    finally:
        watcher.stop()
        typist.close()
    return 0
```

- [ ] **Step 3: Тест и линтер**: `python -m pytest tests/test_keys_pure.py -q`; `python -m ruff check wayswitch`; `python -m py_compile wayswitch/keys.py wayswitch/session.py wayswitch/dbus_service.py wayswitch/daemon.py`.

- [ ] **Step 4: Коммит** `feat: демон — устройства evdev, охрана сессии, D-Bus, сборка в GLib`.

---
### Task 10: CLI и doctor

**Files:**
- Create: `wayswitch/doctor.py`, `wayswitch/cli.py`, `tests/test_doctor.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `daemon.run`, `dbus_service.BUS_NAME/OBJECT_PATH/INTERFACE`, `DeviceWatcher.devices`, `XkbKeymap.available`, `gnome_common.read_sources`.
- Produces:
  - `@dataclass Check(name: str, ok: bool, hint: str = "", required: bool = True)`
  - `doctor.run_checks() -> list[Check]`, `doctor.format_report(checks) -> str`, `doctor.exit_code(checks) -> int`
  - `cli.main(argv=None) -> int`; подкоманды `run [--verbose] [--dry-run] [--config PATH]`, `dry-run`, `doctor`, `devices`, `pause`, `resume`, `fix`, `status`, `gui`, `version`
  - `cli.call_daemon(method: str) -> object` — вызов метода демона по D-Bus

- [ ] **Step 1: Тесты**

`tests/test_doctor.py`:
```python
from wayswitch.doctor import Check, exit_code, format_report


def test_report_and_exit_code():
    checks = [Check("Сессия Wayland", True), Check("uinput", False, "правило udev", True),
              Check("Расширение", False, "необязательно", False)]
    text = format_report(checks)
    assert "✓ Сессия Wayland" in text and "✗ uinput" in text and "правило udev" in text
    assert exit_code(checks) == 1
    assert exit_code([Check("x", True), Check("y", False, required=False)]) == 0
```

`tests/test_cli.py`:
```python
from wayswitch import __version__
from wayswitch.cli import build_parser


def test_parser_subcommands():
    p = build_parser()
    ns = p.parse_args(["run", "--verbose", "--dry-run"])
    assert ns.command == "run" and ns.verbose and ns.dry_run
    assert p.parse_args(["dry-run"]).command == "dry-run"
    assert p.parse_args(["doctor"]).command == "doctor"
    assert p.parse_args(["fix"]).command == "fix"


def test_version(capsys):
    from wayswitch.cli import main

    assert main(["version"]) == 0
    assert __version__ in capsys.readouterr().out
```

- [ ] **Step 2: Реализация**

`wayswitch/doctor.py`:
```python
"""Проверка окружения перед первым запуском: что есть, чего не хватает, как починить."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Check:
    name: str
    ok: bool
    hint: str = ""
    required: bool = True


def _check(name: str, fn, hint: str, required: bool = True) -> Check:
    try:
        ok, detail = fn()
    except Exception as e:  # noqa: BLE001 — любая ошибка = проверка не пройдена
        ok, detail = False, str(e)
    text = hint if not ok else ""
    if detail:
        text = f"{detail}. {text}".strip()
    return Check(name, ok, text, required)


def run_checks() -> list[Check]:
    checks = []

    def session():
        t = os.environ.get("XDG_SESSION_TYPE", "")
        d = os.environ.get("XDG_CURRENT_DESKTOP", "")
        return t == "wayland" and "gnome" in d.lower(), f"{t or '?'} / {d or '?'}"
    checks.append(_check("Сессия GNOME на Wayland", session,
                         "нужна графическая сессия GNOME Wayland"))

    def modules():
        import evdev  # noqa: F401
        import gi
        gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
        gi.require_version("IBus", "1.0")
        from gi.repository import Adw, Gtk, IBus  # noqa: F401
        return True, ""
    checks.append(_check("Python-модули evdev, Gtk4, Adw, IBus", modules,
                         "apt install python3-evdev python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 "
                         "gir1.2-ibus-1.0"))

    def xkb():
        from wayswitch.keymap import XkbKeymap
        return XkbKeymap.available(), ""
    checks.append(_check("libxkbcommon", xkb, "apt install libxkbcommon0"))

    def sources():
        from wayswitch.backends.gnome_common import read_sources
        from wayswitch.keymap import XkbKeymap
        specs = read_sources()
        km = XkbKeymap.from_names(specs, [])
        alphabets = sorted(km.alphabet(g) for g in range(km.num_groups))
        return alphabets == ["latin", "ru"], ", ".join(s.xkb_id for s in specs)
    checks.append(_check("Две раскладки: русская и латинская", sources,
                         "Настройки → Клавиатура → Источники ввода: оставьте ru и en"))

    def ibus():
        import gi
        gi.require_version("IBus", "1.0")
        from gi.repository import IBus
        bus = IBus.Bus()
        if not bus.is_connected():
            return False, "IBus не отвечает"
        desc = bus.get_global_engine()
        return True, f"текущий движок {desc.get_name() if desc else '?'}"
    checks.append(_check("IBus (состояние раскладки)", ibus,
                         "запустите ibus-daemon или установите расширение WaySwitch"))

    def shell():
        from wayswitch.backends.gnome_shell import shell_extension_present
        return shell_extension_present(), ""
    checks.append(_check("Расширение GNOME Shell", shell,
                         "необязательно: gnome-extensions enable wayswitch@siberia.ru",
                         required=False))

    def uinput():
        return os.access("/dev/uinput", os.W_OK), ""
    checks.append(_check("/dev/uinput доступен на запись", uinput,
                         "sudo packaging/install.sh (правило udev), затем перелогиньтесь"))

    def keyboards():
        from evdev import InputDevice, ecodes, list_devices
        from wayswitch.keys import classify
        found, denied = [], 0
        for path in list_devices():
            try:
                dev = InputDevice(path)
            except PermissionError:
                denied += 1
                continue
            kind = classify(dev.name, set(dev.capabilities().get(ecodes.EV_KEY, [])))
            if kind == "keyboard":
                found.append(dev.name)
            dev.close()
        return bool(found), f"{len(found)} клавиатур, без доступа {denied}"
    checks.append(_check("Клавиатуры читаются", keyboards,
                         "правило udev 60-wayswitch.rules + перелогин"))

    def udev_rule():
        return Path("/etc/udev/rules.d/60-wayswitch.rules").exists(), ""
    checks.append(_check("Правило udev установлено", udev_rule, "sudo packaging/install.sh"))

    def unit():
        p = Path.home() / ".config/systemd/user/wayswitch.service"
        return p.exists(), ""
    checks.append(_check("systemd user-сервис", unit, "sudo packaging/install.sh",
                         required=False))

    def tools():
        return shutil.which("gnome-extensions") is not None, ""
    checks.append(_check("gnome-extensions", tools, "нужно только для расширения",
                         required=False))
    return checks


def format_report(checks: list[Check]) -> str:
    lines = []
    for c in checks:
        mark = "✓" if c.ok else "✗"
        line = f"{mark} {c.name}"
        if c.hint:
            line += f"\n    {c.hint}"
        lines.append(line)
    bad = [c for c in checks if not c.ok and c.required]
    lines.append("")
    lines.append("Всё готово." if not bad else f"Не пройдено обязательных проверок: {len(bad)}.")
    return "\n".join(lines)


def exit_code(checks: list[Check]) -> int:
    return 1 if any(not c.ok and c.required for c in checks) else 0
```

`wayswitch/cli.py`:
```python
"""Командная строка: запуск демона, диагностика, управление по D-Bus."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wayswitch import __version__


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wayswitch",
                                description="Автоисправление раскладки для GNOME/Wayland")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="запустить демон")
    run.add_argument("--verbose", "-v", action="store_true")
    run.add_argument("--dry-run", action="store_true", help="решать, но не печатать")
    run.add_argument("--config", type=Path)
    dry = sub.add_parser("dry-run", help="демон в режиме наблюдения (verbose, без действий)")
    dry.add_argument("--config", type=Path)
    sub.add_parser("doctor", help="проверить окружение")
    sub.add_parser("devices", help="список устройств ввода")
    sub.add_parser("pause", help="приостановить исправления")
    sub.add_parser("resume", help="возобновить")
    sub.add_parser("fix", help="исправить последнее слово")
    sub.add_parser("status", help="состояние демона")
    sub.add_parser("gui", help="открыть настройки")
    sub.add_parser("version")
    return p


def call_daemon(method: str):
    from gi.repository import Gio

    from wayswitch.dbus_service import BUS_NAME, INTERFACE, OBJECT_PATH

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    reply = bus.call_sync(BUS_NAME, OBJECT_PATH, INTERFACE, method, None, None,
                          Gio.DBusCallFlags.NONE, 3000, None)
    return reply.unpack() if reply is not None else None


def _devices() -> int:
    from evdev import InputDevice, ecodes, list_devices

    from wayswitch.keys import classify

    for path in list_devices():
        try:
            dev = InputDevice(path)
        except PermissionError:
            print(f"{path:<20} (нет доступа)")
            continue
        kind = classify(dev.name, set(dev.capabilities().get(ecodes.EV_KEY, [])))
        print(f"{path:<20} {dev.name:<45} {kind or ''}")
        dev.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    if ns.command == "version":
        print(f"wayswitch {__version__}")
        return 0
    if ns.command in ("run", "dry-run"):
        from wayswitch.daemon import run

        dry = ns.command == "dry-run" or ns.dry_run
        verbose = dry or ns.verbose if ns.command == "run" else True
        return run(ns.config, verbose, dry)
    if ns.command == "doctor":
        from wayswitch.doctor import exit_code, format_report, run_checks

        checks = run_checks()
        print(format_report(checks))
        return exit_code(checks)
    if ns.command == "devices":
        return _devices()
    if ns.command == "gui":
        from wayswitch.gui.app import main as gui_main

        return gui_main()
    try:
        if ns.command == "pause":
            call_daemon("Pause"); print("пауза")
        elif ns.command == "resume":
            call_daemon("Resume"); print("работает")
        elif ns.command == "fix":
            (ok,) = call_daemon("FixLastWord")
            print("исправлено" if ok else "нечего исправлять")
        elif ns.command == "status":
            (status,) = call_daemon("GetStatus")
            for k, v in sorted(status.items()):
                print(f"{k}: {v}")
    except Exception as e:  # noqa: BLE001
        print(f"демон не отвечает: {e}", file=sys.stderr)
        return 2
    return 0
```

- [ ] **Step 3: Тесты и линтер проходят**: `python -m pytest tests/test_doctor.py tests/test_cli.py -q`, `python -m ruff check .`.

- [ ] **Step 4: Коммит** `feat: CLI и wayswitch doctor`.

---

### Task 11: Расширение GNOME Shell

**Files:**
- Create: `extension/wayswitch@siberia.ru/metadata.json`, `extension/wayswitch@siberia.ru/extension.js`

**Interfaces:**
- Produces D-Bus `ru.siberia.WaySwitch.Shell` на `/ru/siberia/WaySwitch/Shell`: `GetLayout() -> (u index, s id)`, `SetLayout(u index)`, `TypeText(s text)`, `Backspace(u count)`, сигнал `LayoutChanged(u index)`. Индексы совпадают с порядком `org.gnome.desktop.input-sources sources`.

- [ ] **Step 1: `metadata.json`**

```json
{
  "uuid": "wayswitch@siberia.ru",
  "name": "WaySwitch",
  "description": "Состояние раскладки и печать символов для демона WaySwitch по D-Bus.",
  "shell-version": ["46", "47", "48", "49"],
  "url": "https://github.com/Wetoshkin/WaySwitch",
  "version": 1
}
```

- [ ] **Step 2: `extension.js`**

```javascript
// Расширение WaySwitch: отдаёт демону индекс текущей раскладки, переключает её
// синхронно и печатает произвольные keysym-ы через виртуальное устройство
// Clutter — независимо от раскладки, поэтому демону не нужно ждать смены.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Keyboard from 'resource:///org/gnome/shell/ui/status/keyboard.js';

const BUS_NAME = 'ru.siberia.WaySwitch.Shell';
const OBJECT_PATH = '/ru/siberia/WaySwitch/Shell';
const IFACE_XML = `
<node>
  <interface name="ru.siberia.WaySwitch.Shell">
    <method name="GetLayout">
      <arg type="u" name="index" direction="out"/>
      <arg type="s" name="id" direction="out"/>
    </method>
    <method name="SetLayout"><arg type="u" name="index" direction="in"/></method>
    <method name="TypeText"><arg type="s" name="text" direction="in"/></method>
    <method name="Backspace"><arg type="u" name="count" direction="in"/></method>
    <signal name="LayoutChanged"><arg type="u" name="index"/></signal>
  </interface>
</node>`;

export default class WaySwitchExtension extends Extension {
    enable() {
        this._ism = Keyboard.getInputSourceManager();
        const seat = Clutter.get_default_backend().get_default_seat();
        this._vdev = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE_XML, this);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        this._nameId = Gio.DBus.session.own_name(BUS_NAME,
            Gio.BusNameOwnerFlags.NONE, null, null);
        this._changedId = this._ism.connect('current-source-changed', () => {
            const src = this._ism.currentSource;
            if (src)
                this._dbus.emit_signal('LayoutChanged', new GLib.Variant('(u)', [src.index]));
        });
    }

    disable() {
        if (this._changedId) {
            this._ism.disconnect(this._changedId);
            this._changedId = null;
        }
        if (this._nameId) {
            Gio.DBus.session.unown_name(this._nameId);
            this._nameId = null;
        }
        if (this._dbus) {
            this._dbus.unexport();
            this._dbus = null;
        }
        this._vdev = null;
        this._ism = null;
    }

    // --- D-Bus методы (имена совпадают с XML) ---------------------------------

    GetLayout() {
        const src = this._ism.currentSource;
        return src ? [src.index, src.id] : [0, ''];
    }

    SetLayout(index) {
        const src = this._ism.inputSources[index];
        if (!src)
            throw new Error(`нет источника ввода с индексом ${index}`);
        src.activate(true);
    }

    TypeText(text) {
        for (const ch of text) {
            const keyval = Clutter.unicode_to_keysym(ch.codePointAt(0));
            this._tap(keyval);
        }
    }

    Backspace(count) {
        for (let i = 0; i < count; i++)
            this._tap(Clutter.KEY_BackSpace);
    }

    _tap(keyval) {
        const t = Clutter.get_current_event_time();
        this._vdev.notify_keyval(t, keyval, Clutter.KeyState.PRESSED);
        this._vdev.notify_keyval(t, keyval, Clutter.KeyState.RELEASED);
    }
}
```

- [ ] **Step 3: Проверка синтаксиса**: `node --input-type=module --check < extension/wayswitch@siberia.ru/extension.js` (если `node` есть; иначе — внимательно перечитать). Убедиться, что файл в LF и UTF-8.

- [ ] **Step 4: Коммит** `feat: расширение GNOME Shell — состояние раскладки и печать keysym`.

---
### Task 12: GUI — окно настроек и трей

**Files:**
- Create: `wayswitch/gui/app.py`, `wayswitch/gui/daemon_proxy.py`, `wayswitch/gui/tray.py`, `wayswitch/gui/autostart.py`, `tests/test_gui_pure.py`

**Interfaces:**
- Consumes: `config` (load/save/exceptions), `dbus_service.BUS_NAME/OBJECT_PATH/INTERFACE`.
- Produces:
  - `daemon_proxy.DaemonProxy(on_status: Callable[[dict|None], None], on_corrected: Callable[[str,str,bool], None])`: `start()`, `call(method) -> object|None`, `status: dict | None` (None — демон не запущен)
  - `autostart.is_service_enabled() -> bool`, `autostart.set_service_enabled(on: bool) -> None` (`systemctl --user enable/disable --now wayswitch.service`), `autostart.is_tray_enabled()`, `autostart.set_tray_enabled(on)` (файл `~/.config/autostart/ru.siberia.WaySwitch.desktop` с `Exec=wayswitch-gui --tray`), `autostart.tray_desktop_text() -> str` (чистая)
  - `tray.StatusNotifier(on_toggle_auto, on_pause, on_fix, on_settings, on_quit)`: `start()`, `update(status: dict | None)`; чистая `tray.menu_layout(status) -> list[tuple[str, str, bool]]` (id, подпись, включён)
  - `app.main(argv=None) -> int`; флаг `--tray` — стартовать без окна, только значок

- [ ] **Step 1: Тесты чистых функций `tests/test_gui_pure.py`**

```python
from wayswitch.gui.autostart import tray_desktop_text
from wayswitch.gui.tray import menu_layout


def test_desktop_entry():
    text = tray_desktop_text()
    assert "[Desktop Entry]" in text and "Exec=wayswitch-gui --tray" in text
    assert "X-GNOME-Autostart-enabled=true" in text


def test_menu_layout_reflects_status():
    items = dict((i, (label, enabled)) for i, label, enabled in menu_layout(None))
    assert items["settings"][1] and not items["pause"][1]
    items = dict((i, (label, enabled)) for i, label, enabled in
                 menu_layout({"paused": True, "auto_correct": True, "active": False}))
    assert "Возобновить" in items["pause"][0]
    assert "✓" in items["auto"][0]
```

- [ ] **Step 2: Реализация**

`wayswitch/gui/daemon_proxy.py`:
```python
"""Связь GUI с демоном по D-Bus: статус, сигналы, вызовы."""

from __future__ import annotations

import logging
from collections.abc import Callable

from wayswitch.dbus_service import BUS_NAME, INTERFACE, OBJECT_PATH

log = logging.getLogger("wayswitch.gui")


class DaemonProxy:
    def __init__(self, on_status: Callable[[dict | None], None],
                 on_corrected: Callable[[str, str, bool], None]):
        self.on_status = on_status
        self.on_corrected = on_corrected
        self.status: dict | None = None
        self._proxy = None

    def start(self) -> None:
        from gi.repository import Gio

        self._proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None, BUS_NAME, OBJECT_PATH,
            INTERFACE, None)
        self._proxy.connect("g-signal", self._on_signal)
        self._proxy.connect("notify::g-name-owner", lambda *_: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        if self._proxy.get_name_owner() is None:
            self.status = None
        else:
            try:
                (self.status,) = self._proxy.call_sync("GetStatus", None, 0, 2000, None).unpack()
            except Exception as e:  # noqa: BLE001
                log.warning("GetStatus: %s", e)
                self.status = None
        self.on_status(self.status)

    def _on_signal(self, _p, _s, signal: str, params) -> None:
        if signal == "StatusChanged":
            (self.status,) = params.unpack()
            self.on_status(self.status)
        elif signal == "Corrected":
            self.on_corrected(*params.unpack())

    def call(self, method: str):
        if self._proxy is None or self._proxy.get_name_owner() is None:
            return None
        try:
            reply = self._proxy.call_sync(method, None, 0, 3000, None)
            return reply.unpack() if reply is not None else None
        except Exception as e:  # noqa: BLE001
            log.warning("%s: %s", method, e)
            return None
```

`wayswitch/gui/autostart.py`:
```python
"""Автозапуск: systemd user-сервис демона и XDG-autostart трея."""

from __future__ import annotations

import subprocess
from pathlib import Path

SERVICE = "wayswitch.service"
DESKTOP_NAME = "ru.siberia.WaySwitch.desktop"


def _autostart_dir() -> Path:
    import os

    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart"


def tray_desktop_text() -> str:
    return "\n".join([
        "[Desktop Entry]", "Type=Application", "Name=WaySwitch",
        "Comment=Значок WaySwitch в трее", "Exec=wayswitch-gui --tray",
        "Icon=input-keyboard", "Terminal=false", "X-GNOME-Autostart-enabled=true",
        "X-GNOME-Autostart-Delay=3", "",
    ])


def is_service_enabled() -> bool:
    r = subprocess.run(["systemctl", "--user", "is-enabled", SERVICE],
                       capture_output=True, text=True)
    return r.stdout.strip() == "enabled"


def set_service_enabled(on: bool) -> None:
    subprocess.run(["systemctl", "--user", "enable" if on else "disable", "--now", SERVICE],
                   check=False)


def is_tray_enabled() -> bool:
    return (_autostart_dir() / DESKTOP_NAME).exists()


def set_tray_enabled(on: bool) -> None:
    path = _autostart_dir() / DESKTOP_NAME
    if on:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tray_desktop_text(), encoding="utf-8")
    elif path.exists():
        path.unlink()
```

`wayswitch/gui/tray.py` — StatusNotifierItem напрямую через Gio.DBus (без libappindicator, она GTK3-only):
```python
"""Значок в трее по протоколу StatusNotifierItem + com.canonical.dbusmenu.

На GNOME виден при расширении AppIndicator (в Ubuntu включено). Реализованы
только те методы, которые вызывает хост: свойства SNI, Activate, меню.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

log = logging.getLogger("wayswitch.gui")

SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <method name="Activate"><arg type="i" name="x" direction="in"/><arg type="i" name="y" direction="in"/></method>
    <method name="SecondaryActivate"><arg type="i" name="x" direction="in"/><arg type="i" name="y" direction="in"/></method>
    <method name="ContextMenu"><arg type="i" name="x" direction="in"/><arg type="i" name="y" direction="in"/></method>
    <method name="Scroll"><arg type="i" name="delta" direction="in"/><arg type="s" name="orientation" direction="in"/></method>
    <signal name="NewIcon"/><signal name="NewTitle"/><signal name="NewToolTip"/><signal name="NewStatus"><arg type="s"/></signal>
  </interface>
</node>
"""
MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="Status" type="s" access="read"/>
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/><arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/><arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/><arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/><arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/><arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/><arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events" direction="in"/><arg type="ai" name="idErrors" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/><arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids" direction="in"/><arg type="ai" name="updatesNeeded" direction="out"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <signal name="LayoutUpdated"><arg type="u" name="revision"/><arg type="i" name="parent"/></signal>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/><arg type="a(ias)" name="removedProps"/>
    </signal>
  </interface>
</node>
"""
MENU_PATH = "/ru/siberia/WaySwitch/Menu"
ITEM_PATH = "/StatusNotifierItem"


def menu_layout(status: dict | None) -> list[tuple[str, str, bool]]:
    """Пункты меню: (id, подпись, активен). Зависит от состояния демона."""
    running = status is not None
    auto = bool(status and status.get("auto_correct"))
    paused = bool(status and status.get("paused"))
    return [
        ("auto", ("✓ " if auto else "   ") + "Автоисправление", running),
        ("pause", "Возобновить" if paused else "Пауза", running),
        ("fix", "Исправить последнее слово", running),
        ("settings", "Настройки…", True),
        ("quit", "Выход", True),
    ]


class StatusNotifier:
    def __init__(self, on_toggle_auto: Callable[[], None], on_pause: Callable[[], None],
                 on_fix: Callable[[], None], on_settings: Callable[[], None],
                 on_quit: Callable[[], None]):
        self._actions = {"auto": on_toggle_auto, "pause": on_pause, "fix": on_fix,
                         "settings": on_settings, "quit": on_quit}
        self._status: dict | None = None
        self._revision = 1
        self._conn = None

    def start(self) -> None:
        from gi.repository import Gio

        self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        sni = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        menu = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        self._conn.register_object(ITEM_PATH, sni, self._sni_call, self._sni_get, None)
        self._conn.register_object(MENU_PATH, menu, self._menu_call, self._menu_get, None)
        name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        Gio.bus_own_name_on_connection(self._conn, name, Gio.BusNameOwnerFlags.NONE,
                                       lambda *_: self._register(name), None)

    def _register(self, name: str) -> None:
        from gi.repository import Gio, GLib

        try:
            self._conn.call_sync("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher",
                                 "org.kde.StatusNotifierWatcher", "RegisterStatusNotifierItem",
                                 GLib.Variant("(s)", (name,)), None, Gio.DBusCallFlags.NONE,
                                 2000, None)
        except Exception as e:  # noqa: BLE001
            log.info("трей недоступен (нет StatusNotifierWatcher): %s", e)

    # --- SNI ---------------------------------------------------------------------

    def _icon(self) -> str:
        if self._status is None:
            return "input-keyboard-symbolic"
        return "changes-prevent-symbolic" if self._status.get("paused") \
            else "input-keyboard-symbolic"

    def _sni_get(self, _c, _s, _p, _i, prop: str):
        from gi.repository import GLib

        tooltip = "WaySwitch: " + ("демон не запущен" if self._status is None else
                                   "пауза" if self._status.get("paused") else "работает")
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "wayswitch"),
            "Title": GLib.Variant("s", "WaySwitch"),
            "Status": GLib.Variant("s", "Active"),
            "IconName": GLib.Variant("s", self._icon()),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", ("", [], "WaySwitch", tooltip)),
            "Menu": GLib.Variant("o", MENU_PATH),
            "ItemIsMenu": GLib.Variant("b", True),
        }
        return values.get(prop)

    def _sni_call(self, _c, _s, _p, _i, method: str, _params, invocation) -> None:
        if method == "Activate":
            self._actions["settings"]()
        invocation.return_value(None)

    # --- dbusmenu ----------------------------------------------------------------

    def _items(self):
        from gi.repository import GLib

        out = []
        for i, (ident, label, enabled) in enumerate(menu_layout(self._status), start=1):
            props = {"label": GLib.Variant("s", label), "enabled": GLib.Variant("b", enabled)}
            out.append((i, ident, props))
        return out

    def _menu_get(self, _c, _s, _p, _i, prop: str):
        from gi.repository import GLib

        return {"Version": GLib.Variant("u", 3),
                "Status": GLib.Variant("s", "normal")}.get(prop)

    def _menu_call(self, _c, _s, _p, _i, method: str, params, invocation) -> None:
        from gi.repository import GLib

        items = self._items()
        if method == "GetLayout":
            children = [GLib.Variant("(ia{sv}av)", (i, props, [])) for i, _, props in items]
            root = GLib.Variant("(ia{sv}av)", (0, {"children-display": GLib.Variant("s", "submenu")},
                                               children))
            invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("u", self._revision), root))
        elif method == "GetGroupProperties":
            ids = set(params.unpack()[0])
            props = [(i, p) for i, _, p in items if not ids or i in ids]
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (props,)))
        elif method == "GetProperty":
            item_id, name = params.unpack()
            for i, _, p in items:
                if i == item_id and name in p:
                    invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("v", p[name])))
                    return
            invocation.return_dbus_error("org.freedesktop.DBus.Error.InvalidArgs", name)
        elif method == "Event":
            item_id, event_id, _data, _ts = params.unpack()
            if event_id == "clicked":
                for i, ident, _ in items:
                    if i == item_id:
                        self._actions[ident]()
            invocation.return_value(None)
        elif method == "EventGroup":
            for item_id, event_id, _d, _t in params.unpack()[0]:
                if event_id == "clicked":
                    for i, ident, _ in items:
                        if i == item_id:
                            self._actions[ident]()
            invocation.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    def update(self, status: dict | None) -> None:
        from gi.repository import GLib

        self._status = status
        self._revision += 1
        if self._conn:
            self._conn.emit_signal(None, MENU_PATH, "com.canonical.dbusmenu", "LayoutUpdated",
                                   GLib.Variant("(ui)", (self._revision, 0)))
            self._conn.emit_signal(None, ITEM_PATH, "org.kde.StatusNotifierItem", "NewIcon", None)
            self._conn.emit_signal(None, ITEM_PATH, "org.kde.StatusNotifierItem", "NewToolTip",
                                   None)
```

`wayswitch/gui/app.py`:
```python
"""Окно настроек WaySwitch на GTK4/libadwaita и запуск трея."""

from __future__ import annotations

import sys

from wayswitch import __version__
from wayswitch import config as cfgmod
from wayswitch.gui import autostart
from wayswitch.gui.daemon_proxy import DaemonProxy
from wayswitch.gui.tray import StatusNotifier

APP_ID = "ru.siberia.WaySwitch"


def main(argv: list[str] | None = None) -> int:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, Gtk

    argv = sys.argv[1:] if argv is None else argv
    tray_only = "--tray" in argv

    class Window(Adw.ApplicationWindow):
        def __init__(self, app, proxy: DaemonProxy):
            super().__init__(application=app, title="WaySwitch")
            self.set_default_size(560, 640)
            self.proxy = proxy
            self.cfg = cfgmod.load()
            toolbar = Adw.ToolbarView()
            header = Adw.HeaderBar()
            self.stack = Adw.ViewStack()
            switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
            header.set_title_widget(switcher)
            toolbar.add_top_bar(header)
            toolbar.set_content(self.stack)
            self.set_content(toolbar)
            self.stack.add_titled_with_icon(self._status_page(), "status", "Статус",
                                            "dialog-information-symbolic")
            self.stack.add_titled_with_icon(self._settings_page(), "settings", "Настройки",
                                            "preferences-system-symbolic")
            self.stack.add_titled_with_icon(self._exceptions_page(), "exceptions", "Исключения",
                                            "edit-clear-all-symbolic")
            self.stack.add_titled_with_icon(self._autostart_page(), "autostart", "Автозапуск",
                                            "system-run-symbolic")
            self.render_status(proxy.status)

        # --- Статус ---------------------------------------------------------------
        def _status_page(self):
            page = Adw.PreferencesPage()
            group = Adw.PreferencesGroup(title="Демон")
            self.row_state = Adw.ActionRow(title="Состояние")
            self.row_backend = Adw.ActionRow(title="Бэкенд раскладки")
            self.row_layout = Adw.ActionRow(title="Текущая раскладка")
            self.row_count = Adw.ActionRow(title="Исправлений за сессию")
            self.btn_pause = Gtk.Button(label="Пауза", valign=Gtk.Align.CENTER)
            self.btn_pause.connect("clicked", lambda *_: self._toggle_pause())
            self.row_state.add_suffix(self.btn_pause)
            for r in (self.row_state, self.row_backend, self.row_layout, self.row_count):
                group.add(r)
            page.add(group)
            test = Adw.PreferencesGroup(title="Проверка",
                                        description="Наберите здесь «ghbdtn » — слово должно "
                                                    "исправиться в «привет».")
            test.add(Adw.EntryRow(title="Тестовое поле"))
            page.add(test)
            log_group = Adw.PreferencesGroup(title="Последние исправления")
            self.log_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.log_box.add_css_class("boxed-list")
            log_group.add(self.log_box)
            page.add(log_group)
            return page

        def render_status(self, status: dict | None):
            if status is None:
                self.row_state.set_subtitle("демон не запущен — systemctl --user start wayswitch")
                self.btn_pause.set_sensitive(False)
                return
            self.btn_pause.set_sensitive(True)
            self.btn_pause.set_label("Возобновить" if status.get("paused") else "Пауза")
            state = "пауза" if status.get("paused") else \
                "заблокировано" if status.get("locked") else \
                "работает" if status.get("active") else "только ручной режим"
            self.row_state.set_subtitle(state + (" (dry-run)" if status.get("dry_run") else ""))
            self.row_backend.set_subtitle(str(status.get("backend", "?")))
            self.row_layout.set_subtitle(f"индекс {status.get('layout_index')}")
            self.row_count.set_subtitle(
                f"авто {status.get('corrections_total', 0)}, вручную "
                f"{status.get('manual_total', 0)}, откатов {status.get('undo_total', 0)}")

        def add_correction(self, original: str, fixed: str, manual: bool):
            row = Adw.ActionRow(title=f"{original} → {fixed}",
                                subtitle="вручную" if manual else "автоматически")
            self.log_box.prepend(row)
            while (child := self.log_box.get_row_at_index(20)) is not None:
                self.log_box.remove(child)

        def _toggle_pause(self):
            st = self.proxy.status or {}
            self.proxy.call("Resume" if st.get("paused") else "Pause")

        # --- Настройки --------------------------------------------------------------
        def _settings_page(self):
            page = Adw.PreferencesPage()
            g = Adw.PreferencesGroup(title="Автоисправление")
            self.sw_auto = Adw.SwitchRow(title="Исправлять автоматически",
                                         subtitle="На пробеле после слова")
            self.sw_auto.set_active(self.cfg.general.auto_correct)
            g.add(self.sw_auto)
            self.cb_sens = Adw.ComboRow(title="Чувствительность")
            names = Gtk.StringList.new(["Осторожная", "Обычная", "Агрессивная"])
            self.cb_sens.set_model(names)
            self.cb_sens.set_selected(cfgmod.SENSITIVITY_NAMES.index(self.cfg.general.sensitivity))
            g.add(self.cb_sens)
            page.add(g)
            g2 = Adw.PreferencesGroup(title="Ручное исправление")
            self.cb_manual = Adw.ComboRow(title="Жест")
            self.cb_manual.set_model(Gtk.StringList.new(
                ["Двойной Shift (тройной — фраза)", "Клавиша Pause", "Выключено"]))
            self.cb_manual.set_selected(cfgmod.MANUAL_MODES.index(self.cfg.gesture.manual))
            g2.add(self.cb_manual)
            self.sp_undo = Adw.SpinRow.new_with_range(0, 60, 1)
            self.sp_undo.set_title("Окно отката, с")
            self.sp_undo.set_subtitle("Жест в это время возвращает слово и учит исключение")
            self.sp_undo.set_value(self.cfg.general.undo_window_sec)
            g2.add(self.sp_undo)
            page.add(g2)
            g3 = Adw.PreferencesGroup(title="Тонкая настройка")
            self.sp_settle = Adw.SpinRow.new_with_range(0, 2000, 5)
            self.sp_settle.set_title("Запас после смены раскладки, мс")
            self.sp_settle.set_value(self.cfg.typing.settle_ms)
            g3.add(self.sp_settle)
            self.sp_delay = Adw.SpinRow.new_with_range(0, 100, 1)
            self.sp_delay.set_title("Пауза между клавишами, мс")
            self.sp_delay.set_value(self.cfg.typing.key_delay_ms)
            g3.add(self.sp_delay)
            self.cb_backend = Adw.ComboRow(title="Бэкенд раскладки")
            self.cb_backend.set_model(Gtk.StringList.new(
                ["Автовыбор", "Расширение GNOME Shell", "IBus", "Системный хоткей"]))
            self.cb_backend.set_selected(cfgmod.BACKEND_PREFS.index(self.cfg.backend.prefer))
            g3.add(self.cb_backend)
            page.add(g3)
            gs = Adw.PreferencesGroup()
            btn = Gtk.Button(label="Сохранить", halign=Gtk.Align.END)
            btn.add_css_class("suggested-action")
            btn.connect("clicked", lambda *_: self._save())
            gs.add(btn)
            page.add(gs)
            return page

        def _save(self):
            c = self.cfg
            c.general.auto_correct = self.sw_auto.get_active()
            c.general.sensitivity = cfgmod.SENSITIVITY_NAMES[self.cb_sens.get_selected()]
            c.gesture.manual = cfgmod.MANUAL_MODES[self.cb_manual.get_selected()]
            c.general.undo_window_sec = float(self.sp_undo.get_value())
            c.typing.settle_ms = int(self.sp_settle.get_value())
            c.typing.key_delay_ms = int(self.sp_delay.get_value())
            c.backend.prefer = cfgmod.BACKEND_PREFS[self.cb_backend.get_selected()]
            cfgmod.save(c)
            self.proxy.call("Reload")

        # --- Исключения -------------------------------------------------------------
        def _exceptions_page(self):
            page = Adw.PreferencesPage()
            g = Adw.PreferencesGroup(title="Слова, которые не исправляются",
                                     description="Попадают сюда после отката жестом")
            self.entry_exc = Adw.EntryRow(title="Добавить слово")
            self.entry_exc.connect("apply", lambda *_: self._add_exception())
            self.entry_exc.set_show_apply_button(True)
            g.add(self.entry_exc)
            self.exc_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.exc_box.add_css_class("boxed-list")
            g.add(self.exc_box)
            page.add(g)
            self._render_exceptions()
            return page

        def _render_exceptions(self):
            while (child := self.exc_box.get_row_at_index(0)) is not None:
                self.exc_box.remove(child)
            for word in sorted(cfgmod.load_exceptions()):
                row = Adw.ActionRow(title=word)
                btn = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
                btn.add_css_class("flat")
                btn.connect("clicked", lambda _b, w=word: self._remove_exception(w))
                row.add_suffix(btn)
                self.exc_box.append(row)

        def _add_exception(self):
            word = self.entry_exc.get_text().strip()
            if word:
                cfgmod.add_exception(word)
                self.entry_exc.set_text("")
                self._render_exceptions()
                self.proxy.call("Reload")

        def _remove_exception(self, word: str):
            cfgmod.remove_exception(word)
            self._render_exceptions()
            self.proxy.call("Reload")

        # --- Автозапуск -------------------------------------------------------------
        def _autostart_page(self):
            page = Adw.PreferencesPage()
            g = Adw.PreferencesGroup(title="Автозапуск")
            sw1 = Adw.SwitchRow(title="Демон при входе в систему",
                                subtitle="systemctl --user enable wayswitch")
            sw1.set_active(autostart.is_service_enabled())
            sw1.connect("notify::active", lambda r, _: autostart.set_service_enabled(r.get_active()))
            g.add(sw1)
            sw2 = Adw.SwitchRow(title="Значок в трее при входе",
                                subtitle="Нужно расширение AppIndicator (в Ubuntu есть)")
            sw2.set_active(autostart.is_tray_enabled())
            sw2.connect("notify::active", lambda r, _: autostart.set_tray_enabled(r.get_active()))
            g.add(sw2)
            page.add(g)
            about = Adw.PreferencesGroup(title="О программе")
            about.add(Adw.ActionRow(title="WaySwitch", subtitle=f"версия {__version__}"))
            page.add(about)
            return page

    class App(Adw.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
            self.window: Window | None = None
            self.proxy = DaemonProxy(self._on_status, self._on_corrected)
            self.tray = StatusNotifier(
                on_toggle_auto=self._toggle_auto,
                on_pause=lambda: self.proxy.call(
                    "Resume" if (self.proxy.status or {}).get("paused") else "Pause"),
                on_fix=lambda: self.proxy.call("FixLastWord"),
                on_settings=self.show_window, on_quit=self.quit)

        def do_startup(self):
            Adw.Application.do_startup(self)
            self.proxy.start()
            self.tray.start()
            if tray_only:
                self.hold()  # живём без окна

        def do_activate(self):
            if not tray_only or self.window:
                self.show_window()

        def show_window(self):
            if self.window is None:
                self.window = Window(self, self.proxy)
                self.window.connect("close-request", self._on_close)
            self.window.present()

        def _on_close(self, *_):
            if tray_only:
                self.window.set_visible(False)
                return True
            return False

        def _on_status(self, status):
            self.tray.update(status)
            if self.window:
                self.window.render_status(status)

        def _on_corrected(self, original, fixed, manual):
            if self.window:
                self.window.add_correction(original, fixed, manual)

        def _toggle_auto(self):
            cfg = cfgmod.load()
            cfg.general.auto_correct = not cfg.general.auto_correct
            cfgmod.save(cfg)
            self.proxy.call("Reload")

    return App().run([sys.argv[0]] + [a for a in argv if a != "--tray"])
```

- [ ] **Step 3: Проверка**: `python -m pytest tests/test_gui_pure.py -q`; `python -m ruff check wayswitch/gui`; `python -m py_compile wayswitch/gui/app.py wayswitch/gui/tray.py wayswitch/gui/daemon_proxy.py wayswitch/gui/autostart.py`. Живой запуск невозможен до VM — отметить в PROGRESS.md.

- [ ] **Step 4: Коммит** `feat: GUI — окно настроек GTK4/libadwaita и трей StatusNotifierItem`.

---
### Task 13: Упаковка и установка

**Files:**
- Create: `packaging/60-wayswitch.rules`, `packaging/wayswitch.service`, `packaging/ru.siberia.WaySwitch.desktop`, `packaging/install.sh`, `packaging/uninstall.sh`, `tests/test_packaging.py`, `tests/e2e_vm.py`

- [ ] **Step 1: Тест `tests/test_packaging.py`** (проверяет содержимое файлов, не запускает их)

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "packaging"


def test_udev_rules_use_uaccess():
    text = (ROOT / "60-wayswitch.rules").read_text(encoding="utf-8")
    assert 'KERNEL=="uinput"' in text and 'TAG+="uaccess"' in text
    assert 'ENV{ID_INPUT_KEYBOARD}=="1"' in text and 'ENV{ID_INPUT_MOUSE}=="1"' in text
    assert "GROUP=" not in text  # без групп: права даёт uaccess


def test_service_unit():
    text = (ROOT / "wayswitch.service").read_text(encoding="utf-8")
    assert "ExecStart=/usr/local/bin/wayswitch run" in text
    assert "After=graphical-session.target" in text
    assert "WantedBy=default.target" in text


def test_scripts_are_lf_and_executable_shebang():
    for name in ("install.sh", "uninstall.sh"):
        raw = (ROOT / name).read_bytes()
        assert b"\r\n" not in raw, name
        assert raw.startswith(b"#!/bin/bash"), name
```

- [ ] **Step 2: Файлы**

`packaging/60-wayswitch.rules`:
```
# WaySwitch: права пользователю активного сеанса, без групп.
KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"
SUBSYSTEM=="input", KERNEL=="event*", ENV{ID_INPUT_KEYBOARD}=="1", TAG+="uaccess"
SUBSYSTEM=="input", KERNEL=="event*", ENV{ID_INPUT_MOUSE}=="1", TAG+="uaccess"
SUBSYSTEM=="input", KERNEL=="event*", ENV{ID_INPUT_TOUCHPAD}=="1", TAG+="uaccess"
```

`packaging/wayswitch.service`:
```
[Unit]
Description=WaySwitch — автоисправление раскладки
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/local/bin/wayswitch run
Restart=on-failure
RestartSec=3
# Код 3 — второй экземпляр, перезапускать не надо.
RestartPreventExitStatus=3

[Install]
WantedBy=default.target
```

`packaging/ru.siberia.WaySwitch.desktop`:
```
[Desktop Entry]
Type=Application
Name=WaySwitch
Comment=Настройки автоисправления раскладки
Exec=wayswitch-gui
Icon=input-keyboard
Terminal=false
Categories=Settings;Utility;
Keywords=раскладка;layout;punto;
```

`packaging/install.sh`:
```bash
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
udevadm control --reload-rules && udevadm trigger || true

echo "== Ярлык"
install -m 644 "$SRC/packaging/ru.siberia.WaySwitch.desktop" /usr/local/share/applications/ 2>/dev/null \
    || { mkdir -p /usr/local/share/applications; install -m 644 "$SRC/packaging/ru.siberia.WaySwitch.desktop" /usr/local/share/applications/; }

echo "== systemd user-сервис для $SUDO_USER"
UNIT_DIR="$USER_HOME/.config/systemd/user"
sudo -u "$SUDO_USER" mkdir -p "$UNIT_DIR"
install -o "$SUDO_USER" -m 644 "$SRC/packaging/wayswitch.service" "$UNIT_DIR/wayswitch.service"

echo "== Расширение GNOME Shell"
EXT_DIR="$USER_HOME/.local/share/gnome-shell/extensions/wayswitch@siberia.ru"
sudo -u "$SUDO_USER" mkdir -p "$EXT_DIR"
cp "$SRC"/extension/wayswitch@siberia.ru/* "$EXT_DIR/"
chown -R "$SUDO_USER" "$EXT_DIR"

cat <<EOF

Готово. Дальше от имени пользователя $SUDO_USER:
  1. Перелогиньтесь (правила udev применяются к новому сеансу).
  2. wayswitch doctor
  3. systemctl --user enable --now wayswitch
  4. Необязательно: gnome-extensions enable wayswitch@siberia.ru
  5. wayswitch-gui — настройки и трей.
EOF
```

`packaging/uninstall.sh`:
```bash
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
```

`tests/e2e_vm.py` — сквозной тест для VM, **не** собирается pytest (имя без `test_`):
```python
#!/usr/bin/env python3
"""Сквозной тест в VM: виртуальная «физическая» клавиатура печатает ghbdtn␣,
а мы читаем evdev-узел выходного устройства демона и сверяем последовательность.

Запуск при работающем демоне и открытом текстовом поле в фокусе:
    python3 tests/e2e_vm.py
Ожидание: демон молчал во время набора, затем 7×Backspace, затем перепечатка
«привет » кодами ru-раскладки; все клавиши отпущены.
"""

import sys
import time

from evdev import InputDevice, UInput, ecodes, list_devices

VIRTUAL_NAME = "WaySwitch Virtual Keyboard"
WORD = [34, 35, 48, 32, 20, 49]  # g h b d t n
EXPECTED_TAIL = [34, 35, 48, 32, 20, 49, 57]  # те же коды в ru = привет + пробел


def find_output():
    for path in list_devices():
        dev = InputDevice(path)
        if dev.name == VIRTUAL_NAME:
            return dev
    sys.exit("не найдено выходное устройство демона — он запущен?")


def main() -> int:
    out = find_output()  # без grab: копию потока читаем параллельно с компоситором
    fake = UInput({ecodes.EV_KEY: list(range(1, 128))}, name="WaySwitch E2E Keyboard")
    time.sleep(1.0)  # демон открывает новое устройство с задержкой
    for code in WORD:
        fake.write(ecodes.EV_KEY, code, 1); fake.syn()
        fake.write(ecodes.EV_KEY, code, 0); fake.syn()
        time.sleep(0.05)
    silent = [e for e in _drain(out, 0.2) if e.type == ecodes.EV_KEY]
    fake.write(ecodes.EV_KEY, 57, 1); fake.syn()
    fake.write(ecodes.EV_KEY, 57, 0); fake.syn()
    events = [e for e in _drain(out, 1.5) if e.type == ecodes.EV_KEY]
    presses = [e.code for e in events if e.value == 1 and e.code not in (42, 54)]
    held = set()
    for e in events:
        (held.add if e.value else held.discard)(e.code)
    ok = (not silent and presses[:7] == [14] * 7 and presses[7:] == EXPECTED_TAIL
          and not held)
    print("во время набора:", "тихо" if not silent else f"{len(silent)} событий (плохо)")
    print("нажатия демона:", presses)
    print("зажатых осталось:", sorted(held))
    print("РЕЗУЛЬТАТ:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def _drain(dev, seconds):
    import select

    deadline = time.time() + seconds
    out = []
    while time.time() < deadline:
        r, _, _ = select.select([dev.fd], [], [], max(0.0, deadline - time.time()))
        if r:
            out.extend(dev.read())
    return out


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Тест проходит** (`python -m pytest tests/test_packaging.py -q`).

- [ ] **Step 4: Коммит** `feat: установка — udev, systemd user-сервис, ярлык, сквозной тест для VM`.

---

### Task 14: Документация и трекер прогресса

**Files:**
- Modify: `README.md` (переписать), Create: `CLAUDE.md`, `docs/testing-vm.md`, `docs/PROGRESS.md`

- [ ] **Step 1: `README.md`** — структура (русский язык, логотип оставить):
  1. Заголовок, логотип, бейджи (MIT, Python 3.11+, GNOME Wayland).
  2. Что это: аналог Punto Switcher для GNOME на Wayland; одно предложение о принципе (evdev без захвата, словарь + триграммы, переключение через IBus/расширение, перепечатка через uinput).
  3. Возможности списком: автоисправление на пробеле; двойной/тройной Shift; откат жестом + обучение; ранний триггер URL; охрана сессии; трей и настройки; `doctor`/`dry-run`.
  4. Установка: `git clone`, `sudo packaging/install.sh`, перелогин, `wayswitch doctor`, `systemctl --user enable --now wayswitch`, `wayswitch-gui`.
  5. Использование: таблица жестов; пауза; CLI-команды.
  6. Настройка: пример `config.toml` из спецификации, раздел 6; исключения.
  7. Как это работает: короткая схема потока событий (из спецификации, раздел 4) и ссылка на спецификацию.
  8. Ограничения честно: поля паролей, приложения с автодополнением, отмена не откатывает уже отправленное, `uaccess` даёт чтение клавиатуры любой программе пользователя.
  9. Разработка: `pip install pytest ruff`, `python -m pytest`, `python tools/build_data.py`, тесты на Windows/CI, `docs/testing-vm.md`.
  10. Лицензии: MIT; данные — CC-BY-SA 4.0 (`wayswitch/data/LICENSE`).
  Без упоминания сторонних проектов.

- [ ] **Step 2: `CLAUDE.md`** проекта (для будущих сессий): назначение; карта модулей (таблица из плана); правило «чистые модули не импортируют evdev/gi»; как гонять тесты на Windows и что пропускается; как собрать данные; где спецификация и план; правила коммитов (русский, Co-Authored-By); что расширение — ESM и `shell-version`; что `docs/PROGRESS.md` обновляется после каждой задачи.

- [ ] **Step 3: `docs/testing-vm.md`** — чек-лист первого запуска в VM:
  1. Ubuntu 24.04+ (GNOME Wayland), две раскладки ru/en, Super+Space переключает.
  2. `sudo packaging/install.sh`, перелогин.
  3. `wayswitch doctor` — все обязательные ✓.
  4. Проверка IBus вручную: `ibus engine` → `xkb:us::eng`; `ibus engine xkb:ru::rus` → индикатор в панели сменился? (открытый вопрос 13.1 спецификации; если нет — включить расширение и `prefer = "shell"`).
  5. `wayswitch dry-run` в терминале, набрать `ghbdtn ` в gedit — в логе `[dry-run] … 'ghbdtn' → 'привет '`.
  6. `wayswitch run --verbose`: то же слово исправляется; замерить «за N мс» в логе; подобрать `settle_ms`, если первые буквы уходят в старой раскладке.
  7. Двойной Shift, тройной Shift, откат жестом (слово попадает в `exceptions.txt`).
  8. `python3 tests/e2e_vm.py` при открытом текстовом поле.
  9. Блокировка экрана (Super+L): набрать текст до блокировки, разблокировать — ничего не перепечатывается.
  10. Браузер: адресная строка с автодополнением — записать поведение.
  11. `systemctl --user enable --now wayswitch`, `wayswitch-gui`, трей (Ubuntu), тест-поле.
  Таблица «что записать»: версия GNOME, бэкенд, settle_ms, время исправления, найденные проблемы.

- [ ] **Step 4: `docs/PROGRESS.md`**

```markdown
# Прогресс WaySwitch v2

Обновляется после каждой задачи плана `docs/superpowers/plans/2026-09-23-wayswitch-v2.md`.
Статусы: ⬜ не начато · 🔄 в работе · ✅ готово (тесты зелёные) · ⚠️ готово, не проверено на живой системе

| # | Задача | Статус | Заметки |
|---|---|---|---|
| 0 | Каркас, keycodes, CI, удаление v1 | ⬜ | |
| 1 | Конфигурация | ⬜ | |
| 2 | Раскладки (xkbcommon, таблицы) | ⬜ | |
| 3 | Триграммы и словари | ⬜ | |
| 4 | Детектор + тест качества | ⬜ | |
| 5 | Буфер и жест Shift | ⬜ | |
| 6 | Исполнитель (план, uinput) | ⬜ | |
| 7 | Контроллер | ⬜ | |
| 8 | Бэкенды GNOME (IBus, расширение, хоткей) | ⬜ | |
| 9 | Демон: устройства, сессия, D-Bus | ⬜ | |
| 10 | CLI и doctor | ⬜ | |
| 11 | Расширение GNOME Shell | ⬜ | |
| 12 | GUI и трей | ⬜ | |
| 13 | Упаковка и установка | ⬜ | |
| 14 | Документация | ⬜ | |
| 15 | Финальная проверка и публикация | ⬜ | |

## Что проверить на живой GNOME (VM)

- [ ] `ibus engine xkb:ru::rus` переключает раскладку Shell (спец. 13.1)
- [ ] реальный `settle_ms` для IBus-пути
- [ ] расширение: `notify_keyval` печатает кириллицу при латинской раскладке
- [ ] трей виден в Ubuntu (AppIndicator)
- [ ] адресная строка браузера с автодополнением
- [ ] `tests/e2e_vm.py` проходит
```

Исполнитель каждой задачи после коммита переводит свою строку в ✅ (или ⚠️ для задач 8, 9, 11, 12, 13 — их нельзя проверить без GNOME) и дописывает заметку (например, достигнутые recall/fpr в задаче 4). Эта правка входит в коммит задачи или в отдельный `docs: прогресс`.

- [ ] **Step 5: Коммит** `docs: README, CLAUDE.md, чек-лист VM, трекер прогресса`.

---

### Task 15: Финальная проверка и публикация

- [ ] **Step 1:** `python -m ruff check .` — чисто; `python -m pytest -q` — всё зелёное (кроме skip'ов xkb на Windows); `node --input-type=module --check < extension/wayswitch@siberia.ru/extension.js` при наличии node.
- [ ] **Step 2:** `git status` чистый; проверить, что `wayswitch/data/*.gz` закоммичены, а `.gitkeep` удалён; `git log --oneline` — осмысленные сообщения.
- [ ] **Step 3:** Обновить `docs/PROGRESS.md` (строка 15 → ✅, раздел VM остаётся открытым).
- [ ] **Step 4:** `git push origin main`. Убедиться: `gh run list --limit 1` показывает запуск CI; дождаться результата (`gh run watch`), при падении — починить и запушить снова.

---

## Самопроверка плана

- Покрытие спецификации: 5.1 → Task 9 (`keys.py`); 5.2 → Task 2; 5.3 → Task 5; 5.4 → Task 4; 5.5 → Task 6; 5.6 → Task 8; 5.7 → Task 7 + Task 9 (`session.py`); 5.8 → Task 10; 6 → Task 1; 7 → Task 11; 8 → Task 12; 9 → Task 9 (`dbus_service.py`); 10 → Task 13; 11 → тесты в каждой задаче + Task 13 (`e2e_vm.py`) + Task 14 (`testing-vm.md`); 12–13 → документация.
- Имена согласованы: `KeyPress` определён в `buffer.py`, детектор принимает любые объекты с `code/shift/caps` (в тестах `fakes.K`); `plan_fix(delete_count, target_text, target_group, keymap, caps_on)`; `execute(plan, typist, backend, abort, key_delay, sleep, switch_timeout)`; бэкенд — `layouts/xkb_options/current/set/wait_applied/on_change/close`; контроллер — `on_key/on_click/on_layout_changed/on_resync/on_device_gone/set_caps/set_locked/pause/resume/manual_word/manual_phrase/status/reload_config`.
