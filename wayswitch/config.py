"""Конфигурация демона: ~/.config/wayswitch/config.toml и список исключений.

Читаем через tomllib, пишем собственным минимальным сериализатором —
формат плоский (секция → ключ → скаляр), сторонняя библиотека не нужна.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from wayswitch.keycodes import GESTURE_KEY_NAMES, KEY_NAMES

SENSITIVITY_NAMES = ("conservative", "normal", "aggressive")
MANUAL_MODES = ("double_shift", "tap_key", "pause_key", "none")
BACKEND_PREFS = ("auto", "shell", "kde", "ibus", "hotkey")
# Чем печатать символы при shell-бэкенде: клавишами расширения (keysym-ы, не
# зависят от раскладки) или сканерами через uinput (как настоящая клавиатура).
SHELL_TYPING = ("extension", "uinput")


class ConfigError(ValueError):
    """Неверное значение в конфиге."""


@dataclass
class General:
    auto_correct: bool = True
    sensitivity: str = "normal"
    phrase_timeout_sec: float = 8.0
    undo_window_sec: float = 5.0
    # Пауза автоисправления после Backspace/стрелок/Delete и после ручной смены
    # раскладки, секунды. 0 — не замирать.
    edit_guard_sec: float = 1.0
    # «пирогб» → «пирог,»: последняя буква — знак из другой раскладки.
    punct_fix: bool = True
    # «чтото» → «что-то», «кембы» → «кем бы».
    particles_fix: bool = True
    # Не трогать поля паролей (at-spi видит их ролью password text).
    skip_password_fields: bool = True
    # Короткий сигнал после автоисправления (как в Punto Switcher).
    sound_on_fix: bool = False
    # «сонатуБаха» → «сонату Баха»: потерянный пробел виден по регистру.
    split_words_fix: bool = True
    # Исправлять слово, законченное Tab или Enter. По умолчанию нет: в
    # терминале и IDE Tab выбирает автодополнение, и перепечатка после него
    # попадёт уже в другое место.
    fix_on_tab: bool = False
    fix_on_enter: bool = False
    # «ПРивет» → «Привет»: Shift отпущен на букву позже.
    two_capitals_fix: bool = True
    # Раз в сутки спрашивать GitHub о новой версии (только GUI, демон в сеть
    # не ходит). Отключается в Параметрах и на последнем шаге первого запуска.
    check_updates: bool = True


@dataclass
class Gesture:
    manual: str = "double_shift"
    # Клавиша для жеста manual = "tap_key": одиночный тап переворачивает
    # последнее слово (по умолчанию левый Ctrl, как в Punto Switcher).
    tap_key: str = "leftctrl"
    pause_hotkey: str = ""


@dataclass
class Typing:
    # Пауза после переключения раскладки, прежде чем печатать. Приложению нужно
    # время принять новый keymap: при 30 мс последняя клавиша серии (пробел
    # после исправленного слова) до поля не доходила - проверено на GNOME 51,
    # 120 мс дают стабильный результат.
    settle_ms: int = 120
    key_delay_ms: int = 0


@dataclass
class Backend:
    prefer: str = "auto"
    shell_typing: str = "extension"


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


def apps_path() -> Path:
    """Список исключённых приложений (по строке на приложение)."""
    return config_dir() / "apps.txt"


def load_apps(path: Path | None = None) -> list[str]:
    """Прочитать исключённые приложения; файла нет — пустой список."""
    path = path or apps_path()
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def save_apps(apps: list[str], path: Path | None = None) -> None:
    """Записать список приложений (по одному в строке, без дублей)."""
    path = path or apps_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    seen: list[str] = []
    for app in apps:
        name = app.strip()
        if name and name.lower() not in {s.lower() for s in seen}:
            seen.append(name)
    text = "\n".join(seen) + ("\n" if seen else "")
    path.write_text(text, encoding="utf-8")


def add_app(app: str, path: Path | None = None) -> bool:
    """Добавить приложение; False — уже было."""
    apps = load_apps(path)
    if any(app.strip().lower() == a.lower() for a in apps):
        return False
    apps.append(app.strip())
    save_apps(apps, path)
    return True


def remove_app(app: str, path: Path | None = None) -> bool:
    """Убрать приложение; False — такого не было."""
    apps = load_apps(path)
    left = [a for a in apps if a.lower() != app.strip().lower()]
    if len(left) == len(apps):
        return False
    save_apps(left, path)
    return True


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
    if not 0.0 <= g.edit_guard_sec <= 10.0:
        raise ConfigError("edit_guard_sec: 0..10")
    if ge.manual not in MANUAL_MODES:
        raise ConfigError(f"manual: допустимо {MANUAL_MODES}")
    if ge.tap_key.lower() not in GESTURE_KEY_NAMES:
        # Не только «неизвестная»: буква или цифра сломали бы жест — он бы
        # срабатывал на каждой набранной букве.
        raise ConfigError(
            f"tap_key: недопустимая клавиша жеста {ge.tap_key!r}, "
            f"можно: leftctrl, rightctrl, scrolllock, pause, f1…f12 и подобные")
    if ge.pause_hotkey and ge.pause_hotkey.lower() not in KEY_NAMES:
        raise ConfigError(f"pause_hotkey: неизвестная клавиша {ge.pause_hotkey!r}")
    if not 0 <= t.settle_ms <= 2000:
        raise ConfigError("settle_ms: 0..2000")
    if not 0 <= t.key_delay_ms <= 100:
        raise ConfigError("key_delay_ms: 0..100")
    if b.prefer not in BACKEND_PREFS:
        raise ConfigError(f"prefer: допустимо {BACKEND_PREFS}")
    if b.shell_typing not in SHELL_TYPING:
        raise ConfigError(f"shell_typing: допустимо {SHELL_TYPING}")


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
