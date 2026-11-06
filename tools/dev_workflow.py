"""Small Git safeguards; heavy validation belongs to CI."""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

MARKER = "WaySwitch development guard"
PRIVATE_DIRS = {".superpowers", ".remember", ".codex", ".claude", ".ssh",
                "build", "dist", "reports", "research_notes"}
PRIVATE_SUFFIXES = {".qcow2", ".vmdk", ".vdi", ".vhdx", ".iso", ".pem", ".key"}
SENSITIVE = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"
    r"|\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{30,})"
    r"|\bAKIA[0-9A-Z]{16}\b|\bxox[baprs]-[A-Za-z0-9-]{20,}"
    r"|[A-Za-z]:[/\\]+Users[/\\]+[^/\\\s]+[/\\]"
    r"|/home/[A-Za-z0-9_.-]+/"
)


class Rejected(Exception):
    pass


def git(*args):
    result = subprocess.run(["git", *args], capture_output=True)
    if result.returncode:
        raise Rejected("Git command failed; check the reference and repository access")
    return result.stdout.decode("utf-8", errors="replace").strip()


def private_path(name):
    path = PurePosixPath(name)
    return (bool(PRIVATE_DIRS.intersection(path.parts))
            or name.startswith("docs/superpowers/")
            or path.name in {"AGENTS.md", "CLAUDE.md"}
            or path.name.startswith("CLAUDE.")
            or (path.name.startswith(".env") and path.name != ".env.example")
            or path.name.startswith("id_ed25519") or path.name.startswith("id_rsa")
            or path.suffix.lower() in PRIVATE_SUFFIXES)


def check_gate(head):
    workflow = git("show", f"{head}:.github/workflows/ci.yml")
    # This repository keeps job keys at two spaces and gate needs on one line.
    workflow = workflow.partition("\njobs:\n")[2]
    jobs = set(re.findall(r"^  ([a-zA-Z0-9_-]+):\s*$", workflow, re.M))
    gate = re.search(r"^  development-gate:\s*\n((?:[ \t].*\n?|\n)*)", workflow, re.M)
    needs = re.search(r"^    needs: \[([^]\n]+)\]$", gate[1], re.M) if gate else None
    covered = {name.strip() for name in needs[1].split(",")} if needs else set()
    if not jobs or covered != jobs - {"development-gate"}:
        raise Rejected("development gate must cover all jobs; keep gate needs as an inline list")


def check(base, head="HEAD"):
    git("rev-parse", "--verify", f"{base}^{{commit}}")
    git("rev-parse", "--verify", f"{head}^{{commit}}")
    for sha in git("rev-list", "--reverse", f"{base}..{head}").splitlines():
        # Compare merges to their first parent too; intermediate commits are checked separately.
        names = git("diff-tree", "--root", "-m", "--no-commit-id", "--name-only",
                    "--diff-filter=ACMRT", "-r", "-z", sha).split("\0")
        for name in names:
            if private_path(name):
                raise Rejected(f"{sha[:12]}: private or working file: {name}")
        patch = git("show", "--format=", "--first-parent", "--unified=0", sha)
        if any(SENSITIVE.search(line[1:]) for line in patch.splitlines()
               if line.startswith("+") and not line.startswith("+++")):
            raise Rejected(f"{sha[:12]}: sensitive addition; inspect locally (content withheld)")
        result = subprocess.run(["git", "show", "--format=", "--check", sha], capture_output=True)
        if result.returncode:
            raise Rejected(f"{sha[:12]}: whitespace errors; inspect git show --check locally")
    check_gate(head)
    print("Outgoing history and CI gate checked")


def fetch_main(remote="origin"):
    if remote not in git("remote").splitlines():
        raise Rejected("Use a configured remote so its main branch can be checked")
    ref = f"refs/remotes/{remote}/main"
    git("fetch", "--no-tags", remote, f"+refs/heads/main:{ref}")
    return ref


def is_ancestor(base, head):
    result = subprocess.run(["git", "merge-base", "--is-ancestor", base, head], capture_output=True)
    if result.returncode not in (0, 1):
        raise Rejected("Could not compare branch ancestry")
    return result.returncode == 0


def preflight():
    base = fetch_main()
    head = git("rev-parse", "HEAD")
    print(f"Branch: {git('branch', '--show-current') or '(detached)'}")
    print(f"Fresh origin/main: {git('rev-parse', base)}")
    print(f"Uncommitted entries: {len(git('status', '--porcelain').splitlines())}")
    if head != git("rev-parse", base) and is_ancestor(head, base):
        print("This branch is already merged; start the next task from fresh origin/main")
    elif not is_ancestor(base, head):
        print("Main has advanced; integrate it in the task worktree before pushing")


def pre_push(remote):
    updates = [line.split() for line in sys.stdin if line.strip()]
    if any(len(row) != 4 for row in updates):
        raise Rejected("Unexpected pre-push input")
    if any(row[2] == "refs/heads/main" for row in updates):
        raise Rejected("No direct push to main; use a reviewed pull request with green CI")
    updates = [row for row in updates if set(row[1]) != {"0"}]
    if not updates:
        return
    if git("status", "--porcelain"):
        raise Rejected("Uncommitted files; review and commit intended changes before pushing")
    base = fetch_main(remote)
    for _local_ref, sha, _remote_ref, _remote_sha in updates:
        if not is_ancestor(base, sha):
            raise Rejected("Main has advanced; integrate fresh main in the task worktree first")
        check(base, sha)


def install_hook():
    configured = subprocess.run(["git", "config", "--get", "core.hooksPath"], capture_output=True)
    if configured.returncode == 0:
        raise Rejected("Existing core.hooksPath is configured; integrate the guard manually")
    common = Path(git("rev-parse", "--git-common-dir")).resolve()
    hooks = common / "hooks"
    hook = hooks / "pre-push"
    if hook.exists() and MARKER not in hook.read_text(encoding="utf-8", errors="replace"):
        raise Rejected("Preserving existing pre-push; integrate the guard manually")
    hooks.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).resolve()
    target = hooks / "wayswitch-dev-workflow.py"
    if script != target:
        shutil.copyfile(script, target)
    content = ("#!/bin/sh\n# " + MARKER + "\n"
               'if command -v python >/dev/null 2>&1; then py=python; else py=python3; fi\n'
               'exec "$py" -I "$(dirname "$0")/wayswitch-dev-workflow.py" pre-push "$@"\n')
    hook.write_bytes(content.encode())
    hook.chmod(hook.stat().st_mode | 0o111)
    print("Installed shared pre-push guard for this repository and its worktrees")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("preflight")
    commands.add_parser("install-hook")
    checking = commands.add_parser("check")
    checking.add_argument("--base", required=True)
    checking.add_argument("--head", default="HEAD")
    pushing = commands.add_parser("pre-push")
    pushing.add_argument("remote")
    pushing.add_argument("location")
    args = parser.parse_args()
    try:
        if args.command == "check":
            check(args.base, args.head)
        elif args.command == "preflight":
            preflight()
        elif args.command == "pre-push":
            pre_push(args.remote)
        else:
            install_hook()
    except (Rejected, OSError) as exc:
        print(f"Development guard: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
