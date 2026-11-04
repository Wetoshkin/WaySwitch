"""Prepare local Flathub submission files from an already published source commit."""

from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "packaging/flatpak/ru.siberia.WaySwitch.Gui.json"


def submission_manifest(manifest: dict, commit: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Use the full 40-character lowercase Git commit, not a branch or tag")
    result = copy.deepcopy(manifest)
    module = result["modules"][0]
    module["sources"][0] = {"type": "git", "url": "https://github.com/Wetoshkin/WaySwitch.git",
                             "commit": commit, "dest": "upstream"}
    for source in module["sources"][1:]:
        source["path"] = Path(source["path"]).name
    module["build-commands"].insert(0, "cp -r upstream/wayswitch .")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("commit", help="Full published commit with the portable implementation")
    parser.add_argument("output", type=Path, help="A new directory; never an existing checkout")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.commit):
        parser.error("commit must be 40 lowercase hexadecimal characters")

    def read(path: str) -> bytes:
        return subprocess.check_output(["git", "show", f"{args.commit}:{path}"], cwd=ROOT)

    # Refuse old releases that do not contain the companion-aware client.
    read("wayswitch/gui/portable.py")
    manifest = json.loads(read(MANIFEST))
    files = {}
    for source in manifest["modules"][0]["sources"][1:]:
        path = (ROOT / "packaging/flatpak" / source["path"]).resolve().relative_to(ROOT)
        files[path.name] = read(path.as_posix())
    result = submission_manifest(manifest, args.commit)
    args.output.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        (args.output / name).write_bytes(data)
    (args.output / Path(MANIFEST).name).write_text(json.dumps(result, indent=2) + "\n",
                                                 encoding="utf-8")
    print(f"Prepared {args.output}. Verify the commit is public before submitting to Flathub.")


if __name__ == "__main__":
    main()
