"""Pure golden decisions and result comparison for focus behavior.

No evdev, AT-SPI, D-Bus or live typing lives here. ``golden`` runs the
versioned corpus through the existing pure detector/buffer/focus interfaces;
``compare`` validates two result files and reports decision differences and
latency sample statistics. Golden expectations are never regenerated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import subprocess
import sys
import unicodedata
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.fakes import FakeBackend, RecordingTypist, make_keymap  # noqa: E402
from wayswitch import (  # noqa: E402
    __version__,
    a11y,
)
from wayswitch.buffer import InputBuffer, KeyPress  # noqa: E402
from wayswitch.config import Config  # noqa: E402
from wayswitch.controller import Controller  # noqa: E402
from wayswitch.detector import SENSITIVITY, Detector, LanguageModel  # noqa: E402

SCHEMA_VERSION = 1
HARNESS_VERSION = 1
P99_MIN_SAMPLES = 100
P50_P95_MIN_SAMPLES = 20
DEFAULT_CORPUS = ROOT / "tests" / "fixtures" / "focus_golden_v1.json"
DATA_FILES = ("en.ngrams.json.gz", "en.words.gz", "ru.ngrams.json.gz", "ru.words.gz")
CASE_FIELDS = {
    "decision": ("action", "target", "group", "reason_key"),
    "buffer": ("kind", "text", "key_count", "reset_before"),
    "focus": ("blocked", "reason"),
    "controller": ("corrections", "writes", "group"),
}
LATENCY_STATUSES = frozenset({"ok", "refused", "timeout", "lost", "duplicate"})
KIND_SETTINGS = {
    "decision": frozenset({"sensitivity", "two_capitals"}),
    "buffer": frozenset({"phrase_timeout_sec"}),
    "focus": frozenset({"skip_password_fields", "excluded_apps"}),
    "controller": frozenset(),
}
SETTINGS_KEYS = frozenset().union(*KIND_SETTINGS.values())
SAMPLE_FAILURE_FIELDS = (
    "refusals", "timeouts", "lost", "duplicates", "errors", "invalid_latencies",
)

_MODELS: dict[str, LanguageModel] | None = None
_KEYMAP = None


class SnapshotProbe:
    """Minimal probe over an already-decided snapshot; no querying."""

    def __init__(self, snapshot: a11y.FocusSnapshot):
        self._snapshot = snapshot

    def snapshot(self) -> a11y.FocusSnapshot:
        return self._snapshot

    def poke(self) -> None:
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reason_key(reason: str) -> str:
    return reason.split(" ", 1)[0]


def _models() -> dict[str, LanguageModel]:
    global _MODELS
    if _MODELS is None:
        _MODELS = {lang: LanguageModel.load(lang) for lang in ("ru", "en")}
    return _MODELS


def _keymap():
    global _KEYMAP
    if _KEYMAP is None:
        _KEYMAP = make_keymap()
    return _KEYMAP


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _require_nfc(value: object, where: str = "corpus") -> None:
    """Reject non-NFC input instead of silently normalizing the corpus."""
    if isinstance(value, str):
        _require(unicodedata.normalize("NFC", value) == value, f"{where}: text is not NFC")
    elif isinstance(value, dict):
        for key, item in value.items():
            _require_nfc(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _require_nfc(item, f"{where}[{index}]")


def _validate_case(case: dict, index: int) -> None:
    where = f"case #{index}"
    for name in ("id", "kind", "provenance", "layouts", "group", "tags", "expected"):
        _require(name in case, f"{where}: missing {name}")
    _require(isinstance(case["id"], str) and case["id"], f"{where}: bad id")
    where = case["id"]
    _require(case["provenance"] == "synthetic", f"{where}: provenance must be synthetic")
    _require(case["kind"] in CASE_FIELDS, f"{where}: unsupported kind {case['kind']}")
    _require(isinstance(case["tags"], list) and case["tags"], f"{where}: empty tags")
    _require(case["group"] in (0, 1), f"{where}: group must be 0 or 1")
    _require(case["layouts"] == ["us", "ru"], f"{where}: layouts must be ['us', 'ru']")
    settings = case.get("settings", {})
    _require(isinstance(settings, dict), f"{where}: settings must be an object")
    _require(set(settings) <= KIND_SETTINGS[case["kind"]],
             f"{where}: settings not used by {case['kind']}")
    _require(isinstance(case.get("exceptions", []), list), f"{where}: exceptions")
    _require(isinstance(case.get("personal", []), list), f"{where}: personal")
    if case["kind"] != "decision":
        _require(not case.get("exceptions"), f"{where}: exceptions only for decision")
        _require(not case.get("personal"), f"{where}: personal only for decision")
        _require("context" not in case, f"{where}: context only for decision")
    expected = case["expected"]
    _require(isinstance(expected, dict), f"{where}: expected must be an object")

    if case["kind"] == "decision":
        keys = case.get("input", {}).get("keys")
        _require(isinstance(keys, list) and keys, f"{where}: decision keys")
        for key in keys:
            _require(isinstance(key.get("code"), int), f"{where}: key code")
            _require(key.get("value") == 1, f"{where}: decision value must be 1")
            _require(set(key.get("modifiers", [])) <= {"shift", "caps"},
                     f"{where}: key modifiers")
        _require(set(expected) == {"action", "target", "group", "reason"},
                 f"{where}: decision expectation")
        _require(expected["action"] in ("fix", "keep"), f"{where}: decision action")
        _require(expected["group"] in (0, 1), f"{where}: decision group")
    elif case["kind"] == "buffer":
        events = case.get("input", {}).get("events")
        _require(isinstance(events, list) and events, f"{where}: buffer events")
        for event in events:
            _require(isinstance(event.get("code"), int), f"{where}: event code")
            _require(event.get("value") in (0, 1, 2), f"{where}: event value")
        _require(set(expected) == {"kind", "text", "key_count", "reset_before"},
                 f"{where}: buffer expectation")
    elif case["kind"] == "controller":
        events = case.get("input", {}).get("events")
        _require(isinstance(events, list) and events, f"{where}: controller events")
        for event in events:
            if "action" in event:
                _require(event["action"] in {"click", "field", "layout"},
                         f"{where}: controller action")
                if event["action"] == "layout":
                    _require(type(event.get("group")) is int and event["group"] in (0, 1),
                             f"{where}: controller layout")
            else:
                _require(type(event.get("code")) is int and event.get("value") in (0, 1, 2),
                         f"{where}: controller key event")
        _require(set(expected) == {"corrections", "writes", "group"},
                 f"{where}: controller expectation")
    else:
        focus = case.get("input", {}).get("focus")
        _require(isinstance(focus, dict), f"{where}: focus input")
        _require(isinstance(focus.get("taken_at"), (int, float)), f"{where}: taken_at")
        _require(isinstance(case["input"].get("now"), (int, float)), f"{where}: now")
        _require(set(expected) == {"blocked", "reason"}, f"{where}: focus expectation")
        _require(isinstance(expected["blocked"], bool), f"{where}: blocked")


def load_corpus(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    corpus = json.loads(raw.decode("utf-8"))
    _require(isinstance(corpus, dict), "corpus must be an object")
    _require(corpus.get("schema_version") == SCHEMA_VERSION, "unsupported corpus schema")
    provenance = corpus.get("provenance", {})
    _require(provenance.get("kind") == "synthetic", "corpus provenance must be synthetic")
    _require(provenance.get("unicode_normalization") == "NFC", "corpus Unicode normalization")
    _require(isinstance(provenance.get("seed"), int), "corpus seed")
    _require_nfc(corpus)
    data_hashes = corpus.get("data_sha256")
    _require(isinstance(data_hashes, dict), "corpus data hashes")
    _require(set(data_hashes) == set(DATA_FILES), "corpus data files")
    for name, expected in data_hashes.items():
        actual = _sha256(ROOT / "wayswitch" / "data" / name)
        _require(actual == expected, f"data hash mismatch: {name}")
    defaults = corpus.get("defaults", {})
    _require(isinstance(defaults.get("settings"), dict), "default settings")
    _require(set(defaults["settings"]) <= SETTINGS_KEYS, "unknown default setting")
    _require(set(defaults["settings"]) == SETTINGS_KEYS, "default settings incomplete")
    cases = corpus.get("cases")
    _require(isinstance(cases, list) and cases, "corpus cases")
    for index, case in enumerate(cases):
        _require(isinstance(case, dict), f"case #{index}: must be an object")
        _validate_case(case, index)
    ids = [case["id"] for case in cases]
    _require(len(ids) == len(set(ids)), "duplicate corpus case id")
    return corpus, hashlib.sha256(raw).hexdigest()


def _git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _settings(case: dict, defaults: dict) -> dict:
    settings = dict(defaults["settings"])
    settings.update(case.get("settings", {}))
    return settings


def _personal(rows) -> set[tuple[str, str]]:
    return {(lang, word) for lang, word in rows}


def _run_decision(case: dict, defaults: dict) -> tuple[bool, dict]:
    keymap = _keymap()
    settings = _settings(case, defaults)
    exceptions = set(defaults.get("exceptions", [])) | set(case.get("exceptions", []))
    detector = Detector(
        keymap,
        _models(),
        exceptions,
        SENSITIVITY[settings["sensitivity"]],
    )
    personal = _personal(defaults.get("personal", [])) | _personal(case.get("personal", []))
    if personal:
        detector.set_personal_words(personal)
    current = case["group"]
    other = keymap.opposite_group(current)
    _require(other is not None, f"{case['id']}: no opposite layout group")
    keys = [
        KeyPress(key["code"], "shift" in key.get("modifiers", []),
                 "caps" in key.get("modifiers", []))
        for key in case["input"]["keys"]
    ]
    decision = detector.decide(
        keys,
        current,
        other,
        tuple(case.get("context", (None, None))),
        two_capitals=bool(settings["two_capitals"]),
    )
    actual = {
        "action": decision.action,
        "target": decision.target_text,
        "group": current if decision.action == "keep" else other,
        "reason": decision.reason,
        "reason_key": _reason_key(decision.reason),
    }
    expected = case["expected"]
    matched = (
        actual["action"] == expected["action"]
        and actual["target"] == expected["target"]
        and actual["group"] == expected["group"]
        and actual["reason_key"] == expected["reason"]
    )
    return matched, actual


def _run_buffer(case: dict, defaults: dict) -> tuple[bool, dict]:
    keymap = _keymap()
    settings = _settings(case, defaults)
    buffer = InputBuffer(keymap, float(settings["phrase_timeout_sec"]))
    last = None
    for index, event in enumerate(case["input"]["events"]):
        result = buffer.feed(
            event["code"],
            event["value"],
            event.get("device", 1),
            float(event.get("time", index)),
        )
        if result is not None:
            last = result
    if last is None:
        actual = {"kind": None, "text": None, "key_count": None, "reset_before": False}
    else:
        text = keymap.decode(last.keys, case["group"]) if last.kind == "word" else None
        actual = {
            "kind": last.kind,
            "text": text,
            "key_count": len(last.keys) if last.kind == "word" else None,
            "reset_before": last.reset_before,
        }
    return actual == case["expected"], actual


def _run_focus(case: dict, defaults: dict) -> tuple[bool, dict]:
    settings = _settings(case, defaults)
    focus = case["input"]["focus"]
    snapshot = a11y.FocusSnapshot(
        focus.get("app"),
        bool(focus.get("password", False)),
        float(focus["taken_at"]),
        tuple(focus.get("app_ids", ())),
    )
    reason = a11y.skip_reason(
        SnapshotProbe(snapshot),
        settings["excluded_apps"],
        bool(settings["skip_password_fields"]),
        float(case["input"]["now"]),
    )
    actual = {"blocked": reason is not None, "reason": reason}
    return actual == case["expected"], actual


def _run_controller(case: dict, defaults: dict) -> tuple[bool, dict]:
    config = Config()
    config.general.skip_password_fields = True
    config.general.early_switch = False
    focus = case["input"].get("focus", "safe")
    probe = SnapshotProbe(a11y.FocusSnapshot(
        "editor" if focus != "unknown" else None, focus == "password", 100.0,
        field=(":fixture", "/field"), generation=1))
    backend, typist = FakeBackend(current=case["group"]), RecordingTypist()
    pending, corrected = [], []
    controller = Controller(
        config, _keymap(), Detector(_keymap(), _models()), backend, typist,
        clock=lambda: 100.0, probe=probe, schedule=pending.append,
        on_corrected=lambda _original, fixed, _manual: corrected.append(fixed))
    backend.on_change(controller.on_layout_changed)
    for event in case["input"]["events"]:
        if event.get("action") == "click":
            controller.on_click()
        elif event.get("action") == "field":
            probe._snapshot = probe._snapshot._replace(field=(":fixture", "/other"), generation=2)
        elif event.get("action") == "layout":
            backend.emit_external_change(event["group"])
        else:
            controller.on_key(event["code"], event["value"], 1)
    for operation in pending:
        operation()
    actual = {"corrections": corrected, "writes": bool(typist.events), "group": backend.current()}
    return actual == case["expected"], actual


def _run_case(case: dict, defaults: dict) -> dict:
    runner = {
        "decision": _run_decision,
        "buffer": _run_buffer,
        "focus": _run_focus,
        "controller": _run_controller,
    }[case["kind"]]
    result = {
        "id": case["id"],
        "kind": case["kind"],
        "tags": list(case["tags"]),
        "expected": case["expected"],
        "actual": None,
        "status": "error",
        "latency_ms": None,
        "error": None,
    }
    try:
        matched, actual = runner(case, defaults)
    except Exception as exc:  # noqa: BLE001 — every corpus failure belongs in the report
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    result["actual"] = actual
    result["status"] = "pass" if matched else "fail"
    return result


def run_golden(path: Path = DEFAULT_CORPUS) -> dict:
    path = Path(path)
    corpus, corpus_sha = load_corpus(path)
    cases = [_run_case(case, corpus["defaults"]) for case in corpus["cases"]]
    passed = sum(case["status"] == "pass" for case in cases)
    failed = sum(case["status"] == "fail" for case in cases)
    errors = sum(case["status"] == "error" for case in cases)
    data = {name: _sha256(ROOT / "wayswitch" / "data" / name) for name in DATA_FILES}
    return {
        "schema_version": SCHEMA_VERSION,
        "result_kind": "golden",
        "app": {
            "name": "wayswitch",
            "version": __version__,
            "sha": _git_sha(),
        },
        "harness": {
            "name": "benchmark_focus",
            "version": HARNESS_VERSION,
            "sha256": _sha256(Path(__file__)),
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "corpus": {
            "path": str(path.resolve()),
            "sha256": corpus_sha,
            "schema_version": corpus["schema_version"],
            "case_count": len(cases),
            "seed": corpus["provenance"]["seed"],
            "provenance": corpus["provenance"],
        },
        "data": data,
        "seed": corpus["provenance"]["seed"],
        "settings": corpus["defaults"]["settings"],
        "samples": {
            "kind": "golden",
            "count": len(cases),
            "attempts": len(cases),
            "latency_unit": "ms",
            "p99_min_samples": P99_MIN_SAMPLES,
        },
        "cases": cases,
        "summary": {
            "total": len(cases),
            "passed": passed,
            "failed": failed,
            "errors": errors,
        },
    }


def _percentile(values: list[float], quantile: float) -> float:
    position = (len(values) - 1) * quantile
    lower = math.floor(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


def summarize_samples(samples: object) -> dict:
    if not isinstance(samples, dict) or not samples.get("records"):
        return {"available": False, "reason": "no sample records"}
    records = samples["records"]
    counts = Counter()
    latencies: list[float] = []
    invalid = 0
    for record in records:
        if not isinstance(record, dict):
            counts["errors"] += 1
            continue
        status = record.get("status")
        if status not in LATENCY_STATUSES:
            counts["errors"] += 1
            continue
        counts[status] += 1
        if status != "ok":
            continue
        value = record.get("latency_ms")
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0):
            invalid += 1
        else:
            latencies.append(float(value))
    latencies.sort()
    stats = {
        "available": bool(latencies),
        "attempts": len(records),
        "successful": len(latencies),
        "refusals": counts["refused"],
        "timeouts": counts["timeout"],
        "lost": counts["lost"],
        "duplicates": counts["duplicate"],
        "errors": counts["errors"],
        "invalid_latencies": invalid,
        "min": latencies[0] if latencies else None,
        "max": latencies[-1] if latencies else None,
        "mean": sum(latencies) / len(latencies) if latencies else None,
        "p50": None,
        "p95": None,
        "p99": None,
        "p50_reason": None,
        "p95_reason": None,
        "p99_reason": None,
        # A short series is shown in full instead of inventing a tail percentile.
        "values": list(latencies) if len(latencies) < P99_MIN_SAMPLES else None,
    }
    declared = samples.get("attempts")
    if isinstance(declared, int) and declared != len(records):
        stats["declared_attempts"] = declared
        stats["attempts_mismatch"] = True
    if len(latencies) >= P50_P95_MIN_SAMPLES:
        stats["p50"] = _percentile(latencies, 0.50)
        stats["p95"] = _percentile(latencies, 0.95)
    else:
        stats["p50_reason"] = f"fewer than {P50_P95_MIN_SAMPLES} successful samples"
        stats["p95_reason"] = f"fewer than {P50_P95_MIN_SAMPLES} successful samples"
    if len(latencies) >= P99_MIN_SAMPLES:
        stats["p99"] = _percentile(latencies, 0.99)
    else:
        stats["p99_reason"] = f"fewer than {P99_MIN_SAMPLES} successful samples"
    if not latencies:
        stats["reason"] = "no successful latency samples"
    return stats


def _sample_problems(samples: object, summary: dict) -> list[str]:
    """Problems that make a live/statistical sample set unclean for compare."""
    if not isinstance(samples, dict):
        return ["samples missing"]
    if samples.get("kind") == "golden":
        return []
    if samples.get("kind") != "live":
        return [f"unsupported samples kind: {samples.get('kind')}"]
    problems = []
    if not summary.get("attempts"):
        problems.append("no sample records")
    elif not summary.get("available"):
        problems.append("no successful samples")
    for field in SAMPLE_FAILURE_FIELDS:
        if summary.get(field):
            problems.append(f"{field}={summary[field]}")
    if summary.get("attempts_mismatch"):
        problems.append(
            "attempts mismatch: "
            f"declared {summary.get('declared_attempts')}, recorded {summary.get('attempts')}"
        )
    return problems


def _actual(case: dict) -> dict:
    actual = case.get("actual")
    if not isinstance(actual, dict):
        return {}
    kind = case.get("kind")
    if kind == "decision":
        reason_key = actual.get("reason_key")
        if reason_key is None and isinstance(actual.get("reason"), str):
            reason_key = _reason_key(actual["reason"])
        return {
            "action": actual.get("action"),
            "target": actual.get("target"),
            "group": actual.get("group"),
            "reason_key": reason_key,
        }
    fields = CASE_FIELDS.get(kind)
    if fields is None:
        return dict(actual)
    return {name: actual.get(name) for name in fields}


def _cases_by_id(result: dict) -> dict[str, dict]:
    cases = result.get("cases")
    if not isinstance(cases, list):
        raise ValueError("result has no cases list")
    mapping = {}
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str):
            raise ValueError("result case without id")
        if case["id"] in mapping:
            raise ValueError(f"duplicate result case id: {case['id']}")
        mapping[case["id"]] = case
    return mapping


def _result_meta(result: dict) -> dict:
    corpus = result.get("corpus")
    return {
        "app": result.get("app"),
        "harness": result.get("harness"),
        "environment": result.get("environment"),
        "corpus_sha256": corpus.get("sha256") if isinstance(corpus, dict) else None,
        "case_count": len(result.get("cases", [])),
    }


def compare_results(base: dict, candidate: dict) -> dict:
    incompatible = []
    if base.get("schema_version") != SCHEMA_VERSION:
        incompatible.append(f"base schema_version is not {SCHEMA_VERSION}")
    if candidate.get("schema_version") != SCHEMA_VERSION:
        incompatible.append(f"candidate schema_version is not {SCHEMA_VERSION}")
    if base.get("result_kind") not in ("golden", "live") \
            or candidate.get("result_kind") not in ("golden", "live"):
        incompatible.append("unsupported result kind")
    if base.get("result_kind") != candidate.get("result_kind"):
        incompatible.append("result kinds differ")
    for name in ("name", "version", "sha256"):
        left = base.get("harness", {}).get(name)
        right = candidate.get("harness", {}).get(name)
        if left != right:
            incompatible.append(f"harness {name} differs")
    left_env = base.get("environment")
    right_env = candidate.get("environment")
    if not isinstance(left_env, dict) or not isinstance(right_env, dict):
        incompatible.append("environment missing")
    elif left_env != right_env:
        incompatible.append("environment differs")
    if base.get("seed") != candidate.get("seed"):
        incompatible.append("seeds differ")
    if base.get("settings") != candidate.get("settings"):
        incompatible.append("settings differ")
    if base.get("data") != candidate.get("data"):
        incompatible.append("data hashes differ")
    left_corpus = base.get("corpus", {}).get("sha256")
    right_corpus = candidate.get("corpus", {}).get("sha256")
    if left_corpus != right_corpus:
        incompatible.append("corpus hashes differ")

    try:
        left_cases = _cases_by_id(base)
        right_cases = _cases_by_id(candidate)
    except ValueError as exc:
        incompatible.append(str(exc))
        left_cases, right_cases = {}, {}

    only_base = sorted(set(left_cases) - set(right_cases))
    only_candidate = sorted(set(right_cases) - set(left_cases))
    decision_changes = []
    status_changes = []
    for case_id in sorted(set(left_cases) & set(right_cases)):
        left = left_cases[case_id]
        right = right_cases[case_id]
        if _actual(left) != _actual(right):
            decision_changes.append({
                "id": case_id,
                "base": _actual(left),
                "candidate": _actual(right),
            })
        if left.get("status") != right.get("status"):
            status_changes.append({
                "id": case_id,
                "base": left.get("status"),
                "candidate": right.get("status"),
            })

    def failures(result: dict) -> list[dict]:
        return [
            {"id": case.get("id"), "status": case.get("status"), "error": case.get("error")}
            for case in result.get("cases", [])
            if case.get("status") in ("fail", "error")
        ]

    base_samples = base.get("samples")
    candidate_samples = candidate.get("samples")
    base_latency = summarize_samples(base_samples)
    candidate_latency = summarize_samples(candidate_samples)
    base_problems = _sample_problems(base_samples, base_latency)
    candidate_problems = _sample_problems(candidate_samples, candidate_latency)
    for result, problems in ((base, base_problems), (candidate, candidate_problems)):
        if result.get("result_kind") == "live":
            if result.get("success") is not True or result.get("layout_restored") is not True:
                problems.append("live run did not finish successfully with restored layout")
            requested = result.get("settings", {}).get("samples")
            records = result.get("samples", {}).get("records", [])
            if type(requested) is not int or requested <= 0 or len(records) != requested:
                problems.append("requested sample count was not completed")

    return {
        "schema_version": SCHEMA_VERSION,
        "compatible": not incompatible,
        "incompatible": incompatible,
        "base": _result_meta(base),
        "candidate": _result_meta(candidate),
        "decision_changes": decision_changes,
        "status_changes": status_changes,
        "only_base": only_base,
        "only_candidate": only_candidate,
        "failures": {
            "base": failures(base),
            "candidate": failures(candidate),
        },
        "latency": {
            "base": base_latency,
            "candidate": candidate_latency,
            "clean": {
                "base": not base_problems,
                "candidate": not candidate_problems,
            },
            "problems": {
                "base": base_problems,
                "candidate": candidate_problems,
            },
        },
        "summary": {
            "decision_changes": len(decision_changes),
            "status_changes": len(status_changes),
            "only_base": len(only_base),
            "only_candidate": len(only_candidate),
            "base_failures": len(failures(base)),
            "candidate_failures": len(failures(candidate)),
            "base_sample_issues": len(base_problems),
            "candidate_sample_issues": len(candidate_problems),
        },
    }


def compare_ok(report: dict) -> bool:
    summary = report["summary"]
    clean = report["latency"]["clean"]
    return (
        report["compatible"]
        and not report["decision_changes"]
        and not report["status_changes"]
        and not report["only_base"]
        and not report["only_candidate"]
        and summary["base_failures"] == 0
        and summary["candidate_failures"] == 0
        and clean["base"]
        and clean["candidate"]
    )


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_result(path: Path) -> dict:
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("result must be a JSON object")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    golden = commands.add_parser("golden", help="run the pure golden corpus")
    golden.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    golden.add_argument("--output", type=Path, required=True)
    compare = commands.add_parser("compare", help="compare two result files")
    compare.add_argument("base", type=Path)
    compare.add_argument("candidate", type=Path)
    compare.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if args.command == "golden":
        try:
            result = run_golden(args.corpus)
            _write_json(args.output, result)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"golden failed: {exc}", file=sys.stderr)
            return 1
        summary = result["summary"]
        print(f"golden: {summary['passed']}/{summary['total']} passed")
        return 0 if summary["failed"] == 0 and summary["errors"] == 0 else 1

    try:
        report = compare_results(_read_result(args.base), _read_result(args.candidate))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"compare failed: {exc}", file=sys.stderr)
        return 1
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if compare_ok(report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
