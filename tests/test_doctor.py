from wayswitch import doctor
from wayswitch.doctor import Check, exit_code, format_report


def test_report_and_exit_code():
    checks = [Check("Сессия Wayland", True), Check("uinput", False, "правило udev", True),
              Check("Расширение", False, "необязательно", False)]
    text = format_report(checks)
    assert "✓ Сессия Wayland" in text and "✗ uinput" in text and "правило udev" in text
    assert exit_code(checks) == 1
    assert exit_code([Check("x", True), Check("y", False, required=False)]) == 0


def test_uinput_status_ok(monkeypatch):
    monkeypatch.setattr(doctor.os, "open", lambda *a, **kw: 7)
    monkeypatch.setattr(doctor.os, "close", lambda fd: None)
    assert doctor.uinput_status() == (True, "")


def test_uinput_status_permission(monkeypatch):
    def denied(*_a, **_kw):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(doctor.os, "open", denied)
    ok, detail = doctor.uinput_status()
    assert not ok and "прав" in detail


def test_uinput_status_module_missing(monkeypatch):
    # Права есть, а модуля uinput в ядре нет — именно этот случай os.access()
    # показывал как «доступен».
    def gone(*_a, **_kw):
        raise OSError(19, "No such device")

    monkeypatch.setattr(doctor.os, "open", gone)
    ok, detail = doctor.uinput_status()
    assert not ok and "uinput" in detail and "No such device" in detail


def test_extension_state_distinguishes_cases():
    # Issue #6: «не установлено», «выключено» и «включено, но старая версия»
    # раньше склеивались в «нужно включить».
    st = doctor.extension_state
    assert st(installed=False, enabled=False, running=False, version=None) == "missing"
    assert st(installed=True, enabled=False, running=False, version=None) == "disabled"
    assert st(installed=True, enabled=True, running=False, version=None) == "not_loaded"
    assert st(installed=True, enabled=True, running=True, version=1) == "outdated"
    assert st(installed=True, enabled=True, running=True, version=2) == "ok"


def test_old_extension_does_not_block_onboarding():
    from wayswitch.gui import texts

    ok, key, required, text = doctor.EXTENSION_STATES["outdated"]
    rows = texts.onboarding_rows([Check("Расширение GNOME Shell", ok, text, required, key)])
    assert rows[0].state == "optional" and rows[0].action == "logout"
    assert not texts.onboarding_blocking(rows)
    assert texts.onboarding_hint(rows).startswith("Можно продолжать")
    # Выключенное расширение — по-прежнему кнопка «Включить» и блокировка.
    ok, key, required, text = doctor.EXTENSION_STATES["disabled"]
    rows = texts.onboarding_rows([Check("Расширение GNOME Shell", ok, text, required, key)])
    assert rows[0].action == "extension" and texts.onboarding_blocking(rows)
    # Совет в doctor и на экране — про новый вход, а не про «включить».
    assert "войдите снова" in doctor.EXTENSION_STATES["outdated"][3]
