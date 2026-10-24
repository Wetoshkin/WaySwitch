"""Тексты и модели окна без GTK: что показать по статусу демона, история
исправлений, подписи значений. Всё здесь — чистые функции и классы, их
проверяют тесты без графической сессии (tests/test_gui_pure.py).

Правило для подписей: обычные слова вместо «демон», «бэкенд», «индекс 0».
Технические подробности — в «О WaySwitch → Устранение неполадок».
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

# Как бэкенд называется для человека: «Раскладка: русская · через …».
BACKEND_HUMAN = {
    "shell": "через расширение GNOME Shell",
    "ibus": "через IBus",
    "kde": "через KDE Plasma",
    "hotkey": "через сочетание клавиш системы",
    "sway": "через Sway IPC",
    "hyprland": "через Hyprland IPC",
    "compositor": "через сочетание клавиш композитора",
}

# Варианты «Способ» в Параметрах → Система; порядок = config.BACKEND_PREFS.
BACKEND_CHOICES = ("Автоматически", "Расширение GNOME Shell", "KDE Plasma", "IBus",
                   "Сочетание клавиш GNOME", "Sway IPC", "Hyprland IPC",
                   "Сочетание клавиш композитора")

SENSITIVITY_CHOICES = ("Осторожная", "Обычная", "Смелая")

# Варианты «Жест»; порядок = config.MANUAL_MODES.
MANUAL_CHOICES = ("Двойной Shift", "Нажатие клавиши", "Клавиша Pause", "Выключен")

LAYOUT_HUMAN = {"ru": "русская", "latin": "английская"}

# Раскладки XKB по-человечески — для шага «Проверка системы».
XKB_HUMAN = {"us": "English (US)", "gb": "English (UK)", "ru": "Русская",
             "ru+phonetic": "Русская (фонетическая)", "ru+typewriter": "Русская (машинопись)"}


def xkb_human(ids: str) -> str:
    """«us, ru» → «English (US), Русская»."""
    parts = [p.strip() for p in ids.split(",") if p.strip()]
    return ", ".join(XKB_HUMAN.get(p, p) for p in parts)


def plural(n: int, one: str, few: str, many: str) -> str:
    """Русское согласование: 1 слово, 2 слова, 5 слов."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def format_seconds(value: float) -> str:
    """5.0 → «5 с», 0.5 → «0,5 с», 0 → «нет»."""
    if value <= 0:
        return "нет"
    text = f"{value:.1f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{text} с"


def format_ms(value: float) -> str:
    return f"{int(round(value))} мс"


def layout_human(alphabet: str | None) -> str:
    return LAYOUT_HUMAN.get(alphabet or "", "неизвестна")


@dataclass(frozen=True)
class StatusView:
    """Карточка состояния в главном окне."""

    kind: str          # "on" | "paused" | "locked" | "manual" | "off"
    title: str
    subtitle: str
    switch_active: bool
    switch_sensitive: bool


def status_view(status: dict | None) -> StatusView:
    """Что написать в карточке состояния по ответу GetStatus.

    None — служба не отвечает: окно в этом случае показывает отдельную
    страницу «WaySwitch не запущен», а не карточку.
    """
    if status is None:
        return StatusView("off", "WaySwitch не запущен",
                          "Слова сейчас не исправляются", False, False)
    if status.get("paused"):
        return StatusView("paused", "На паузе",
                          "Слова не исправляются. Включите, когда закончите.", False, True)
    if status.get("locked"):
        return StatusView("locked", "Экран заблокирован",
                          "Исправление продолжится после входа", True, False)
    backend = BACKEND_HUMAN.get(str(status.get("backend") or ""), "")
    layout = layout_human(status.get("layout"))
    where = f"Раскладка: {layout}" + (f" · {backend}" if backend else "")
    if not status.get("active") or not status.get("auto_correct", True):
        # Служба работает, но на пробеле не исправляет: либо выключено в
        # параметрах, либо раскладку прочитать нечем (только ручной жест).
        reason = ("Сам WaySwitch не исправляет — это выключено в Параметрах. "
                  "Исправить слово можно жестом (по умолчанию — дважды Shift)"
                  if not status.get("auto_correct", True)
                  else "Сам WaySwitch сейчас не исправляет: не видно, какая раскладка "
                       "включена. Исправить слово можно жестом (по умолчанию — дважды Shift)")
        return StatusView("manual", "Только вручную", reason, True, True)
    return StatusView("on", "Исправление включено", where, True, True)


def tray_title(desktop: str) -> str:
    """Как называется место для значка в этой среде."""
    return "Значок в верхней панели" if desktop == "gnome" else "Значок в трее"


def tray_hint(desktop: str) -> str:
    return {
        "gnome": "В Ubuntu работает сразу. В других системах нужно расширение "
                 "«AppIndicator» — включите его в приложении «Расширения»",
        "kde": "В системном лотке Plasma",
        "sway": "Для панелей с треем: Waybar, ironbar, AGS",
        "hyprland": "Для панелей с треем: Waybar, ironbar, AGS",
    }.get(desktop, "В области уведомлений панели")


def tray_summary(status: dict | None, update: str | None = None) -> str:
    """Вторая строка в меню значка: «Исправляет · 48 слов за сеанс»."""
    if status is None:
        return "Не запущен"
    if status.get("paused"):
        return "На паузе"
    total = int(status.get("corrections_total", 0) or 0) + \
        int(status.get("manual_total", 0) or 0)
    text = f"Исправляет · {total} {plural(total, 'слово', 'слова', 'слов')} за сеанс"
    if update:
        text += f" · есть версия {update}"
    return text


# --- история исправлений ---------------------------------------------------------

@dataclass
class HistoryEntry:
    original: str
    fixed: str
    manual: bool
    at: float
    # Пользователь нажал «больше не исправлять» — слово ушло в исключения.
    excluded: bool = False

    @property
    def kind(self) -> str:
        if self.excluded:
            return "excluded"
        return "manual" if self.manual else "auto"

    def clock(self) -> str:
        return time.strftime("%H:%M", time.localtime(self.at))


class History:
    """Последние исправления за сеанс GUI (живёт в приложении, не в окне:
    окно можно закрыть, значок в панели продолжает собирать историю)."""

    def __init__(self, limit: int = 20):
        self._items: deque[HistoryEntry] = deque(maxlen=limit)

    def add(self, original: str, fixed: str, manual: bool,
            at: float | None = None) -> HistoryEntry:
        entry = HistoryEntry(original, fixed, manual, time.time() if at is None else at)
        self._items.appendleft(entry)
        return entry

    def mark_excluded(self, original: str) -> None:
        for entry in self._items:
            if entry.original.lower() == original.lower():
                entry.excluded = True

    def clear(self) -> None:
        self._items.clear()

    def __iter__(self):
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)


# --- первый запуск: человеческие названия проверок doctor ----------------------

# key проверки doctor → (заголовок, подпись при успехе, подпись при неудаче,
# действие-кнопка или None). Проверки без записи здесь в онбординге не видны:
# они остаются в «Устранение неполадок».
ONBOARDING_CHECKS = {
    "wayland": ("Сессия Wayland", "{detail}",
                "Сейчас включён старый режим графики X11. Выйдите из системы, на экране "
                "входа нажмите на шестерёнку и выберите сеанс без «Xorg»", None),
    "layouts": ("Две раскладки", "{detail}",
                "Добавьте русскую и английскую раскладки в настройках системы",
                "layouts"),
    "uinput": ("Виртуальная клавиатура", "Можно печатать исправления",
               "Перезагрузите компьютер", None),
    "keyboards": ("Доступ к клавиатуре", "Клавиатура читается",
                  "Сохраните документы и перезагрузите компьютер "
                  "(или выйдите из системы и войдите снова)", "logout"),
    "switch": ("Переключение раскладки", "{detail}",
               "WaySwitch не может переключать раскладку. Подробности — в меню ☰ → "
               "«О WaySwitch» → «Устранение неполадок»",
               None),
    "extension": ("Расширение GNOME Shell", "Включено",
                  "Нужно, чтобы переключать раскладку. Нажмите «Включить», затем "
                  "перезагрузите компьютер",
                  "extension"),
}


@dataclass(frozen=True)
class OnboardingRow:
    key: str
    title: str
    subtitle: str
    state: str          # "ok" | "fail" | "optional"
    action: str | None


def onboarding_rows(checks) -> list[OnboardingRow]:
    """Строки шага «Проверка системы» из результата doctor.run_checks()."""
    rows = []
    for check in checks:
        spec = ONBOARDING_CHECKS.get(getattr(check, "key", ""))
        if spec is None:
            continue
        title, ok_text, fail_text, action = spec
        detail = (check.hint or "").split(". ")[0] if check.ok else ""
        if check.key == "layouts":
            detail = xkb_human(detail)
        if check.ok:
            subtitle = ok_text.format(detail=detail or "Готово")
            state = "ok"
        else:
            subtitle = fail_text
            state = "fail" if check.required else "optional"
        rows.append(OnboardingRow(check.key, title, subtitle, state,
                                  None if check.ok else action))
    return rows


def onboarding_blocking(rows: list[OnboardingRow]) -> bool:
    """Есть ли непройденная обязательная проверка (кнопка «Продолжить» серая)."""
    return any(r.state == "fail" for r in rows)
