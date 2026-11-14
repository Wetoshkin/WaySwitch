"""Opt-in stage timing must not retain function arguments or results."""

import json
from types import SimpleNamespace

import pytest

from tools.profile_focus import Timings, profile_bus_calls


def test_stage_records_duration_and_failure_without_private_values():
    clock = iter([10, 25, 30, 60]).__next__
    stats = Timings(clock=clock)
    target = SimpleNamespace(call=lambda text: text.upper())
    stats.wrap(target, "call", "context")
    assert target.call("private-marker") == "PRIVATE-MARKER"
    target.call = lambda text: (_ for _ in ()).throw(ValueError(text))
    stats.wrap(target, "call", "context")
    with pytest.raises(ValueError):
        target.call("private-marker")
    assert stats.stages["context"] == {
        "calls": 2, "errors": 1, "durations_ns": [15, 30], "dropped": 0}
    assert "private-marker" not in json.dumps(stats.stages).lower()


def test_stage_storage_is_bounded_and_wrappers_can_be_restored():
    target = SimpleNamespace(call=lambda: 7)
    original = target.call
    stats = Timings(limit=2)
    stats.wrap(target, "call", "context")
    for _ in range(5):
        assert target.call() == 7
    assert stats.stages["context"]["calls"] == 5
    assert len(stats.stages["context"]["durations_ns"]) == 2
    assert stats.stages["context"]["dropped"] == 3
    stats.restore()
    assert target.call is original


def test_bus_profile_counts_actual_shell_sends_without_payload():
    class Connection:
        def call_sync(self, destination, path, interface, method, *args):
            return "private-result"
    stats = Timings()
    original = Connection.call_sync
    profile_bus_calls(stats, Connection)
    bus = Connection()
    bus.call_sync(":1.10", "/", "io.github.Wetoshkin.WaySwitch.Shell",
                  "GetFocusedContext", "private-input")
    bus.call_sync(":1.10", "/", "other", "GetText", "private-input")
    assert stats.stages["shell_ipc.GetFocusedContext"]["calls"] == 1
    assert len(stats.stages) == 1
    assert "private" not in json.dumps(stats.stages)
    stats.restore()
    assert Connection.call_sync is original
