import pytest

from wayswitch import doctor
from wayswitch.doctor import Check, exit_code, format_report


def test_cosmic_backend_selected(monkeypatch):
    from wayswitch.backends import cosmic
    from wayswitch.backends.select import choose_backend, order_for

    monkeypatch.setattr(cosmic, "CosmicBackend", lambda: "native")
    assert order_for("cosmic", "auto") == ["cosmic"]
    assert choose_backend("auto", None, 0, desktop="cosmic") == "native"


def test_umbriel_backend_selected_without_hotkey_fallback(monkeypatch):
    from wayswitch.backends import umbriel
    from wayswitch.backends.base import BackendError
    from wayswitch.backends.select import choose_backend, order_for

    monkeypatch.setattr(umbriel, "UmbrielBackend", lambda: "native")
    assert order_for("umbriel", "auto") == ["umbriel"]
    assert choose_backend("auto", None, 0, desktop="umbriel") == "native"

    def unavailable():
        raise BackendError("no IPC")

    monkeypatch.setattr(umbriel, "UmbrielBackend", unavailable)
    with pytest.raises(BackendError, match="no IPC"):
        choose_backend("auto", None, 0, desktop="umbriel")


@pytest.mark.parametrize("service_active", [False, True])
def test_umbriel_report_does_not_recommend_labwc(monkeypatch, service_active):
    import subprocess

    from wayswitch.backends import umbriel

    original_run = subprocess.run

    def run(command, *args, **kwargs):
        if command == ["systemctl", "--user", "is-active", "wayswitch"]:
            return subprocess.CompletedProcess(command, 0 if service_active else 3,
                                               "active\n" if service_active else "inactive\n")
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(doctor, "detect_desktop", lambda: "umbriel")
    monkeypatch.setattr(umbriel, "find_socket", lambda: None)
    checks = doctor.run_checks()
    support = next(c for c in checks if c.key == "switch")
    assert not support.ok and support.required
    assert "UMBRIEL_SOCKET" in support.hint and "Umbriel" in support.hint
    assert "labwc" not in format_report(checks)
    assert not any(c.key == "layouts" for c in checks)
    service = next(c for c in checks if c.key == "service")
    assert service.ok is service_active
    assert service.hint == "active." if service_active else "автозапуск Umbriel" in service.hint


def test_nixos_dependency_report_points_to_declarative_module(monkeypatch):
    import platform
    import sys

    monkeypatch.setattr(platform, "freedesktop_os_release", lambda: {"ID": "nixos"})
    monkeypatch.setattr(doctor, "detect_desktop", lambda: "other")
    monkeypatch.setitem(sys.modules, "evdev", None)
    checks = doctor.run_checks()
    report = format_report(checks)
    assert "nixos-rebuild switch" in report and "docs/nixos.md" in report
    assert "apt install" not in report and "dnf install" not in report
    assert "pacman -S" not in report


@pytest.mark.parametrize("path", [None, "/native-socket"])
def test_umbriel_focus_check_blocks_before_reading_stale_background(path, monkeypatch):
    from wayswitch import a11y
    from wayswitch.backends import umbriel

    def background():
        raise AssertionError("must not query background AT-SPI while native guard is locked")

    monkeypatch.setattr(doctor, "detect_desktop", lambda: "umbriel")
    monkeypatch.setattr(umbriel, "find_socket", lambda: path)
    monkeypatch.setattr(umbriel, "query_input_locked", lambda _ipc: True)
    monkeypatch.setattr(a11y, "query_focus", background)
    ok, detail = doctor.focus_status()
    assert not ok and "Umbriel" in detail and "ввод заблокирован" in detail


def test_umbriel_active_window_does_not_replace_verified_atspi_field(monkeypatch):
    from wayswitch import a11y
    from wayswitch.backends import umbriel

    monkeypatch.setattr(doctor, "detect_desktop", lambda: "umbriel")
    monkeypatch.setattr(umbriel, "find_socket", lambda: "/native-socket")
    monkeypatch.setattr(umbriel, "query_input_locked", lambda _ipc: False)
    monkeypatch.setattr(a11y, "query_focus", lambda: a11y.FocusSnapshot())
    ok, detail = doctor.focus_status()
    assert not ok and "автоисправление ждёт фокуса" in detail


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
    assert "ручное исправление доступно" not in report
    assert exit_code(checks) == 0


def test_kde_focus_diagnostics_do_not_recommend_gnome_extension(monkeypatch):
    monkeypatch.setattr(doctor, "detect_desktop", lambda: "kde")
    monkeypatch.setattr(doctor, "focus_status", lambda: (False, "фокус недоступен"))
    checks = doctor.run_checks()
    focus = next(c for c in checks if c.key == "focus")
    assert not focus.ok and not focus.required
    assert "wayswitch doctor --delay 5" in focus.hint
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

    monkeypatch.setattr(doctor, "detect_desktop", lambda: "gnome")
    monkeypatch.setattr(a11y, "query_focus", lambda: a11y.FocusSnapshot(
        "IntelliJ IDEA", False, 100.0, ("jetbrains-idea",)))
    ok, detail = doctor.focus_status()
    assert ok and "jetbrains-idea" in detail and "IntelliJ IDEA" in detail


def test_focus_status_explains_missing_atspi_application(monkeypatch):
    from wayswitch import a11y

    monkeypatch.setattr(doctor, "detect_desktop", lambda: "gnome")
    monkeypatch.setattr(a11y, "query_focus", lambda: a11y.FocusSnapshot(
        None, False, 100.0, ("jetbrains-idea",)))
    ok, detail = doctor.focus_status()
    assert not ok and "jetbrains-idea" in detail and "парол" in detail


def test_focus_report_identifies_protected_input_without_window_title(monkeypatch):
    from wayswitch import a11y

    monkeypatch.setattr(doctor, "detect_desktop", lambda: "gnome")
    monkeypatch.setattr(a11y, "query_focus", lambda: a11y.FocusSnapshot(
        "Terminal", True, window="PRIVATE TITLE"))
    ok, detail = doctor.focus_status()
    assert not ok and "защищённый ввод" in detail and "PRIVATE TITLE" not in detail


@pytest.mark.parametrize("flags, expected", [
    ({}, "Автоисправление включено"),
    ({"auto_correct": False, "active": False}, "Автоисправление выключено в настройках"),
    ({"paused": True, "active": False}, "на паузе"),
    ({"locked": True, "active": False}, "сеанс заблокирован"),
    ({"lock_confirmation_required": True}, "возобновление"),
    ({"dry_run": True}, "режим наблюдения"),
    ({"layout_supported": False, "active": False}, "раскладка не поддерживается"),
    ({"active": False}, "бэкенд не поддерживает"),
])
def test_report_uses_running_daemon_settings(monkeypatch, flags, expected):
    from wayswitch import cli

    status = dict(backend="umbriel", layout="latin", layout_supported=True, active=True,
                  auto_correct=True, paused=False, locked=False, dry_run=False,
                  lock_confirmation_required=False, private="PRIVATE")

    def call(method):
        assert method == "GetStatus"
        return (status | flags,)

    monkeypatch.setattr(cli, "call_daemon", call)
    ok, report = doctor.daemon_status()
    assert ok is (not flags)
    assert expected in report and "umbriel" in report and "латинская" in report
    assert "PRIVATE" not in report


@pytest.mark.parametrize("reply", [None, ({},), ({"paused": False},)])
def test_unavailable_or_old_daemon_does_not_assume_defaults(monkeypatch, reply):
    from wayswitch import cli

    monkeypatch.setattr(cli, "call_daemon", lambda method: reply)
    ok, report = doctor.daemon_status()
    assert not ok
    assert "Автоисправление включено" not in report
    assert "состояние автоисправления неизвестно" in report


def test_report_when_session_bus_is_unavailable(monkeypatch):
    from wayswitch import cli

    def unavailable(method):
        raise OSError("PRIVATE")

    monkeypatch.setattr(cli, "call_daemon", unavailable)
    checks = doctor.run_checks()
    daemon = next(c for c in checks if c.key == "daemon")
    assert not daemon.ok and exit_code([daemon]) == 0
    report = format_report(checks)
    assert "Демон недоступен по D-Bus" in report and "PRIVATE" not in report


def test_uinput_status_ok(monkeypatch):
    monkeypatch.setattr(doctor.os, "open", lambda *a, **kw: 7)
    monkeypatch.setattr(doctor.os, "close", lambda fd: None)
    assert doctor.uinput_status() == (True, "")


def test_uinput_status_permission(monkeypatch):
    def denied(*_a, **_kw):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(doctor.os, "open", denied)
    ok, detail = doctor.uinput_status()
    assert not ok and "прав" in detail and "RDP" in detail and "локальн" in detail


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
    assert st(installed=True, enabled=True, running=True, version=3) == "outdated"
    assert st(installed=True, enabled=True, running=True, version=4) == "ok"


def test_old_extension_does_not_block_onboarding(ru_locale):
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
