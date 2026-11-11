"""Verify locale contracts through the installed CLI, without a running daemon."""

from __future__ import annotations

import json
import os
import subprocess
import sys


def main() -> None:
    executable = sys.argv[1] if len(sys.argv) > 1 else "wayswitch"
    machine = []
    for locale, russian in (("ru_RU.UTF-8", True), ("en_US.UTF-8", False)):
        env = {**os.environ, "LC_ALL": locale, "LANGUAGE": "ru" if russian else "en"}

        def run(*args, codes=(0,), environment=env):
            result = subprocess.run([executable, *args], env=environment, capture_output=True,
                                    text=True, encoding="utf-8", timeout=60)
            assert result.returncode in codes, (args, result.returncode, result.stderr)
            assert "Traceback" not in result.stdout + result.stderr
            return result

        help_text = run("--help").stdout
        assert ("Исправление раскладки" if russian else "Keyboard layout correction") in help_text
        report = run("doctor", codes=(0, 1)).stdout
        assert ("Распознавание программ и полей пароля" if russian else
                "Application and password field recognition") in report
        explanation = run("explain", "ghbdtn").stdout
        assert ("Слово: ghbdtn" if russian else "Word: ghbdtn") in explanation
        assert "reason=dict-b" in explanation
        invalid = run("doctor", "--delay", "-1", codes=(2,))
        assert ("ошибка:" if russian else "error:") in invalid.stderr
        machine.append((run("version").stdout,
                        json.loads(run("explain", "ghbdtn", "--json").stdout),
                        json.loads(run("status", "--json", codes=(0, 2)).stdout)))
    assert machine[0] == machine[1]
    print("Installed CLI: ru/en help, doctor, explain, errors and machine contracts passed")


if __name__ == "__main__":
    main()
