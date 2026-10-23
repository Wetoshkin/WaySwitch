"""Проверка новых версий: разбор версий, выбор релиза, суточный кэш."""

from wayswitch import updates


def test_parse_and_compare_versions():
    assert updates.is_newer("2.0.0", "2.0.0a1")
    assert updates.is_newer("v2.0.0a2", "2.0.0a1")
    assert updates.is_newer("2.0.0rc1", "2.0.0b3")
    assert updates.is_newer("2.1", "2.0.9")
    assert not updates.is_newer("2.0.0a1", "2.0.0")
    assert not updates.is_newer("мусор", "2.0.0")
    assert updates.is_prerelease("2.0.0a1") and not updates.is_prerelease("2.0.0")


TAG = "https://github.com/Wetoshkin/WaySwitch/releases/tag/"
RELEASES = [
    {"tag_name": "v2.1.0a1", "prerelease": True, "html_url": TAG + "v2.1.0a1"},
    {"tag_name": "v2.0.1", "prerelease": False, "html_url": TAG + "v2.0.1", "body": "заметки"},
    {"tag_name": "v2.0.0", "prerelease": False, "html_url": "https://evil.example/x"},
    {"tag_name": "v9.9.9", "draft": True},
]


def test_stable_user_is_not_offered_prerelease():
    release = updates.pick_release(RELEASES, "2.0.0")
    assert release.version == "2.0.1" and release.notes == "заметки"
    assert release.url.endswith("/releases/tag/v2.0.1")


def test_foreign_release_url_is_replaced():
    release = updates.pick_release([{"tag_name": "v3.0.0", "html_url": "https://evil.example/x"}],
                                   "2.0.0")
    assert release.url == updates.RELEASES_URL


def test_known_update_survives_offline_after_cache_expiry(tmp_path):
    path = tmp_path / "update.json"
    updates.check("2.0.0", now=0.0, fetch=lambda c: updates.Release("2.0.1", "u"), path=path)

    def offline(_c):
        raise updates.UpdateError("нет сети")

    got = updates.check("2.0.0", now=10 * 24 * 3600.0, fetch=offline, path=path)
    assert got is not None and got.version == "2.0.1"


def test_prerelease_user_gets_newest_including_prerelease():
    assert updates.pick_release(RELEASES, "2.0.0a2").version == "2.1.0a1"


def test_nothing_newer():
    assert updates.pick_release(RELEASES[1:], "2.0.1") is None


def test_check_caches_for_a_day(tmp_path):
    path = tmp_path / "update.json"
    calls = []

    def fetch(current):
        calls.append(current)
        return updates.Release("2.0.1", "u")

    assert updates.check("2.0.0", now=1000.0, fetch=fetch, path=path).version == "2.0.1"
    # Через час — из кэша, без сети.
    assert updates.check("2.0.0", now=1000.0 + 3600, fetch=fetch, path=path).version == "2.0.1"
    assert len(calls) == 1
    # Через сутки — снова спрашиваем.
    updates.check("2.0.0", now=1000.0 + 25 * 3600, fetch=fetch, path=path)
    assert len(calls) == 2
    # Уже обновились — кэш старую версию не предлагает.
    assert updates.check("2.0.1", now=1000.0 + 25 * 3600 + 1, fetch=fetch, path=path) is None


def test_network_error_is_not_cached(tmp_path):
    path = tmp_path / "update.json"

    def broken(_current):
        raise updates.UpdateError("нет сети")

    assert updates.check("2.0.0", now=1000.0, fetch=broken, path=path) is None
    assert not path.exists()


def test_notify_once_per_version(tmp_path):
    path = tmp_path / "update.json"
    assert updates.should_notify("2.0.1", path)
    assert not updates.should_notify("2.0.1", path)
    assert updates.should_notify("2.0.2", path)
