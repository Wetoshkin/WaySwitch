from wayswitch.doctor import Check, exit_code, format_report


def test_report_and_exit_code():
    checks = [Check("Сессия Wayland", True), Check("uinput", False, "правило udev", True),
              Check("Расширение", False, "необязательно", False)]
    text = format_report(checks)
    assert "✓ Сессия Wayland" in text and "✗ uinput" in text and "правило udev" in text
    assert exit_code(checks) == 1
    assert exit_code([Check("x", True), Check("y", False, required=False)]) == 0
