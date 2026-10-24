"""Бэкенд KDE Plasma: разбор данных KWin и поведение без настоящей шины."""

import pytest

from wayswitch.backends import kde_plasma as kde_mod
from wayswitch.backends.base import BackendError
from wayswitch.backends.kde_plasma import KdePlasmaBackend, parse_layouts_list, read_kxkbrc_options
from wayswitch.backends.select import order_for


def test_parse_layouts_list_keeps_order():
    specs = parse_layouts_list([("us", "English (US)", "English (US)"),
                                ("ru", "Russian", "Russian")])
    assert [s.xkb_id for s in specs] == ["us", "ru"]


def test_parse_layouts_list_keeps_variant():
    specs = parse_layouts_list([("us", "", ""), ("ru+phonetic", "", "")])
    assert specs[1].xkb_id == "ru+phonetic"


def test_read_kxkbrc_options(tmp_path):
    cfg = tmp_path / "kxkbrc"
    cfg.write_text("[Layout]\nLayoutList=us,ru\nOptions=grp:alt_shift_toggle,compose:ralt\n",
                   encoding="utf-8")
    assert read_kxkbrc_options(cfg) == ["grp:alt_shift_toggle", "compose:ralt"]


def test_read_kxkbrc_options_missing_file(tmp_path):
    assert read_kxkbrc_options(tmp_path / "nope") == []


def test_read_kxkbrc_options_ignores_other_sections(tmp_path):
    cfg = tmp_path / "kxkbrc"
    cfg.write_text("[Other]\nOptions=nope\n[Layout]\nUse=true\n", encoding="utf-8")
    assert read_kxkbrc_options(cfg) == []


class FakeKwin:
    """Подмена D-Bus KWin: методы отдают то же, что настоящий org.kde.keyboard."""

    def __init__(self, layouts=("us", "ru"), current=0, refuse=False):
        self.rows = [(name, name.upper(), name) for name in layouts]
        self.current = current
        self.refuse = refuse
        self.calls = []

    def __call__(self, method, arg, _sig):
        self.calls.append(method)
        if method == "getLayoutsList":
            return (self.rows,)
        if method == "getLayout":
            return (self.current,)
        if method == "setLayout":
            if self.refuse:
                return (False,)
            self.current = arg
            return (True,)
        raise AssertionError(f"неожиданный метод {method}")


@pytest.fixture
def no_variant(monkeypatch):
    """Без gi: в тестах аргумент setLayout — просто индекс."""
    monkeypatch.setattr(kde_mod, "_variant_u", lambda index: index)


def make_backend(fake, signals, settle=0):
    return KdePlasmaBackend(settle_ms=settle, caller=fake,
                            subscriber=lambda cb: (signals.append(cb), lambda: None)[1],
                            options_reader=lambda: ["grp:alt_shift_toggle"])


def test_backend_reads_layouts_and_current(no_variant):
    fake, signals = FakeKwin(current=1), []
    backend = make_backend(fake, signals)
    assert [s.xkb_id for s in backend.layouts()] == ["us", "ru"]
    assert backend.current() == 1
    assert backend.xkb_options() == ["grp:alt_shift_toggle"]


def test_backend_switch_and_wait(no_variant):
    fake, signals = FakeKwin(current=0), []
    backend = make_backend(fake, signals)
    backend.set(1)
    assert fake.current == 1
    assert backend.wait_applied(1, 1.0) is True


def test_backend_wait_detects_failed_switch(no_variant):
    fake, signals = FakeKwin(current=0), []
    backend = make_backend(fake, signals)
    assert backend.wait_applied(1, 1.0) is False


def test_backend_refuses_when_one_layout(no_variant):
    with pytest.raises(BackendError):
        make_backend(FakeKwin(layouts=("us",)), [])


def test_backend_set_reports_kwin_refusal(no_variant):
    fake, signals = FakeKwin(refuse=True), []
    backend = make_backend(fake, signals)
    with pytest.raises(BackendError):
        backend.set(1)


def test_backend_signal_marks_external_change(no_variant):
    fake, signals = FakeKwin(current=0), []
    backend = make_backend(fake, signals)
    seen = []
    backend.on_change(lambda index, external: seen.append((index, external)))

    signals[0](1)      # пользователь переключил руками
    assert seen == [(1, True)]
    assert backend.current() == 1

    backend.set(0)
    signals[0](0)      # это уже наше переключение
    assert seen[-1] == (0, False)


def test_auto_order_depends_on_desktop():
    assert order_for("gnome", "auto") == ["shell", "ibus", "hotkey"]
    assert order_for("kde", "auto") == ["kde"]
    assert order_for("other", "auto") == ["compositor", "hotkey"]
    assert order_for("sway", "auto") == ["sway"]
    assert order_for("hyprland", "auto") == ["hyprland"]
    assert order_for("xfce", "auto") == ["compositor", "hotkey"]


def test_explicit_backend_wins_over_desktop():
    assert order_for("kde", "shell") == ["shell"]
    assert order_for("gnome", "hotkey") == ["hotkey"]


def test_kde_variants_come_from_kxkbrc(tmp_path):
    """KWin отдаёт «ru» без варианта — вариант берём из VariantList."""
    from wayswitch.backends.kde_plasma import parse_layouts_list, read_kxkbrc_variants
    from wayswitch.keymap import LayoutSpec

    rc = tmp_path / "kxkbrc"
    rc.write_text("[Layout]\nLayoutList=us,ru\nVariantList=,phonetic\n", encoding="utf-8")
    variants = read_kxkbrc_variants(rc)
    rows = [("us", "", "English (US)"), ("ru", "", "Russian")]
    assert parse_layouts_list(rows, variants) == [LayoutSpec("us"), LayoutSpec("ru", "phonetic")]
    # Число не совпало (kxkbrc устарел) — не гадаем, берём как отдал KWin.
    assert parse_layouts_list(rows, ["", "", "x"]) == [LayoutSpec("us"), LayoutSpec("ru")]
