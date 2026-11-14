"""Golden-корпус решений #73: фиксированные ответы, не снимок текущего кода.

Корпус хранит физические нажатия и ожидания, написанные по действующим
контрактам детектора/буфера/фокуса. CLI только прогоняет корпус; он не
пересобирает ожидания.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
import unicodedata
import uuid
from pathlib import Path

import pytest

from wayswitch import __version__

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "focus_golden_v1.json"
DATA = ROOT / "wayswitch" / "data"


def _load_benchmark():
    path = ROOT / "tools" / "benchmark_focus.py"
    spec = importlib.util.spec_from_file_location("benchmark_focus_golden", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def benchmark():
    return _load_benchmark()


@pytest.fixture(scope="module")
def corpus():
    return json.loads(CORPUS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def report(benchmark):
    return benchmark.run_golden(CORPUS)


@pytest.fixture(scope="module")
def workdir():
    path = ROOT / f".tmp-focus-golden-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _copy_corpus():
    return json.loads(CORPUS.read_text(encoding="utf-8"))


def _write_corpus(workdir: Path, name: str, corpus: dict) -> Path:
    path = workdir / name
    path.write_text(json.dumps(corpus, ensure_ascii=False), encoding="utf-8")
    return path


def test_corpus_is_synthetic_and_pinned(corpus):
    assert corpus["schema_version"] == 1
    assert corpus["provenance"]["kind"] == "synthetic"
    assert corpus["provenance"]["seed"] == 0
    assert corpus["provenance"]["unicode_normalization"] == "NFC"
    assert set(corpus["data_sha256"]) == {
        "en.ngrams.json.gz", "en.words.gz", "ru.ngrams.json.gz", "ru.words.gz",
    }
    for name, digest in corpus["data_sha256"].items():
        assert _sha256(DATA / name) == digest, name

    ids = [case["id"] for case in corpus["cases"]]
    assert len(ids) == len(set(ids))
    assert len(ids) >= 30


@pytest.mark.parametrize("layouts", [["ru", "us"], ["us", "us"]])
def test_corpus_rejects_layouts_the_runner_does_not_honor(benchmark, workdir, layouts):
    corpus = _copy_corpus()
    corpus["cases"][0]["layouts"] = layouts
    path = _write_corpus(workdir, "layouts.json", corpus)
    with pytest.raises(ValueError, match="layouts"):
        benchmark.load_corpus(path)


def test_corpus_rejects_settings_ignored_by_the_case_kind(benchmark, workdir):
    corpus = _copy_corpus()
    case = next(case for case in corpus["cases"] if case["kind"] == "decision")
    case["settings"] = {"skip_password_fields": True}
    path = _write_corpus(workdir, "settings.json", corpus)
    with pytest.raises(ValueError, match="settings"):
        benchmark.load_corpus(path)


def test_corpus_rejects_text_mislabeled_as_nfc(benchmark, workdir):
    corpus = _copy_corpus()
    case = next(case for case in corpus["cases"] if case["id"] == "ru-yo-entered-yo")
    decomposed = unicodedata.normalize("NFD", case["expected"]["target"])
    assert decomposed != case["expected"]["target"]
    case["expected"]["target"] = decomposed
    path = _write_corpus(workdir, "nfc.json", corpus)
    with pytest.raises(ValueError, match="NFC"):
        benchmark.load_corpus(path)


def test_every_case_declares_physical_input_and_expected_outcome(corpus):
    for case in corpus["cases"]:
        assert case["provenance"] == "synthetic"
        assert case["kind"] in ("decision", "buffer", "focus", "controller")
        assert isinstance(case["tags"], list) and case["tags"]
        assert len(case["layouts"]) == 2
        assert case["group"] in (0, 1)
        assert case["expected"]
        if case["kind"] == "decision":
            keys = case["input"]["keys"]
            assert keys
            for key in keys:
                assert isinstance(key["code"], int)
                assert key["value"] == 1
                assert set(key["modifiers"]) <= {"shift", "caps"}
        elif case["kind"] == "buffer":
            events = case["input"]["events"]
            assert events
            assert all(event["value"] in (0, 1, 2) for event in events)
        elif case["kind"] == "focus":
            assert "focus" in case["input"]
            assert "now" in case["input"]
        else:
            assert case["input"]["events"]


def test_required_coverage_tags(corpus):
    tags = {tag for case in corpus["cases"] for tag in case["tags"]}
    assert {
        "direction-ru", "direction-en", "dictionary", "ngram", "threshold",
        "short", "technical", "contraction", "e-yo", "case", "mixed",
        "editing", "stale", "unknown", "password", "exception", "personal",
        "url", "held", "repeat", "focus",
    } <= tags


def test_all_cases_match_fixed_expected_decisions(report):
    assert report["summary"] == {
        "total": len(report["cases"]),
        "passed": len(report["cases"]),
        "failed": 0,
        "errors": 0,
    }
    for case in report["cases"]:
        assert case["status"] == "pass", (case["id"], case["actual"], case["error"])


def test_selected_user_visible_outcomes_are_pinned(report):
    pinned = {
        "ru-dict-ghbdtn": ("fix", "привет", 1, "dict-b"),
        "ru-tail-period": ("fix", "привет.", 1, "dict-b"),
        "ru-yo-entered-e": ("fix", "небоскреб", 1, "dict-b"),
        "ru-dc-collision": ("keep", "dc`", 0, "dict-a"),
        "en-dict": ("fix", "hello", 0, "dict-b"),
        "en-contraction-normal": ("fix", "I'm", 0, "dict-b"),
        "en-contraction-conservative": ("keep", "Шэь", 1, "short"),
        "ru-dict-near-threshold": ("fix", "книгой", 1, "dict-b"),
        "ru-ngram-context-doubles-margin": ("keep", "rkfdbfnehs", 0, "ngram"),
        "ru-ngram-without-context": ("fix", "клавиатуры", 1, "ngram"),
        "tech-url": ("fix", "https://site.com", 0, "url"),
        "case-two-capitals-on": ("fix", "Привет", 1, "capitals-layout"),
        "exception": ("keep", "ghbdtn", 0, "exception"),
        "personal": ("fix", "ubuntu", 0, "personal"),
        "personal-exception": ("keep", "гигтег", 1, "exception"),
    }
    actual = {
        case["id"]: (case["actual"]["action"], case["actual"]["target"],
                     case["actual"]["group"], case["actual"]["reason_key"])
        for case in report["cases"] if case["kind"] == "decision"
    }
    for case_id, expected in pinned.items():
        assert actual[case_id] == expected, case_id


def test_refusals_are_explicit(report):
    focus = {case["id"]: case["actual"] for case in report["cases"]
             if case["kind"] == "focus"}
    assert focus["focus-safe"] == {"blocked": False, "reason": None}
    assert focus["focus-password"] == {"blocked": True, "reason": "защищённый ввод"}
    assert focus["focus-password-disabled"] == {"blocked": False, "reason": None}
    assert focus["focus-unknown"] == {"blocked": True, "reason": "фокус недоступен"}
    assert focus["focus-stale"] == {"blocked": True, "reason": "фокус устарел"}
    assert focus["focus-excluded"] == {
        "blocked": True, "reason": "приложение TelegramDesktop",
    }


def test_report_pins_data_corpus_and_sample_metadata(report):
    assert report["result_kind"] == "golden"
    assert report["app"]["version"] == __version__
    assert report["harness"]["version"] == 1
    assert report["corpus"]["sha256"] == _sha256(CORPUS)
    assert report["corpus"]["case_count"] == len(report["cases"])
    assert report["seed"] == 0
    assert report["data"]["ru.words.gz"] == _sha256(DATA / "ru.words.gz")
    assert report["samples"]["kind"] == "golden"
    assert report["samples"]["p99_min_samples"] == 100
    assert report["settings"]["sensitivity"] == "normal"


def test_runner_does_not_regenerate_the_corpus(benchmark, report):
    before = CORPUS.read_bytes()
    again = benchmark.run_golden(CORPUS)
    assert CORPUS.read_bytes() == before
    first = {case["id"]: case["actual"] for case in report["cases"]}
    second = {case["id"]: case["actual"] for case in again["cases"]}
    assert first == second


def test_golden_covers_queued_correction_and_cancellation(report):
    cases = {case["id"]: case for case in report["cases"]}
    assert cases["controller-correct"]["actual"] == {
        "corrections": ["привет"], "writes": True, "group": 1}
    for name in ("controller-click-cancels", "controller-field-change",
                 "controller-password", "controller-unknown"):
        assert cases[name]["actual"] == {"corrections": [], "writes": False, "group": 0}
    assert cases["controller-layout-cancels"]["actual"] == {
        "corrections": [], "writes": False, "group": 1}
