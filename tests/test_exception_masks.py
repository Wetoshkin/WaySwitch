"""Тесты масок исключений (#64)."""

import pytest

from tests.fakes import RU, US, FakeBackend, RecordingTypist, keys_for_text, make_keymap
from tests.test_focus import FakeProbe
from wayswitch import config as cfgmod
from wayswitch import explain
from wayswitch.config import (
    MAX_MASK_RULES,
    Config,
    ConfigError,
    ExceptionMask,
    ExceptionRules,
    add_exception_mask,
    load_exception_masks,
    remove_exception_mask,
    save_exception_masks,
    validate_exception_mask,
)
from wayswitch.controller import Controller
from wayswitch.detector import Detector, LanguageModel


def _models():
    return {
        "ru": LanguageModel.from_words(
            "ru",
            [("привет", 900.0), ("стакан", 800.0), ("бы", 700.0), ("кем", 700.0)],
            "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        ),
        "en": LanguageModel.from_words(
            "en",
            [("hello", 900.0), ("proxy", 800.0), ("server", 800.0), ("github", 800.0)],
            "abcdefghijklmnopqrstuvwxyz",
        ),
    }


def test_masks_exact_prefix_contains_and_case_sensitivity():
    rules = ExceptionRules([
        ExceptionMask("prefix", "proxy-"),
        ExceptionMask("contains", "стак"),
        ExceptionMask("exact", "GitHub", case_sensitive=True),
    ])
    assert rules.matches("proxy-server") is True
    assert rules.reason("PROXY-server") == "prefix"
    assert rules.matches("стакан") is True
    assert rules.reason("подстаканник") == "contains"
    assert rules.matches("GitHub") is True
    assert rules.matches("github") is False


def test_save_and_load_masks_roundtrip_without_touching_exact_exceptions(tmp_path):
    exc_path = tmp_path / "exceptions.txt"
    exc_path.write_text("ghbdtn\n", encoding="utf-8")
    mask_path = tmp_path / "exceptions_masks.txt"
    rules = add_exception_mask(ExceptionMask("prefix", "proxy-"), mask_path)
    rules = add_exception_mask(ExceptionMask("exact", "GitHub", True), mask_path)
    assert load_exception_masks(mask_path) == rules
    assert exc_path.read_text(encoding="utf-8") == "ghbdtn\n"

    after = remove_exception_mask(ExceptionMask("prefix", "proxy-"), mask_path)
    assert after.rules == (ExceptionMask("exact", "GitHub", True),)


@pytest.mark.parametrize("line", [
    "regex\tfoo*",
    "prefix\ta",
    "contains\tb",
    "prefix\tfoo*\tcase",
    "prefix\tfoo\twrong",
    "onlyonecol",
    "exact\tfoo\tcase\textra",
])
def test_invalid_mask_line_rejects_entire_file(tmp_path, line):
    path = tmp_path / "exceptions_masks.txt"
    path.write_text(f"prefix\tok-\n{line}\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_exception_masks(path)


def test_mask_limit_and_invalid_utf8_rejected(tmp_path):
    path = tmp_path / "exceptions_masks.txt"
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(ConfigError):
        load_exception_masks(path)

    too_many = "\n".join(f"exact\tw{i}" for i in range(MAX_MASK_RULES + 1))
    path.write_text(too_many, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_exception_masks(path)

    with pytest.raises(ConfigError):
        validate_exception_mask("prefix", "a*b")


def test_detector_masks_override_personal_words_and_early_prefix():
    km = make_keymap()
    masks = ExceptionRules([
        ExceptionMask("prefix", "ghbd"),
        ExceptionMask("contains", "ст"),
    ])
    det = Detector(km, _models(), exceptions=set(), exception_masks=masks)
    det.set_personal_words({("ru", "привет")})
    keys = keys_for_text(km, "ghbdtn", US)
    assert det.decide(keys, US, RU).reason == "exception"

    early_keys = keys_for_text(km, "ghbd", US)
    assert det.early_word_target(early_keys, US, RU) is None

    # contains, уже видимый в префиксе (4 буквы), подавляет early; невидимый суффикс — нет.
    assert masks.matches_prefix("тест") is True
    assert masks.matches_prefix("те") is False


def test_controller_masks_block_punto_and_auto_but_allow_manual_word():
    km = make_keymap()
    masks = ExceptionRules([ExceptionMask("prefix", "кемб")])
    det = Detector(km, _models(), exceptions=set(), exception_masks=masks)
    typist = RecordingTypist()
    probe = FakeProbe(lambda: 100.0, app="editor")
    ctrl = Controller(Config(), km, det, FakeBackend(current=RU), typist,
                      clock=lambda: 100.0, probe=probe)
    for k in keys_for_text(km, "кембы ", RU):
        ctrl.on_key(k.code, 1, 1)
        ctrl.on_key(k.code, 0, 1)
    assert typist.taps() == []  # split_words_fix заблокирован маской

    # Ручная команда игнорирует маски.
    for k in keys_for_text(km, "ghbdtn", US):
        ctrl.on_key(k.code, 1, 1)
        ctrl.on_key(k.code, 0, 1)
    ctrl.detector.masks = ExceptionRules([ExceptionMask("exact", "ghbdtn")])
    ctrl.backend.current_index = US
    assert ctrl.manual_word() is True
    assert typist.taps()


def test_explain_reports_mask_exclusion(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    save_exception_masks(ExceptionRules([ExceptionMask("prefix", "ghbd")]),
                         cfgmod.exceptions_masks_path())
    settings = explain.load_settings()
    assert settings.masks.reason("ghbdtn") == "prefix"


@pytest.mark.parametrize("scheme", ["http://", "https://", "ftp://"])
@pytest.mark.parametrize("kind", ["exact", "prefix", "contains", "legacy"])
def test_url_masks_block_early_and_word_boundary(scheme, kind):
    km = make_keymap()
    keys = keys_for_text(km, scheme, US)
    source = km.decode(keys, RU)
    pattern = source if kind in {"exact", "legacy"} else source[:2]
    det = Detector(km, _models())
    assert det.early_url_target(keys, RU, US) == scheme
    assert det.decide(keys, RU, US).reason == "url"
    if kind == "legacy":
        det.exceptions.add(pattern.lower())
    else:
        det.masks = ExceptionRules([validate_exception_mask(kind, pattern)])
    assert det.early_url_target(keys, RU, US) is None
    assert det.decide(keys, RU, US).reason == "exception"

    ctrl = Controller(Config(), km, det, FakeBackend(current=RU), RecordingTypist(),
                      clock=lambda: 100.0, probe=FakeProbe(lambda: 100.0, app="editor"))
    scheduled = []
    ctrl._schedule_fix = lambda *args, **kwargs: scheduled.append((args, kwargs))
    ctrl._on_letter(keys, 1, keys[-1].code)
    assert scheduled == []
