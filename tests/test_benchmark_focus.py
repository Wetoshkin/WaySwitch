"""CLI и статистика tools/benchmark_focus.py без живого ввода."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "benchmark_focus.py"


def _load_benchmark():
    spec = importlib.util.spec_from_file_location("benchmark_focus_cli", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def benchmark():
    return _load_benchmark()


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.fixture(scope="module")
def workdir():
    path = ROOT / f".tmp-focus-benchmark-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(scope="module")
def golden_file(workdir):
    output = workdir / "golden.json"
    result = _run_cli("golden", "--output", str(output))
    assert result.returncode == 0, result.stderr
    return output


@pytest.fixture(scope="module")
def golden_report(golden_file):
    return json.loads(golden_file.read_text(encoding="utf-8"))


def _write(workdir: Path, name: str, report: dict) -> Path:
    path = workdir / name
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return path


def _with_samples(report: dict, records: list[dict], attempts: int | None = None) -> dict:
    result = copy.deepcopy(report)
    result["result_kind"] = "live"
    result["success"] = True
    result["layout_restored"] = True
    result["settings"]["samples"] = len(records)
    result["samples"] = {
        "kind": "live",
        "attempts": len(records) if attempts is None else attempts,
        "records": records,
    }
    return result


@pytest.mark.parametrize("change", [{"success": False}, {"layout_restored": False},
                                    {"settings": {"samples": 100}}])
def test_compare_rejects_identical_incomplete_live_runs(benchmark, golden_report, change):
    report = _with_samples(golden_report, [{"status": "ok", "latency_ms": 1.0}] * 2)
    report.update(change)
    comparison = benchmark.compare_results(report, report)
    assert not benchmark.compare_ok(comparison)


def test_golden_cli_writes_report(golden_report):
    assert golden_report["result_kind"] == "golden"
    assert golden_report["summary"]["failed"] == 0
    assert golden_report["summary"]["errors"] == 0
    assert golden_report["cases"]
    assert all(case["latency_ms"] is None for case in golden_report["cases"])


def test_compare_identical_reports_no_changes(golden_file, golden_report):
    result = _run_cli("compare", str(golden_file), str(golden_file))
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["compatible"] is True
    assert report["decision_changes"] == []
    assert report["status_changes"] == []
    assert report["only_base"] == []
    assert report["only_candidate"] == []


def test_compare_reports_decision_difference(workdir, golden_report):
    candidate = copy.deepcopy(golden_report)
    case = next(case for case in candidate["cases"] if case["kind"] == "decision")
    case["actual"]["target"] = "changed"
    base_path = _write(workdir, "base.json", golden_report)
    candidate_path = _write(workdir, "candidate.json", candidate)
    result = _run_cli("compare", str(base_path), str(candidate_path))
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["compatible"] is True
    assert [change["id"] for change in report["decision_changes"]] == [case["id"]]


def test_compare_reports_failures(workdir, golden_report):
    candidate = copy.deepcopy(golden_report)
    case = candidate["cases"][0]
    case["status"] = "fail"
    case["error"] = "synthetic failure"
    base_path = _write(workdir, "base.json", golden_report)
    candidate_path = _write(workdir, "candidate.json", candidate)
    result = _run_cli("compare", str(base_path), str(candidate_path))
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert [change["id"] for change in report["status_changes"]] == [case["id"]]
    assert report["failures"]["candidate"][0]["id"] == case["id"]


@pytest.mark.parametrize("field", [
    "schema_version", "result_kind", "seed", "settings", "data", "corpus", "harness",
    "environment",
])
def test_compare_rejects_incompatible_runs(workdir, golden_report, field):
    candidate = copy.deepcopy(golden_report)
    if field == "schema_version":
        candidate["schema_version"] = 2
    elif field == "result_kind":
        candidate["result_kind"] = "live"
    elif field == "seed":
        candidate["seed"] = 1
    elif field == "settings":
        candidate["settings"]["sensitivity"] = "aggressive"
    elif field == "data":
        candidate["data"]["ru.words.gz"] = "0" * 64
    elif field == "corpus":
        candidate["corpus"]["sha256"] = "0" * 64
    elif field == "environment":
        candidate["environment"]["python"] = "0.0.0-other"
    else:
        candidate["harness"]["sha256"] = "0" * 64
    base_path = _write(workdir, "base.json", golden_report)
    candidate_path = _write(workdir, "candidate.json", candidate)
    result = _run_cli("compare", str(base_path), str(candidate_path))
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["compatible"] is False
    assert report["incompatible"], field


def test_latency_stats_account_for_every_attempt(benchmark):
    samples = {
        "attempts": 9,
        "records": [
            {"status": "ok", "latency_ms": 40.0},
            {"status": "ok", "latency_ms": 10.0},
            {"status": "ok", "latency_ms": 50.0},
            {"status": "ok", "latency_ms": 20.0},
            {"status": "ok", "latency_ms": 30.0},
            {"status": "refused"},
            {"status": "timeout"},
            {"status": "lost"},
            {"status": "duplicate"},
        ],
    }
    stats = benchmark.summarize_samples(samples)
    assert stats["attempts"] == 9
    assert stats["successful"] == 5
    assert stats["refusals"] == 1
    assert stats["timeouts"] == 1
    assert stats["lost"] == 1
    assert stats["duplicates"] == 1
    assert stats["errors"] == 0
    assert stats["max"] == 50.0
    assert stats["values"] == [10.0, 20.0, 30.0, 40.0, 50.0]
    assert stats["p50"] is None
    assert stats["p95"] is None
    assert stats["p99"] is None
    assert "100" in stats["p99_reason"]


def test_latency_stats_emit_p99_only_for_large_series(benchmark):
    samples = {
        "attempts": 101,
        "records": [
            *({"status": "ok", "latency_ms": float(value)} for value in range(100)),
            {"status": "refused"},
        ],
    }
    stats = benchmark.summarize_samples(samples)
    assert stats["attempts"] == 101
    assert stats["successful"] == 100
    assert stats["p50"] is not None
    assert stats["p95"] is not None
    assert stats["p99"] is not None
    assert stats["p99"] >= 98.0
    assert stats["values"] is None


def test_latency_stats_exclude_invalid_and_unknown_outcomes(benchmark):
    samples = {
        "attempts": 6,
        "records": [
            {"status": "ok", "latency_ms": 1.5},
            {"status": "ok", "latency_ms": None},
            {"status": "ok", "latency_ms": "12"},
            {"status": "ok", "latency_ms": True},
            {"status": "ok", "latency_ms": -1},
            {"status": "unexpected"},
        ],
    }
    stats = benchmark.summarize_samples(samples)
    assert stats["attempts"] == 6
    assert stats["successful"] == 1
    assert stats["invalid_latencies"] == 4
    assert stats["errors"] == 1
    assert stats["p99"] is None


def test_compare_accepts_clean_live_samples(workdir, golden_report):
    records = [
        {"status": "ok", "latency_ms": 10.0},
        {"status": "ok", "latency_ms": 20.0},
        {"status": "ok", "latency_ms": 30.0},
    ]
    base = _with_samples(golden_report, records)
    candidate = _with_samples(golden_report, records)
    base_path = _write(workdir, "base-clean.json", base)
    candidate_path = _write(workdir, "candidate-clean.json", candidate)
    result = _run_cli("compare", str(base_path), str(candidate_path))
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["latency"]["clean"] == {"base": True, "candidate": True}
    assert report["latency"]["base"]["attempts"] == 3
    assert report["latency"]["base"]["p99"] is None


def test_compare_rejects_identical_failed_samples(workdir, golden_report):
    records = [
        {"status": "ok", "latency_ms": 10.0},
        {"status": "refused"},
        {"status": "timeout"},
        {"status": "lost"},
        {"status": "duplicate"},
    ]
    base = _with_samples(golden_report, records)
    candidate = _with_samples(golden_report, records)
    base_path = _write(workdir, "base-failed.json", base)
    candidate_path = _write(workdir, "candidate-failed.json", candidate)
    result = _run_cli("compare", str(base_path), str(candidate_path))
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["compatible"] is True
    assert report["latency"]["clean"] == {"base": False, "candidate": False}
    problems = " ".join(report["latency"]["problems"]["candidate"])
    for token in ("refusals=1", "timeouts=1", "lost=1", "duplicates=1"):
        assert token in problems, problems


def test_compare_rejects_attempts_mismatch(workdir, golden_report):
    records = [
        {"status": "ok", "latency_ms": 10.0},
        {"status": "ok", "latency_ms": 20.0},
    ]
    base = _with_samples(golden_report, records, attempts=3)
    candidate = _with_samples(golden_report, records, attempts=3)
    base_path = _write(workdir, "base-mismatch.json", base)
    candidate_path = _write(workdir, "candidate-mismatch.json", candidate)
    result = _run_cli("compare", str(base_path), str(candidate_path))
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["latency"]["clean"] == {"base": False, "candidate": False}
    assert "attempts mismatch" in " ".join(report["latency"]["problems"]["base"])


def test_compare_reports_failed_latency_samples(workdir, golden_report):
    base = _with_samples(golden_report, [
        {"status": "ok", "latency_ms": 10.0},
        {"status": "ok", "latency_ms": 20.0},
        {"status": "refused"},
    ])
    candidate = _with_samples(golden_report, [
        {"status": "ok", "latency_ms": 11.0},
        {"status": "ok", "latency_ms": 21.0},
        {"status": "timeout"},
        {"status": "lost"},
    ])
    base_path = _write(workdir, "base-report.json", base)
    candidate_path = _write(workdir, "candidate-report.json", candidate)
    result = _run_cli("compare", str(base_path), str(candidate_path))
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["latency"]["base"]["attempts"] == 3
    assert report["latency"]["base"]["refusals"] == 1
    assert report["latency"]["candidate"]["attempts"] == 4
    assert report["latency"]["candidate"]["timeouts"] == 1
    assert report["latency"]["candidate"]["lost"] == 1
    assert report["latency"]["candidate"]["p99"] is None
    assert report["latency"]["clean"] == {"base": False, "candidate": False}
