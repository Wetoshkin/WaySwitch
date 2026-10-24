"""Проверка новых версий на GitHub Releases.

Раз в сутки (не чаще) GUI спрашивает у api.github.com последний релиз и, если
он новее установленной версии, показывает полосу «Вышла версия …» в главном
окне и пункт в меню значка. Больше никуда ничего не уходит: запрос анонимный,
без идентификаторов, отключается в «Параметрах» (general.check_updates).

Модуль чистый: сеть трогает только fetch_latest(), всё остальное — разбор
версий и кэш, которые проверяются тестами без сети.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

REPO = "Wetoshkin/WaySwitch"
API_URL = f"https://api.github.com/repos/{REPO}/releases"
RELEASES_URL = f"https://github.com/{REPO}/releases"
# Не чаще раза в сутки: GitHub ограничивает анонимные запросы (60 в час с
# адреса), а новые версии выходят не каждый день.
CHECK_INTERVAL_SEC = 24 * 3600

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?(?:[.-]?(a|b|rc)\.?(\d+))?$", re.I)
# Предрелиз меньше релиза той же версии: 2.0.0a1 < 2.0.0b1 < 2.0.0rc1 < 2.0.0.
_PRE_RANK = {"a": 0, "b": 1, "rc": 2, None: 3}


def parse_version(text: str) -> tuple[int, int, int, int, int] | None:
    """«v2.0.0a1» → кортеж для сравнения; непонятная строка → None."""
    m = _VERSION_RE.match(text.strip())
    if not m:
        return None
    major, minor, patch, pre, pre_n = m.groups()
    return (int(major), int(minor), int(patch or 0),
            _PRE_RANK[pre.lower() if pre else None], int(pre_n or 0))


def is_prerelease(text: str) -> bool:
    parsed = parse_version(text)
    return parsed is not None and parsed[3] < _PRE_RANK[None]


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a > b


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    notes: str = ""


def pick_release(releases: list[dict], current: str) -> Release | None:
    """Выбрать из ответа API релиз, на который стоит обновиться.

    Черновики пропускаем всегда. Предрелизы предлагаем, только если сейчас
    стоит предрелиз: человек на 2.0.0a1 хочет знать про 2.0.0a2, а человек на
    стабильной 2.0.0 — нет.
    """
    allow_pre = is_prerelease(current)
    best: tuple[tuple, Release] | None = None
    for item in releases:
        if item.get("draft"):
            continue
        tag = str(item.get("tag_name") or "")
        parsed = parse_version(tag)
        if parsed is None:
            continue
        if (item.get("prerelease") or is_prerelease(tag)) and not allow_pre:
            continue
        if not is_newer(tag, current):
            continue
        url = str(item.get("html_url") or "")
        if not url.startswith(f"https://github.com/{REPO}/"):
            url = RELEASES_URL  # ссылку открываем в браузере — только свою
        release = Release(tag.lstrip("vV"), url, str(item.get("body") or "")[:2000])
        if best is None or parsed > best[0]:
            best = (parsed, release)
    return best[1] if best else None


def cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "wayswitch" / "update.json"


def load_cache(path: Path | None = None) -> dict:
    path = path or cache_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_cache(data: dict, path: Path | None = None) -> None:
    path = path or cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # кэш — оптимизация, без него просто спросим ещё раз


def due(cache: dict, now: float, interval: float = CHECK_INTERVAL_SEC) -> bool:
    """Пора ли спрашивать GitHub снова."""
    checked = cache.get("checked_at")
    return not isinstance(checked, (int, float)) or now - checked >= interval \
        or now < checked  # часы ушли назад — не верим кэшу


class UpdateError(OSError):
    """GitHub не ответил или ответил не тем."""


def fetch_latest(current: str, timeout: float = 5.0) -> Release | None:
    """Спросить GitHub (сеть!). None — новее нет; сбой — UpdateError."""
    import urllib.request

    from wayswitch import __version__

    req = urllib.request.Request(
        f"{API_URL}?per_page=20",
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": f"WaySwitch/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — https
            data = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateError(str(exc)) from exc
    if not isinstance(data, list):
        raise UpdateError("неожиданный ответ GitHub")
    return pick_release(data, current)


def check(current: str, *, force: bool = False, now: float | None = None,
          fetch=fetch_latest, path: Path | None = None) -> Release | None:
    """Новая версия с учётом суточного кэша. Блокирует — звать из потока."""
    now = time.time() if now is None else now
    cache = load_cache(path)
    if not force and not due(cache, now):
        cached = cache.get("latest")
        if isinstance(cached, dict) and is_newer(str(cached.get("version", "")), current):
            return Release(str(cached["version"]), str(cached.get("url") or RELEASES_URL),
                           str(cached.get("notes") or ""))
        return None
    try:
        release = fetch(current)
    except UpdateError:
        # Сети нет — не запоминаем «проверено»: спросим при следующем запуске.
        # Явную проверку (кнопка «Проверить») об ошибке надо известить.
        if force:
            raise
        # Уже известную новую версию не забываем из-за пропавшей сети.
        cached = cache.get("latest")
        if isinstance(cached, dict) and is_newer(str(cached.get("version", "")), current):
            return Release(str(cached["version"]), str(cached.get("url") or RELEASES_URL),
                           str(cached.get("notes") or ""))
        return None
    entry = {"checked_at": now}
    if "notified" in cache:
        entry["notified"] = cache["notified"]
    if release is not None:
        entry["latest"] = {"version": release.version, "url": release.url,
                           "notes": release.notes}
    save_cache(entry, path)
    return release


def should_notify(version: str, path: Path | None = None) -> bool:
    """Показать системное уведомление о версии только один раз."""
    data = load_cache(path)
    if data.get("notified") == version:
        return False
    data["notified"] = version
    save_cache(data, path)
    return True
