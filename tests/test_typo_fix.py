"""Тесты опционального исправления опечаток (#67)."""

import pytest

from tests.fakes import (
    RU,
    US,
    FakeBackend,
    RecordingTypist,
    keys_for_text,
    make_keymap,
    run_after_key,
)
from wayswitch import a11y
from wayswitch.config import Config, ExceptionMask, ExceptionRules
from wayswitch.controller import Controller
from wayswitch.detector import Detector, LanguageModel, TypoIndex, allowed_edit


def _models():
    return {
        "ru": LanguageModel.from_words(
            "ru",
            [
                ("привет", 900.0),
                ("нормально", 800.0),
                ("небоскрёб", 700.0),
                ("небоскреб", 700.0),
                ("карта", 600.0),
                ("парта", 600.0),
                ("марта", 600.0),
            ],
            "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        ),
        "en": LanguageModel.from_words(
            "en",
            [("hello", 900.0), ("world", 800.0)],
            "abcdefghijklmnopqrstuvwxyz",
        ),
    }


def test_allowed_edit_accepts_insert_delete_swap_and_rejects_substitution():
    assert allowed_edit("првет", "привет") is True
    assert allowed_edit("приветт", "привет") is True
    assert allowed_edit("нромально", "нормально") is True
    assert allowed_edit("превет", "привет") is False


def test_typo_index_and_detector_rules(monkeypatch):
    km = make_keymap()
    models = _models()
    # Положительная опечатка допустима только при отклонённом кандидате раскладки.
    monkeypatch.setattr(models["en"], "score", lambda _text: -8.0)
    det = Detector(km, models)
    assert det._typo_indices == {}

    # При выключенном typo_fix индекс не строится.
    dec_off = det.decide(keys_for_text(km, "првет", RU), RU, US, typo_fix=False)
    assert dec_off.action == "keep"
    assert det._typo_indices == {}

    # Включение: пропуск, вставка, перестановка и сохранение регистра.
    for typed, expected in (
        ("првет", "привет"),
        ("Првет", "Привет"),
        ("ПРВЕТ", "ПРИВЕТ"),
        ("приветт", "привет"),
        ("нромально", "нормально"),
    ):
        dec = det.decide(keys_for_text(km, typed, RU), RU, US, typo_fix=True)
        assert dec.action == "fix"
        assert dec.reason == "typo"
        assert dec.same_layout is True
        assert dec.target_text == expected

    # Замена 1 символа в v1 запрещена.
    assert det.decide(keys_for_text(km, "превет", RU), RU, US, typo_fix=True).action == "keep"
    # Слово короче 5 символов не трогается.
    assert det.decide(keys_for_text(km, "wrod", US), US, RU, typo_fix=True).action == "keep"
    # Неоднозначность (карта / парта / марта) -> keep.
    assert det.decide(keys_for_text(km, "аарта", RU), RU, US, typo_fix=True).action == "keep"
    # Небоскрёб / небоскреб в словаре сохраняются как есть.
    assert det.decide(keys_for_text(km, "небоскрёб", RU), RU, US, typo_fix=True).action == "keep"
    assert det.decide(keys_for_text(km, "небоскреб", RU), RU, US, typo_fix=True).action == "keep"


@pytest.mark.parametrize("other_score, expected", [(-5.0, "keep"), (-8.0, "fix")])
def test_typo_fallback_does_not_override_valid_ngram_candidate(monkeypatch, other_score, expected):
    km = make_keymap()
    models = _models()
    monkeypatch.setattr(models["ru"], "score", lambda _text: -5.0)
    monkeypatch.setattr(models["en"], "score", lambda _text: other_score)
    det = Detector(km, models)
    keys = keys_for_text(km, "нромально", RU)
    baseline = det.decide(keys, RU, US)
    assert baseline.action == "keep"
    decision = det.decide(keys, RU, US, typo_fix=True)
    assert decision.action == expected
    if expected == "keep":
        assert decision.reason == baseline.reason
        assert decision.target_text == baseline.target_text
        assert det._typo_indices == {}
    else:
        assert decision.reason == "typo"
        assert decision.target_text == "нормально"
        assert decision.same_layout is True


def test_typo_respects_layout_priority_masks_personal_and_tech():
    km = make_keymap()
    masks = ExceptionRules([ExceptionMask("prefix", "нром")])
    det = Detector(km, _models(), exceptions={"приветт"}, exception_masks=masks)
    det.set_personal_words({("ru", "кастомное")})

    # Раскладка приоритетнее опечатки.
    layout_dec = det.decide(keys_for_text(km, "ghbdtn", US), US, RU, typo_fix=True)
    assert layout_dec.action == "fix"
    assert layout_dec.reason == "dict-b"
    assert layout_dec.same_layout is False

    # Точное исключение и маска блокируют опечатку.
    assert det.decide(
        keys_for_text(km, "приветт", RU), RU, US, typo_fix=True).reason == "exception"
    assert det.decide(
        keys_for_text(km, "нромально", RU), RU, US, typo_fix=True).reason == "exception"

    # Персональное слово не попадает в TypoIndex.
    idx = TypoIndex(_models()["ru"].words)
    assert idx.lookup("кастмное") is None
    assert det.typo_candidate("кастмное", "ru") is None

    # Технические токены не трогаются.
    for tech in ("useState", "src/main.py", "v1.2.3"):
        dec = det.decide(keys_for_text(km, tech, US), US, RU, typo_fix=True)
        assert dec.action == "keep"


def test_controller_typo_fix_keeps_layout_and_blocks_secret_focus(monkeypatch):
    km = make_keymap()
    cfg = Config()
    cfg.general.typo_fix = True
    cfg.general.skip_password_fields = False
    models = _models()
    monkeypatch.setattr(models["en"], "score", lambda _text: -8.0)
    det = Detector(km, models)
    backend = FakeBackend(current=RU)
    typist = RecordingTypist()
    snap = [a11y.FocusSnapshot("editor", False, 100.0, field=(":1.2", "/field"))]
    probe = type("P", (), {"snapshot": lambda _s: snap[0], "poke": lambda _s: None})()
    ctrl = Controller(cfg, km, det, backend, typist, clock=lambda: 100.0, probe=probe)

    def send_word(word):
        for k in keys_for_text(km, word, RU):
            run_after_key(ctrl, lambda c=k.code: (ctrl.on_key(c, 1, 1), ctrl.on_key(c, 0, 1)))

    send_word("првет ")
    assert backend.set_calls == []
    assert typist.taps()

    # При защищённом фокусе typo_fix не срабатывает, даже если skip_password_fields=False.
    typist.events.clear()
    snap[0] = snap[0]._replace(password=True)
    send_word("нромально ")
    assert typist.taps() == []
