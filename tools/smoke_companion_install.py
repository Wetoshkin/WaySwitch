"""CI-only first installation: real apt transaction through the handoff transport."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from wayswitch import __version__, companion


def main() -> None:
    assert os.environ.get("GITHUB_ACTIONS") == "true", "Run only on the disposable CI host"
    info = companion.check(__version__, os_release={"ID": "ubuntu"}, atomic=False)
    assert not info.installed and info.automatic and info.candidate, info
    processes = []
    original_popen = subprocess.Popen
    original_terminal = companion.terminal.terminal_argv

    def start(*args, **kwargs):
        kwargs.update(stdin=subprocess.PIPE, stdout=None, stderr=None)
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    # CI has no graphical terminal; only its transport is substituted, not apt.
    companion.terminal.terminal_argv = lambda script, **kw: ["sh", "-c", script]
    companion.subprocess.Popen = start
    try:
        assert companion.handoff(info, confirmed=True)
        assert len(processes) == 1
        processes[0].communicate(input=b"y\n\n", timeout=180)
    finally:
        companion.subprocess.Popen = original_popen
        companion.terminal.terminal_argv = original_terminal
    version = subprocess.check_output(["dpkg-query", "-W", "-f=${Version}", "wayswitch"],
                                      text=True).strip()
    assert version == info.candidate, (version, info.candidate)
    for name in ("/usr/lib/udev/rules.d/60-wayswitch.rules",
                 "/usr/lib/systemd/user/wayswitch.service",
                 "/usr/share/gnome-shell/extensions/wayswitch@wetoshkin.github.io/metadata.json"):
        assert Path(name).is_file(), name
    print("Native handoff: confirmed first apt installation and packaged integration files")


if __name__ == "__main__":
    main()
