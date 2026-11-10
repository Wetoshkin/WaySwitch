"""Звуковой сигнал: корректный WAV, выбор плеера и владение процессом."""

import subprocess
import sys
import threading
import wave

import pytest

from wayswitch import sound
from wayswitch.config import Config


def test_write_beep_makes_valid_wav(tmp_path):
    path = sound.write_beep(tmp_path / "beep.wav")
    with wave.open(str(path), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == sound.SAMPLE_RATE
        assert wav.getnframes() == int(sound.SAMPLE_RATE * sound.DURATION_SEC)


def test_beep_is_quiet_and_starts_silent(tmp_path):
    """Края плавные: первый и последний сэмплы почти нулевые, пик не в упор."""
    import struct

    path = sound.write_beep(tmp_path / "beep.wav")
    with wave.open(str(path), "rb") as wav:
        raw = wav.readframes(wav.getnframes())
    samples = struct.unpack(f"<{len(raw) // 2}h", raw)
    assert abs(samples[0]) < 200
    assert abs(samples[-1]) < 200
    assert max(abs(s) for s in samples) < 32767 * 0.3


@pytest.mark.parametrize("available,expected", [
    ({"pw-play", "paplay", "aplay"}, ["pw-play"]),
    ({"paplay", "aplay"}, ["paplay"]),
    ({"aplay"}, ["aplay", "-q"]),
    (set(), None),
])
def test_player_prefers_pipewire_then_pulse_then_alsa(available, expected):
    which = lambda name: f"/usr/bin/{name}" if name in available else None  # noqa: E731
    assert sound.player_command(which=which) == expected


def test_play_without_player_returns_false(tmp_path):
    calls = []
    assert sound.play(tmp_path / "x.wav", which=lambda _n: None,
                      run=lambda *a, **k: calls.append(1)) is False
    assert calls == []


def test_play_runs_player_with_path_and_supports_simple_run_result(tmp_path):
    calls = []
    marker = object()

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return marker

    path = tmp_path / "beep.wav"
    assert sound.play(path, which=lambda n: "/usr/bin/" + n, run=fake_run) is True
    # В команду идёт имя из PATH, а не найденный абсолютный путь: так плеер
    # подхватит обновление пакета без перезапуска демона.
    assert calls[0][0][0] == "pw-play"
    assert calls[0][0][-1] == str(path)
    # Ожидание ограничено: зависший плеер снимет и дождётся subprocess.run.
    assert calls[0][1]["timeout"] == sound.PLAY_TIMEOUT_SEC


def test_play_timeout_returns_true_and_releases_lock(tmp_path):
    calls = []

    def timed_out_run(cmd, **kwargs):
        calls.append(kwargs)
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    assert sound.play(tmp_path / "beep.wav", which=lambda n: "/usr/bin/" + n,
                      run=timed_out_run) is True
    assert calls[0]["timeout"] == sound.PLAY_TIMEOUT_SEC
    # Таймаут не должен оставить замок занятым навсегда.
    assert sound.play(tmp_path / "beep.wav", which=lambda n: "/usr/bin/" + n,
                      run=lambda *a, **k: object()) is True


def test_play_missing_executable_returns_false_and_releases_lock(tmp_path, monkeypatch):
    missing = str(tmp_path / "no-such-player")
    monkeypatch.setattr(sound, "PLAYERS", ((missing,),))
    assert sound.play(tmp_path / "beep.wav", which=lambda _n: missing) is False
    # Реальный subprocess.run получил FileNotFoundError; замок освобождён.
    called = []
    assert sound.play(tmp_path / "beep.wav", which=lambda _n: missing,
                      run=lambda *a, **k: called.append(1)) is True
    assert called == [1]


def test_play_true_when_player_exits_nonzero(tmp_path, monkeypatch):
    completed = []
    monkeypatch.setattr(
        sound, "player_command",
        lambda which: [sys.executable, "-c", "raise SystemExit(7)"],
    )

    def real_run(cmd, **kwargs):
        result = subprocess.run(cmd, **kwargs)
        completed.append(result)
        return result

    assert sound.play(tmp_path / "beep.wav", which=lambda _n: "found",
                      run=real_run) is True
    assert completed[0].returncode == 7


def test_default_run_kills_and_reaps_real_hung_child(tmp_path, monkeypatch):
    captured = []
    real_popen = subprocess.Popen

    class CapturingPopen(real_popen):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.kill_called = False
            captured.append(self)

        def kill(self):
            self.kill_called = True
            super().kill()

    # subprocess.run (production default) смотрит Popen в своём модуле.
    monkeypatch.setattr(subprocess, "Popen", CapturingPopen)
    monkeypatch.setattr(
        sound, "player_command",
        lambda which: [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    monkeypatch.setattr(sound, "PLAY_TIMEOUT_SEC", 0.2)

    assert sound.play(tmp_path / "beep.wav", which=lambda _n: "found") is True
    assert len(captured) == 1
    # Через 0.2 с ребёнок ещё спал бы; returncode выставлен только после
    # kill+wait внутри subprocess.run, то есть процесс снят и забран.
    assert captured[0].kill_called
    assert captured[0].returncode is not None


def test_play_skips_while_another_player_is_still_running(tmp_path):
    """Всплеск сигналов не плодит зависшие плееры: играет только один."""
    started = threading.Event()
    release = threading.Event()
    calls = []
    first = []

    def slow_run(cmd, **_kwargs):
        calls.append(cmd)
        started.set()
        release.wait(timeout=2)
        return object()

    def run_first():
        first.append(sound.play(tmp_path / "beep.wav", which=lambda n: "/usr/bin/" + n,
                                run=slow_run))

    thread = threading.Thread(target=run_first)
    thread.start()
    try:
        assert started.wait(timeout=2)
        # Пока первый subprocess.run не вернулся, новые сигналы не запускаются.
        for _ in range(3):
            assert sound.play(tmp_path / "beep.wav", which=lambda n: "/usr/bin/" + n,
                              run=slow_run) is False
        assert len(calls) == 1
    finally:
        release.set()
        thread.join(timeout=2)
    assert not thread.is_alive()
    assert first == [True]


def test_ensure_and_play_creates_file_once(tmp_path):
    played = []

    def fake_run(cmd, **kwargs):
        played.append((cmd[-1], kwargs["timeout"]))
        return object()

    for _ in range(2):
        assert sound.ensure_and_play(tmp_path, which=lambda n: "/usr/bin/" + n, run=fake_run)
    assert len(played) == 2
    assert all(timeout == sound.PLAY_TIMEOUT_SEC for _, timeout in played)
    assert (tmp_path / "wayswitch-beep.wav").exists()


def test_sound_is_off_by_default():
    assert Config().general.sound_on_fix is False
