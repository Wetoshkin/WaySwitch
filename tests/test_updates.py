"""Проверка новых версий: разбор версий, выбор релиза, суточный кэш."""

from itertools import permutations

import pytest

from wayswitch import updates


def test_parse_and_compare_versions():
    assert updates.is_newer("2.0.0", "2.0.0a1")
    assert updates.is_newer("v2.0.0a2", "2.0.0a1")
    assert updates.is_newer("2.0.0rc1", "2.0.0b3")
    assert updates.is_newer("2.1", "2.0.9")
    assert not updates.is_newer("2.0.0a1", "2.0.0")
    assert not updates.is_newer("мусор", "2.0.0")
    assert updates.is_prerelease("2.0.0a1") and not updates.is_prerelease("2.0.0")
    assert updates.parse_version("v2.0.0a4") == (2, 0, 0, 0, 4)


@pytest.mark.parametrize("legacy", ["2.0.0a1", "2.0.0a2", "2.0.0a3", "2.0.0a4"])
def test_renumbered_stable_release_follows_published_legacy_alphas(legacy):
    assert updates.is_newer("1.0.0", legacy)
    assert not updates.is_newer(legacy, "1.0.0")


@pytest.mark.parametrize("candidate,current,expected", [
    ("2.0.0a5", "1.0.0", True),
    ("2.0.0", "1.0.0", True),
    ("1.0.0", "2.0.0a5", False),
    ("2.0.0b1", "2.0.0a5", True),
    ("2.0.0a5", "2.0.0", False),
])
def test_future_versions_keep_normal_order(candidate, current, expected):
    assert updates.is_newer(candidate, current) is expected


TAG = "https://github.com/Wetoshkin/WaySwitch/releases/tag/"
RELEASES = [
    {"tag_name": "v2.1.0a1", "prerelease": True, "html_url": TAG + "v2.1.0a1"},
    {"tag_name": "v2.0.1", "prerelease": False, "html_url": TAG + "v2.0.1", "body": "заметки"},
    {"tag_name": "v2.0.0", "prerelease": False, "html_url": "https://evil.example/x"},
    {"tag_name": "v9.9.9", "draft": True},
]

MIGRATION_RELEASES = [
    {"tag_name": "v2.0.0a3", "prerelease": True, "html_url": TAG + "v2.0.0a3"},
    {"tag_name": "v2.0.0a4", "prerelease": True, "html_url": TAG + "v2.0.0a4"},
    {"tag_name": "v1.0.0", "prerelease": False, "html_url": TAG + "v1.0.0"},
]


@pytest.mark.parametrize("releases", list(permutations(MIGRATION_RELEASES)))
def test_legacy_install_gets_stable_release_in_any_api_order(releases):
    release = updates.pick_release(list(releases), "2.0.0a2")
    assert release is not None and release.version == "1.0.0"


def test_legacy_install_can_still_update_to_later_legacy_alpha():
    release = updates.pick_release(MIGRATION_RELEASES[:2], "2.0.0a2")
    assert release is not None and release.version == "2.0.0a4"


def test_stable_install_is_not_offered_legacy_alpha():
    assert updates.pick_release(MIGRATION_RELEASES[:2], "1.0.0") is None


@pytest.mark.parametrize("current,cached,expected", [
    ("2.0.0a4", "1.0.0", "1.0.0"),
    ("1.0.0", "2.0.0a4", None),
])
def test_cached_release_respects_renumbering(tmp_path, current, cached, expected):
    path = tmp_path / "update.json"
    updates.save_cache({"checked_at": 100.0, "latest": {"version": cached}}, path)
    release = updates.check(current, now=101.0, path=path)
    assert (release.version if release else None) == expected


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


def test_update_request_sends_only_release_url_and_public_headers(monkeypatch):
    import io
    import urllib.request

    from wayswitch import __version__

    sent = []

    def receive(request, *, timeout):
        sent.append((request.full_url, request.get_method(), request.data,
                     dict(request.header_items()), timeout))
        return io.BytesIO(b"[]")

    monkeypatch.setattr(urllib.request, "urlopen", receive)
    assert updates.fetch_latest("2.0.0", timeout=1.0) is None
    assert sent == [(
        "https://api.github.com/repos/Wetoshkin/WaySwitch/releases?per_page=20",
        "GET", None, {"Accept": "application/vnd.github+json",
                      "User-agent": f"WaySwitch/{__version__}"}, 1.0,
    )]
