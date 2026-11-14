"""The synthetic keyboard cannot type outside the owned, verified fixture."""

import pytest

from tools import live_focus
from tools.live_focus import GuardedKeyboard, check_refusal, single_graphical_session


class Device:
    def __init__(self):
        self.events = []
        self.closed = False

    def write(self, kind, code, value):
        self.events.append((kind, code, value))

    def syn(self):
        pass

    def close(self):
        self.closed = True


def test_foreign_or_locked_focus_sends_zero_key_downs():
    device = Device()
    keyboard = GuardedKeyboard(device, lambda: False)
    with pytest.raises(RuntimeError, match="focus"):
        keyboard.press(34)
    keyboard.close()
    assert device.events == []
    assert device.closed


def test_failure_after_key_down_releases_only_owned_key():
    device = Device()
    def fail():
        if device.events:
            raise OSError("write failed")
    device.syn = fail
    keyboard = GuardedKeyboard(device, lambda: True)
    with pytest.raises(OSError):
        keyboard.press(34)
    keyboard.close()
    assert device.events == [(1, 34, 1), (1, 34, 0)]
    assert keyboard.held == set()


def test_refusal_is_latched_even_if_focus_returns():
    device = Device()
    focused = [False]
    keyboard = GuardedKeyboard(device, lambda: focused[0])
    with pytest.raises(RuntimeError):
        keyboard.press(34)
    focused[0] = True
    with pytest.raises(RuntimeError):
        keyboard.press(34)
    assert device.events == []


def test_negative_probe_never_uses_a_real_input_device():
    assert check_refusal(lambda: False)
    assert not check_refusal(lambda: True)


@pytest.mark.parametrize("extra,selected,uid", [
    ([], "other", 1000), ([], "one", 1001),
    ([{"Id": "two", "Type": "wayland"}], "one", 1000),
])
def test_live_requires_one_matching_local_graphical_session(extra, selected, uid):
    session = {"Id": "one", "Type": "wayland", "Seat": ("seat0", "/seat"),
               "User": (1000, "/user"), "Remote": False}
    assert single_graphical_session([session], "one", 1000)
    assert not single_graphical_session([session, *extra], selected, uid)


def test_initialization_failure_writes_failed_report(monkeypatch, tmp_path):
    def failed(_args):
        raise OSError("private-provider-diagnostic")
    monkeypatch.setattr(live_focus, "_run", failed, raising=False)
    output = tmp_path / "result.json"
    assert live_focus.main(["--session-id", "one", "--samples", "2", "--app-sha", "abc",
                            "--config", str(tmp_path / "config"), "--output", str(output)]) == 1
    import json
    report = json.loads(output.read_text())
    assert report["success"] is False
    assert "private-provider-diagnostic" not in output.read_text()


def test_readback_interval_is_explicit_and_bounded(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(live_focus, "_run", lambda args: seen.append(args.poll_ms) or 0)
    argv = ["--session-id", "one", "--app-sha", "abc", "--config", "config",
            "--output", str(tmp_path / "result.json")]
    assert live_focus.main(argv) == 0
    assert live_focus.main([*argv, "--poll-ms", "1"]) == 0
    assert seen == [20, 1]
    for invalid in ("0", "1001"):
        with pytest.raises(SystemExit):
            live_focus.main([*argv, "--poll-ms", invalid])
