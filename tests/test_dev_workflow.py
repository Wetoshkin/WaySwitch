"""Development checks run against real Git commits, including intermediate history."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "dev_workflow.py"


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True,
                                   encoding="utf-8", stderr=subprocess.DEVNULL).strip()


def commit(repo, name, text="sample\n"):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(repo, "add", "--", name)
    git(repo, "commit", "-qm", "test: проверка рабочего процесса")


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Example")
    git(repo, "config", "user.email", "tests@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")
    commit(repo, ".github/workflows/ci.yml", "name: ci\non: [push, pull_request]\njobs:\n"
           "  development-policy:\n    runs-on: ubuntu-latest\n    steps: []\n"
           "  test:\n    runs-on: ubuntu-latest\n    steps: []\n"
           "  development-gate:\n    needs: [development-policy, test]\n"
           "    runs-on: ubuntu-latest\n    steps: []\n")
    return repo, git(repo, "rev-parse", "HEAD")


def invoke(repo, *args, stdin=None):
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([sys.executable, "-I", str(TOOL), *args], cwd=repo, env=env,
                          input=stdin, capture_output=True, text=True, encoding="utf-8")


def test_clean_change_passes(repository):
    repo, base = repository
    commit(repo, "normal.py", "value = 1\n")
    result = invoke(repo, "check", "--base", base)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", ["AGENTS.md", ".env", ".ssh/id_ed25519",
                                  ".superpowers/report.md", "reports/raw.txt",
                                  "docs/superpowers/plan.md", "test.qcow2"])
def test_private_and_working_files_are_rejected(repository, name):
    repo, base = repository
    commit(repo, name)
    result = invoke(repo, "check", "--base", base)
    assert result.returncode == 1
    assert "private or working file" in result.stderr


def test_removed_private_file_is_still_rejected_in_outgoing_history(repository):
    repo, base = repository
    commit(repo, ".env")
    git(repo, "rm", "-q", ".env")
    git(repo, "commit", "-qm", "test: удаление образца")
    result = invoke(repo, "check", "--base", base)
    assert result.returncode == 1
    assert "private or working file" in result.stderr


def test_token_is_rejected_without_printing_it(repository):
    repo, base = repository
    token = "ghp_" + "x" * 36
    commit(repo, "normal.py", "token = '" + token + "'\n")
    result = invoke(repo, "check", "--base", base)
    assert result.returncode == 1 and "sensitive addition" in result.stderr
    assert token not in result.stdout + result.stderr


def test_personal_path_is_rejected(repository):
    repo, base = repository
    personal = "C:" + "/Users/" + "example-user/private.txt"
    commit(repo, "normal.py", "path = '" + personal + "'\n")
    result = invoke(repo, "check", "--base", base)
    assert result.returncode == 1 and "sensitive addition" in result.stderr
    assert personal not in result.stdout + result.stderr


def test_new_ci_job_cannot_bypass_final_gate(repository):
    repo, base = repository
    path = repo / ".github/workflows/ci.yml"
    text = path.read_text(encoding="utf-8")
    commit(repo, ".github/workflows/ci.yml", text + "  new-job:\n    steps: []\n")
    result = invoke(repo, "check", "--base", base)
    assert result.returncode == 1 and "gate must cover all jobs" in result.stderr


def test_direct_main_push_is_rejected_before_network_access(repository):
    repo, _base = repository
    sha = git(repo, "rev-parse", "HEAD")
    result = invoke(repo, "pre-push", "origin", "unused",
                    stdin=f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n")
    assert result.returncode == 1 and "direct push to main" in result.stderr


def test_hook_installation_is_idempotent(repository):
    repo, _base = repository
    assert invoke(repo, "install-hook").returncode == 0
    hook = repo / ".git/hooks/pre-push"
    installed = hook.read_bytes()
    assert b"WaySwitch development guard" in installed
    assert invoke(repo, "install-hook").returncode == 0
    assert hook.read_bytes() == installed


def test_existing_custom_hook_is_preserved(repository):
    repo, _base = repository
    hook = repo / ".git/hooks/pre-push"
    original = b"#!/bin/sh\nexit 0\n"
    hook.write_bytes(original)
    result = invoke(repo, "install-hook")
    assert result.returncode == 1 and "existing pre-push" in result.stderr
    assert hook.read_bytes() == original


def test_real_installed_hook_allows_feature_push_and_blocks_main(repository, tmp_path):
    repo, _base = repository
    remote = tmp_path / "remote.git"
    git(repo, "init", "--bare", "-q", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "origin", "main")
    git(repo, "switch", "-qc", "codex/example")
    commit(repo, "normal.py", "value = 1\n")
    assert invoke(repo, "install-hook").returncode == 0
    git(repo, "push", "-q", "origin", "HEAD:refs/heads/codex/example")
    assert git(repo, "ls-remote", "origin", "refs/heads/codex/example")
    result = subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=repo,
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode != 0 and "direct push to main" in result.stderr


def test_stale_branch_is_rejected(repository, tmp_path):
    repo, base = repository
    remote = tmp_path / "remote.git"
    git(repo, "init", "--bare", "-q", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    commit(repo, "new-main.py", "value = 1\n")
    git(repo, "push", "-q", "origin", "main")
    git(repo, "switch", "-qc", "codex/old-task", base)
    commit(repo, "normal.py", "value = 2\n")
    sha = git(repo, "rev-parse", "HEAD")
    result = invoke(repo, "pre-push", "origin", str(remote),
                    stdin=f"refs/heads/codex/old-task {sha} refs/heads/codex/old-task {'0' * 40}\n")
    assert result.returncode == 1 and "Main has advanced" in result.stderr


def test_checker_source_can_be_published_without_false_positive(repository):
    repo, base = repository
    commit(repo, "checker.py", TOOL.read_text(encoding="utf-8"))
    result = invoke(repo, "check", "--base", base)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("private", [False, True])
def test_missing_previous_tip_uses_available_base_and_checks_new_history(repository, private):
    repo, base = repository
    git(repo, "update-ref", "refs/remotes/origin/main", base)
    commit(repo, ".env" if private else "normal.py", "value = 1\n")
    result = invoke(repo, "check", "--base", "f" * 40, "--fallback-base", "origin/main")
    assert result.returncode == (1 if private else 0), result.stderr
    if private:
        assert "private or working file" in result.stderr
