"""Звуковой сигнал после автоисправления.

В Punto Switcher переключение раскладки слышно: короткий щелчок подтверждает,
что программа сработала, и человек не перепечатывает слово зря. Здесь то же
самое, но без лишних зависимостей: файл генерируется стандартной библиотекой,
а проигрывает его первый найденный плеер из системы.
"""

from __future__ import annotations

import math
import shutil
import struct
import subprocess
import threading
import wave
from pathlib import Path

# Частота и длительность короткого «пика»: слышно, но не раздражает.
FREQ_HZ = 880.0
DURATION_SEC = 0.06
SAMPLE_RATE = 22050
AMPLITUDE = 0.22  # доля полной шкалы: тихо, чтобы не пугать в наушниках

# Плееры в порядке предпочтения: PipeWire, PulseAudio, ALSA.
PLAYERS = (("pw-play",), ("paplay",), ("aplay", "-q"))

# Плеер получает запас времени на запуск поверх короткого звукового сигнала.
PLAY_TIMEOUT_SEC = 2.0

# Один сигнал за раз: очередь перекрывающихся зависших плееров не нужна.
_PLAYBACK = threading.Lock()


def wav_path(cache_dir: Path) -> Path:
    return cache_dir / "wayswitch-beep.wav"


def write_beep(path: Path) -> Path:
    """Сгенерировать короткий сигнал (16 бит, моно) с плавными краями.

    Плавное начало и конец нужны, чтобы не было щелчка на срезе — он звучит
    громче самого сигнала.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    total = int(SAMPLE_RATE * DURATION_SEC)
    fade = max(1, total // 8)
    frames = bytearray()
    for i in range(total):
        edge = min(1.0, i / fade, (total - 1 - i) / fade)
        value = math.sin(2 * math.pi * FREQ_HZ * i / SAMPLE_RATE) * AMPLITUDE * edge
        frames += struct.pack("<h", int(value * 32767))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(bytes(frames))
    return path


def player_command(which=shutil.which) -> list[str] | None:
    """Первая доступная команда воспроизведения (или None)."""
    for cmd in PLAYERS:
        if which(cmd[0]):
            return list(cmd)
    return None


def play(path: Path, which=shutil.which, run=subprocess.run) -> bool:
    """Запустить плеер с ограниченным ожиданием.

    True — команда была запущена, в том числе если плеер завершился с ошибкой
    или был снят subprocess.run по таймауту. False — играть нечем, запуск не
    удался или уже звучит другой сигнал. Ожидание ограничено, но не мгновенно:
    вызывать не из потока ввода.
    """
    cmd = player_command(which)
    if cmd is None:
        return False
    if not _PLAYBACK.acquire(blocking=False):
        return False
    try:
        run([*cmd, str(path)], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=PLAY_TIMEOUT_SEC)
        return True
    except subprocess.TimeoutExpired:
        return True
    except OSError:
        return False
    finally:
        _PLAYBACK.release()


def ensure_and_play(cache_dir: Path, which=shutil.which, run=subprocess.run) -> bool:
    """Сгенерировать файл при первом обращении и проиграть."""
    path = wav_path(cache_dir)
    if not path.exists():
        write_beep(path)
    return play(path, which=which, run=run)
