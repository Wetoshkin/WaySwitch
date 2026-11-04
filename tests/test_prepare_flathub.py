import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def preparer():
    spec = importlib.util.spec_from_file_location("prepare_flathub",
                                                 ROOT / "tools/prepare_flathub.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_flathub_manifest_pins_code_and_copies_flattened_support_files():
    manifest = json.loads((ROOT / "packaging/flatpak/ru.siberia.WaySwitch.Gui.json")
                          .read_text(encoding="utf-8"))
    result = preparer().submission_manifest(manifest, "a" * 40)
    sources = result["modules"][0]["sources"]
    assert sources[0] == {"type": "git", "url": "https://github.com/Wetoshkin/WaySwitch.git",
                           "commit": "a" * 40, "dest": "upstream"}
    assert all(".." not in source["path"] for source in sources[1:])
    assert result["modules"][0]["build-commands"][0] == "cp -r upstream/wayswitch ."


@pytest.mark.parametrize("commit", ["main", "v1.0.0", "abcdef", "x" * 40])
def test_flathub_manifest_rejects_moving_or_invalid_source(commit):
    with pytest.raises(ValueError, match="40"):
        preparer().submission_manifest({}, commit)
