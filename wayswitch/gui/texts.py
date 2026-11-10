"""Тексты и модели окна без GTK: что показать по статусу демона, история
исправлений, подписи значений. Всё здесь — чистые функции и классы, их
проверяют тесты без графической сессии (tests/test_gui_pure.py).

Строки-источники английские: это msgid каталога gettext. Перевод берётся при
вызове, а не при импорте, поэтому выбор языка процесса не зависит от порядка
импортов и не оставляет глобального состояния между тестами.

Правило для подписей: обычные слова вместо «демон», «бэкенд», «индекс 0».
Технические подробности — в «О WaySwitch → Устранение неполадок».
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from wayswitch import i18n

# Как бэкенд называется для человека: «Раскладка: русская · через …».
# Значения — английские msgid; переводятся при показе.
BACKEND_HUMAN = {
    "shell": "via the GNOME Shell extension",
    "ibus": "via IBus",
    "kde": "via KDE Plasma",
    "hotkey": "via the system shortcut",
    "sway": "via Sway IPC",
    "hyprland": "via Hyprland IPC",
    "cosmic": "via COSMIC",
    "umbriel": "via Umbriel",
    "compositor": "via the compositor shortcut",
}

# Варианты «Способ» в Параметрах → Система; порядок = config.BACKEND_PREFS.
BACKEND_CHOICES = ("Automatic", "GNOME Shell extension", "KDE Plasma", "IBus",
                   "GNOME shortcut", "Sway IPC", "Hyprland IPC",
                   "Compositor shortcut", "COSMIC", "Umbriel IPC")

SENSITIVITY_CHOICES = ("Cautious", "Normal", "Bold")

# Варианты «Жест»; порядок = config.MANUAL_MODES.
MANUAL_CHOICES = ("Double Shift", "Key press", "Pause key", "Disabled")

LAYOUT_HUMAN = {"ru": "Russian", "latin": "English", "other": "other (not supported)"}

# Раскладки XKB по-человечески — для шага «Проверка системы».
XKB_HUMAN = {"us": "English (US)", "gb": "English (UK)", "ru": "Russian",
             "ru+phonetic": "Russian (phonetic)", "ru+typewriter": "Russian (typewriter)"}

# Описание области работы для GUI. В CLI/doctor остаётся keymap.LAYOUT_SCOPE:
# это собственный текст окна, а не переписанная диагностика.
LAYOUT_SCOPE_TEXT = (
    "WaySwitch corrects typing only between Russian and English layouts. "
    "Other sources can stay in the system: when one is selected, WaySwitch "
    "does not change the text")


def _(message: str) -> str:
    return i18n.gettext(message)


def xkb_human(ids: str) -> str:
    """«us, ru» → «English (US), Russian»."""
    parts = []
    for part in (p.strip() for p in ids.split(",") if p.strip()):
        name = _(XKB_HUMAN[part]) if part in XKB_HUMAN else part
        if part.startswith("ru") and name:
            name = name[:1].upper() + name[1:]
        parts.append(name)
    return ", ".join(parts)


def plural(n: int, one: str, few: str, many: str) -> str:
    """Согласование числительного на языке интерфейса.

    one/few/many — уже переведённые формы. Русский использует все три,
    английский — one для 1 и many для остальных.
    """
    n = abs(int(n))
    if i18n.language() != "ru":
        return one if n == 1 else many
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def format_seconds(value: float) -> str:
    """5.0 → «5 с», 0.5 → «0,5 с», 0 → «нет»."""
    if value <= 0:
        return _("none")
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    if i18n.language() == "ru":
        text = text.replace(".", ",")
    return f"{text} {_('s')}"


def format_ms(value: float) -> str:
    return f"{int(round(value))} {_('ms')}"


def layout_human(alphabet: str | None) -> str:
    return _(LAYOUT_HUMAN.get(alphabet or "", "unknown"))


@dataclass(frozen=True)
class StatusView:
    """Карточка состояния в главном окне."""

    kind: str          # "on" | "paused" | "locked" | "manual" | "off"
    title: str
    subtitle: str
    switch_active: bool
    switch_sensitive: bool


def pause_method(status: dict) -> str:
    # Протокол D-Bus: строки не переводятся.
    return "Resume" if status.get("paused") or status.get("lock_confirmation_required") else "Pause"


def status_view(status: dict | None) -> StatusView:
    """Что написать в карточке состояния по ответу GetStatus.

    None — служба не отвечает: окно в этом случае показывает отдельную
    страницу «WaySwitch не запущен», а не карточку.
    """
    if status is None:
        return StatusView("off", _("WaySwitch is not running"),
                          _("Words are not being corrected right now"), False, False)
    if status.get("lock_confirmation_required"):
        locked = bool(status.get("locked"))
        return StatusView("locked",
                          _("Screen is locked") if locked else _("Confirm COSMIC sign-in"),
                          _("After signing in and unlocking, turn the switch on "
                            "to resume correction"), False, not locked)
    if status.get("paused"):
        return StatusView("paused", _("Paused"),
                          _("Words are not corrected. Turn it on when you are done."),
                          False, True)
    if status.get("locked"):
        return StatusView("locked", _("Screen is locked"),
                          _("Correction will continue after you sign in"), True, False)
    backend_key = BACKEND_HUMAN.get(str(status.get("backend") or ""))
    backend = _(backend_key) if backend_key else ""
    layout = layout_human(status.get("layout"))
    where = _("Layout: {layout}").format(layout=layout)
    if backend:
        where += " · " + backend
    if status.get("layout_supported") is False:
        return StatusView("manual", _("Waiting for a Russian or English layout"),
                          _(LAYOUT_SCOPE_TEXT) + ". "
                          + _("Both languages and a working layout switch are required"),
                          True, True)
    if not status.get("active") or not status.get("auto_correct", True):
        # Служба работает, но на пробеле не исправляет: либо выключено в
        # параметрах, либо раскладку прочитать нечем (только ручной жест).
        reason = (_("WaySwitch does not correct anything — it is turned off in Settings. "
                    "You can fix a word with the gesture (double Shift by default)")
                  if not status.get("auto_correct", True)
                  else _("WaySwitch cannot correct right now: it cannot see which layout "
                         "is active. You can fix a word with the gesture "
                         "(double Shift by default)"))
        return StatusView("manual", _("Manual only"), reason, True, True)
    return StatusView("on", _("Correction is on"), where, True, True)


# Клавиши действий над выделенным: (поле конфига, заголовок, подпись,
# подсказка в диалоге выбора клавиши). Значения — английские msgid.
SELECTION_KEY_ROWS = (
    ("selection_key", "Fix selection",
     "One press converts the selected text to the other layout",
     "Press the key you will use to fix the selected text"),
    ("translit_key", "Transliteration",
     "Привет ↔ Privet for the selected text",
     "Press a key to transliterate the selected text"),
    ("numbers_key", "Number in words",
     "125 → сто двадцать пять for the selected number",
     "Press a key to write the selected number in words"),
)


@dataclass(frozen=True)
class SelectionFeedback:
    """Уведомление, когда служба не подтвердила исправление выделенного."""

    title: str
    body: str


def selection_guidance(status: dict | None) -> str | None:
    """Причина подтверждённой недоступности действий с выделением."""
    if status is not None and status.get("selection_supported") is False:
        return _("Selection actions need the accessibility service")
    return None


def selection_feedback(result) -> SelectionFeedback | None:
    """Что показать по ответу DaemonProxy.call("FixSelection").

    (True,) — исправление подтверждено, уведомление не нужно; (False,), None и
    любой неожиданный ответ — не подтвердилось, показываем одно сообщение.
    """
    if isinstance(result, tuple) and len(result) == 1 and result[0] is True:
        return None
    return SelectionFeedback(
        _("Could not confirm the correction"),
        _("Check the text in the field: the correction may have been applied partially. "
          "A single-line selection in a regular editable field works best. "
          "Calling from the menu can move focus — you can press the assigned key instead."),
    )


def format_times(n: float) -> str:
    """1 → «с первого раза», 2 → «2 раза», 5 → «5 раз»."""
    n = int(n)
    if n == 1:
        return _("first try")
    return i18n.ngettext("{n} time", "{n} times", n).format(n=n)


def tray_title(desktop: str) -> str:
    """Как называется место для значка в этой среде."""
    return _("Top bar icon") if desktop == "gnome" else _("Tray icon")


def tray_hint(desktop: str) -> str:
    return {
        "gnome": _("Works out of the box on Ubuntu. On other systems enable the "
                   "“AppIndicator” extension in the Extensions app"),
        "kde": _("In the Plasma system tray"),
        "sway": _("For panels with a tray: Waybar, ironbar, AGS"),
        "hyprland": _("For panels with a tray: Waybar, ironbar, AGS"),
    }.get(desktop, _("In the notification area of the panel"))


def tray_summary(status: dict | None, update: str | None = None) -> str:
    """Состояние службы и итоги сеанса для меню и подсказки значка."""
    if status is None:
        return _("Not running")
    if status.get("locked"):
        return _("Screen is locked")
    if status.get("lock_confirmation_required"):
        return _("Confirm COSMIC sign-in")
    if status.get("paused"):
        return _("Paused")
    if status.get("layout_supported") is False:
        return _("Waiting for a Russian or English layout")
    total = int(status.get("corrections_total", 0) or 0) + \
        int(status.get("manual_total", 0) or 0)
    words = i18n.ngettext("word", "words", total)
    if not status.get("active", True) or not status.get("auto_correct", True):
        template = _("Manual only · {count} {words} this session")
    else:
        template = _("Correcting · {count} {words} this session")
    text = template.format(count=total, words=words)
    if update:
        text += _(" · version {version} available").format(version=update)
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
# они остаются в «Устранение неполадок». Тексты — английские msgid.
ONBOARDING_CHECKS = {
    "wayland": ("Wayland session", "{detail}",
                "The old X11 graphics mode is active. Sign out, click the gear icon on "
                "the sign-in screen and choose a session without “Xorg”", None),
    "layouts": ("Russian and English layouts", "{detail}",
                "Add Russian and English layouts in the system settings. "
                "You do not need to remove others. Until the ru/en pair appears, "
                "correction is unavailable", "layouts"),
    "layout_scope": ("Working languages", LAYOUT_SCOPE_TEXT, LAYOUT_SCOPE_TEXT, None),
    "uinput": ("Virtual keyboard", "Corrections can be typed",
               "Restart the computer", None),
    "keyboards": ("Keyboard access", "Keyboard is readable",
                  "Save your documents and restart the computer "
                  "(or sign out and sign in again)", "logout"),
    "switch": ("Layout switching", "Available",
               "WaySwitch cannot switch the layout. Details: menu ☰ → "
               "“About WaySwitch” → “Troubleshooting”", None),
    # On success the detail is a stable value from doctor (for example
    # "GNOME"). Russian explanations from doctor are never shown here.
    "focus": ("Input field detection", "Available",
              "No reliable focus in an input field. Protected auto-correction is waiting; "
              "manual correction is available. Check a regular text field in the "
              "required app via “Troubleshooting”", None),
    "extension": ("GNOME Shell extension", "Enabled",
                  "Needed to switch the layout. Click “Enable”, then sign out and "
                  "sign in again", "extension"),
    "extension_missing": ("GNOME Shell extension", "Enabled",
                          "Not installed. Reinstall WaySwitch with the same command "
                          "you used before", None),
    # Включено, но GNOME держит старую копию (или ещё не загрузил новую):
    # кнопка «Включить» тут бесполезна, помогает только новый вход. Не
    # блокирует «Продолжить» — старая версия умеет главное.
    "extension_relogin": ("GNOME Shell extension", "Enabled",
                          "Enabled, but GNOME will load the new version only after "
                          "signing in again. You can continue and sign out later", "logout"),
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
            # Детали doctor — диагностические данные и не переводятся.
            subtitle = _(ok_text).format(detail=detail or _("Done"))
            state = "ok"
        else:
            subtitle = _(fail_text)
            state = "fail" if check.required else "optional"
        rows.append(OnboardingRow(check.key, _(title), subtitle, state,
                                  None if check.ok else action))
    return rows


def onboarding_hint(rows: list[OnboardingRow]) -> str:
    """Подпись над списком проверок."""
    if onboarding_blocking(rows):
        return _("Fix the items marked in red — WaySwitch will work after that.")
    if any(r.key == "focus" and r.state == "optional" for r in rows):
        return _("You can continue. Protected auto-correction is not confirmed: "
                 "reliable focus in an input field is required.")
    if any(r.state == "optional" and r.action for r in rows):
        return _("You can continue. Circled items are optional and can be done later.")
    return _("Everything is ready.")


def onboarding_blocking(rows: list[OnboardingRow]) -> bool:
    """Есть ли непройденная обязательная проверка (кнопка «Продолжить» серая)."""
    return any(r.state == "fail" for r in rows)
