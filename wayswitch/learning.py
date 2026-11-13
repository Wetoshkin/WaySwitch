"""Локальный персональный словарь; запись не затрагивает частоты языковой модели."""

from __future__ import annotations

import hashlib
import os
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from wayswitch.config import config_dir
from wayswitch.keymap import CYRILLIC, LATIN

MAX_WORDS = 5000
MAX_BYTES = 1024 * 1024
ALPHABETS = {"ru": CYRILLIC, "en": LATIN}
COUNT_LIMIT = 256
COUNT_TTL = 7 * 24 * 3600


@dataclass
class ManualLearningEvent:
    source: str
    target: str
    scope: tuple
    end: int
    time: float
    key: tuple[str, str]
    count_before: int | None
    digest: bytes | None = None


def normalize_word(lang: str, word: str) -> str:
    word = unicodedata.normalize("NFC", word).lower()
    letters = ALPHABETS.get(lang, set())
    if (not 1 <= len(word) <= 64 or word[0] not in letters or word[-1] not in letters
            or any(c not in letters and c not in "-'" for c in word)):
        raise ValueError("Invalid personal word")
    return word


class LearnedWords:
    def __init__(self, path: Path | None = None):
        self.path = path or config_dir() / "learned_words.txt"
        self.words: frozenset[tuple[str, str]] = frozenset()
        self.digest = hashlib.sha256(b"").digest()
        self.error: str | None = None
        self._stamp = None
        self.reload(force=True)

    def _stat(self):
        try:
            s = self.path.stat()
        except FileNotFoundError:
            return None
        return s.st_mtime_ns, s.st_size, s.st_ino, s.st_dev

    def _read(self):
        before = self._stat()
        if before is None:
            return b"", None
        if before[1] > MAX_BYTES:
            raise ValueError("Personal dictionary is too large")
        with self.path.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES or self._stat() != before:
            raise ValueError("Personal dictionary changed while reading")
        return data, before

    @staticmethod
    def _parse(data):
        words, malformed = set(), False
        for line in data.decode("utf-8").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            try:
                lang, word = line.split("\t")
                words.add((lang, normalize_word(lang, word)))
            except ValueError:
                malformed = True
        if len(words) > MAX_WORDS:
            raise ValueError("Personal dictionary is full")
        return frozenset(words), malformed

    def reload(self, *, force=False) -> bool:
        try:
            if not force and self._stat() == self._stamp:
                return False
            data, stamp = self._read()
            words, malformed = self._parse(data)
        except (OSError, ValueError) as exc:
            self.error = type(exc).__name__
            return False
        changed = words != self.words
        self.words, self._stamp = words, stamp
        self.digest = hashlib.sha256(data).digest()
        self.error = "InvalidLines" if malformed else None
        return changed

    @staticmethod
    def _atomic_write(path: Path, data: bytes):
        fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.chmod(name, 0o600)
            except OSError:
                pass
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def update(self, *, add=None, remove=None, expected=None, allowed=None) -> bool:
        add = (add[0], normalize_word(*add)) if add is not None else None
        remove = (remove[0], normalize_word(*remove)) if remove is not None else None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(".lock")
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            data, stamp = self._read()
            digest = hashlib.sha256(data).digest()
            if expected is not None and expected != digest:
                raise ValueError("Personal dictionary changed")
            words, malformed = self._parse(data)
            if malformed:
                raise ValueError("Repair invalid personal dictionary lines first")
            updated = set(words)
            if remove is not None:
                updated.discard(remove)
            if add is not None:
                updated.add(add)
            if updated == words:
                self.reload(force=True)
                return False
            if len(updated) > MAX_WORDS:
                raise ValueError("Personal dictionary is full")
            output = "".join(f"{lang}\t{word}\n" for lang, word in sorted(updated)).encode("utf-8")
            if stamp is not None:
                self._atomic_write(self.path.with_suffix(".bak"), data)
            # Внешний редактор не обязан брать наш lock: перед replace сверяем и содержимое.
            if self._read() != (data, stamp):
                raise ValueError("Personal dictionary changed before writing")
            if allowed is not None and not allowed():
                raise ValueError("Learning context changed")
            self._atomic_write(self.path, output)
            self.words = frozenset(updated)
            self.digest = hashlib.sha256(output).digest()
            self._stamp = None
            self.reload(force=True)
            return True
        finally:
            os.close(fd)
            lock.unlink()
