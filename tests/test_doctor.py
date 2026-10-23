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
