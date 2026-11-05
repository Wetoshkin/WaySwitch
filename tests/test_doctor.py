from wayswitch import doctor
from wayswitch.doctor import Check, exit_code, format_report


def test_cosmic_backend_selected(monkeypatch):
    from wayswitch.backends import cosmic
    from wayswitch.backends.select import choose_backend, order_for

    monkeypatch.setattr(cosmic, "CosmicBackend", lambda: "native")
    assert order_for("cosmic", "auto") == ["cosmic"]
    assert choose_backend("auto", None, 0, desktop="cosmic") == "native"


def test_report_and_exit_code():
    checks = [Check("Сессия Wayland", True), Check("uinput", False, "правило udev", True),
              Check("Расширение", False, "необязательно", False)]
    text = format_report(checks)
    assert "✓ Сессия Wayland" in text and "✗ uinput" in text and "правило udev" in text
    assert exit_code(checks) == 1
    assert exit_code([Check("x", True), Check("y", False, required=False)]) == 0


def test_report_does_not_claim_ready_without_focus():
    checks = [Check("Сессия Wayland", True, key="wayland"),
              Check("Служба AT-SPI", True, required=False),
              Check("Активное приложение", False, required=False, key="focus")]
    report = format_report(checks)
    assert "○ Активное приложение" in report
    assert "Всё готово" not in report
    assert "автоисправление не подтверждено" in report
    assert exit_code(checks) == 0


def test_kde_focus_diagnostics_do_not_recommend_gnome_extension(monkeypatch):
    monkeypatch.setattr(doctor, "detect_desktop", lambda: "kde")
    monkeypatch.setattr(doctor, "focus_status", lambda: (False, "фокус недоступен"))
    checks = doctor.run_checks()
    focus = next(c for c in checks if c.key == "focus")
    assert not focus.ok and not focus.required
    assert "sleep 5; wayswitch doctor" in focus.hint
    assert "GNOME" not in focus.hint and "расширения" not in focus.hint
    setting = next(c for c in checks if "настройка" in c.name)
    assert "GTK" in setting.name and "GNOME" in setting.name


def test_cosmic_report_does_not_recommend_labwc(monkeypatch):
    from wayswitch.backends import cosmic
    from wayswitch.backends.base import BackendError

    def unavailable(**_kwargs):
        raise BackendError("нет сеанса")

    monkeypatch.setattr(cosmic, "CosmicBackend", unavailable)
    monkeypatch.setattr(doctor, "detect_desktop", lambda: "cosmic")
    checks = doctor.run_checks()
    support = next(c for c in checks if c.key == "switch")
    assert not support.ok and support.required
    assert "COSMIC" in support.hint and "zcosmic_keyboard_layout_manager_v1" in support.hint
    assert "labwc" not in format_report(checks)
    assert "ровно две" not in format_report(checks)


def test_focus_status_reports_only_application_identifiers(monkeypatch):
    from wayswitch import a11y

    monkeypatch.setattr(a11y, "query_focus", lambda: a11y.FocusSnapshot(
        "IntelliJ IDEA", False, 100.0, ("jetbrains-idea",)))
    ok, detail = doctor.focus_status()
    assert ok and "jetbrains-idea" in detail and "IntelliJ IDEA" in detail


def test_focus_status_explains_missing_atspi_application(monkeypatch):
    from wayswitch import a11y

    monkeypatch.setattr(a11y, "query_focus", lambda: a11y.FocusSnapshot(
        None, False, 100.0, ("jetbrains-idea",)))
    ok, detail = doctor.focus_status()
    assert not ok and "jetbrains-idea" in detail and "парол" in detail


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
    assert st(installed=True, enabled=True, running=True, version=2) == "outdated"
    assert st(installed=True, enabled=True, running=True, version=3) == "ok"


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
