"""Звуковой сигнал: корректный WAV и выбор плеера."""

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
    assert sound.play(tmp_path / "x.wav", which=lambda _n: None) is False


def test_play_runs_player_with_path(tmp_path):
    calls = []

    class FakeProc:
        pass

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return FakeProc()

    path = tmp_path / "beep.wav"
    assert sound.play(path, which=lambda n: "/usr/bin/" + n, run=fake_run) is True
    # В команду идёт имя из PATH, а не найденный абсолютный путь: так плеер
    # подхватит обновление пакета без перезапуска демона.
    assert calls[0][0][0] == "pw-play"
    assert calls[0][0][-1] == str(path)


def test_ensure_and_play_creates_file_once(tmp_path):
    played = []

    def fake_run(cmd, **kwargs):
        played.append(cmd[-1])
        return object()

    for _ in range(2):
        assert sound.ensure_and_play(tmp_path, which=lambda n: "/usr/bin/" + n, run=fake_run)
    assert len(played) == 2
    assert (tmp_path / "wayswitch-beep.wav").exists()


def test_sound_is_off_by_default():
    assert Config().general.sound_on_fix is False
