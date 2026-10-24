"""Небольшие строительные блоки окна поверх стандартных виджетов libadwaita.

Импорт gi — внутри функций, как и во всём пакете gui: модуль можно
импортировать без GTK (тесты, CI без дисплея).
"""

from __future__ import annotations

from collections.abc import Callable


def keycap(text: str, large: bool = False):
    """Надпись в виде клавиши: «Shift», «Левый Ctrl»."""
    from gi.repository import Gtk

    label = Gtk.Label(label=text, valign=Gtk.Align.CENTER)
    label.add_css_class("ws-keycap")
    if large:
        label.add_css_class("large")
    return label


def keycaps(names: list[str]):
    """Несколько клавиш подряд: Shift Shift."""
    from gi.repository import Gtk

    box = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
    for name in names:
        box.append(keycap(name))
    return box


def flat_icon_button(icon: str, tooltip: str, on_click: Callable[[], None]):
    from gi.repository import Gtk

    button = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER, tooltip_text=tooltip)
    button.add_css_class("flat")
    # У кнопки-значка нет текста — подпись для скринридера.
    button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
    button.connect("clicked", lambda *_: on_click())
    return button


class StepRow:
    """Строка «название · значение с единицами · − +».

    AdwSpinRow показывает голое число, а единицы тогда уезжают в название
    («Окно отката, с»). Здесь значение подписано рядом: «5 с», «30 мс».
    """

    def __init__(self, title: str, subtitle: str, value: float, lower: float, upper: float,
                 step: float, fmt: Callable[[float], str],
                 on_change: Callable[[float], None]):
        from gi.repository import Adw, Gtk

        self.lower, self.upper, self.step, self.fmt = lower, upper, step, fmt
        self.on_change = on_change
        self.row = Adw.ActionRow(title=title, subtitle=subtitle)
        self.label = Gtk.Label(valign=Gtk.Align.CENTER, width_chars=6, xalign=1.0)
        self.label.add_css_class("numeric")
        box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        self.minus = Gtk.Button(icon_name="list-remove-symbolic", tooltip_text="Меньше")
        self.plus = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Больше")
        for button, sign in ((self.minus, -1), (self.plus, 1)):
            button.add_css_class("circular")
            button.update_property([Gtk.AccessibleProperty.LABEL],
                                   [f"{title}: {'меньше' if sign < 0 else 'больше'}"])
            button.connect("clicked", lambda _b, s=sign: self._bump(s))
            box.append(button)
        self.row.add_suffix(self.label)
        self.row.add_suffix(box)
        self.value = value
        self._render()

    def _bump(self, sign: int) -> None:
        value = round(self.value + sign * self.step, 3)
        value = min(self.upper, max(self.lower, value))
        if value != self.value:
            self.value = value
            self._render()
            self.on_change(value)

    def set_value(self, value: float) -> None:
        self.value = min(self.upper, max(self.lower, value))
        self._render()

    def _render(self) -> None:
        self.label.set_label(self.fmt(self.value))
        self.minus.set_sensitive(self.value > self.lower)
        self.plus.set_sensitive(self.value < self.upper)


def boxed_list():
    from gi.repository import Gtk

    box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    box.add_css_class("boxed-list")
    return box


def clear_list(box) -> None:
    while (child := box.get_row_at_index(0)) is not None:
        box.remove(child)


def run_in_thread(work: Callable[[], object], done: Callable[[object], None]) -> None:
    """Выполнить work() в потоке и отдать результат в главный цикл GTK.

    Всё, что ходит в D-Bus чужих программ (at-spi) или в сеть, — только так:
    синхронный вызов в главном цикле подвешивает окно.
    """
    import threading

    from gi.repository import GLib

    def target():
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001 — ошибку отдаём вызывающему
            result = exc
        GLib.idle_add(lambda: (done(result), False)[1])

    threading.Thread(target=target, daemon=True).start()
