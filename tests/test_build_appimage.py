"""Integrity of build inputs and intentional source staging."""

import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def builder():
    spec = importlib.util.spec_from_file_location("build_appimage",
                                                 ROOT / "tools/build_appimage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verified_download_rejects_corrupted_cache(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"verified artifact")
    cache = tmp_path / "cache"
    cache.write_bytes(b"corrupt")
    builder().fetch_verified(source.as_uri(), cache,
                             hashlib.sha256(source.read_bytes()).hexdigest())
    assert cache.read_bytes() == b"verified artifact"


def test_verified_download_rejects_wrong_digest(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"truncated")
    target = tmp_path / "cache"
    with pytest.raises(ValueError, match="SHA256"):
        builder().fetch_verified(source.as_uri(), target, "0" * 64)
    assert not target.exists()


def test_common_licenses_have_a_verified_source():
    lock = json.loads((ROOT / "packaging/appimage/dependencies.json").read_text())
    source = lock["common_licenses"]
    assert source["file"].startswith("base-files_")
    assert source["url"].startswith("https://") and len(source["sha256"]) == 64


def test_ubuntu_inputs_use_a_single_immutable_snapshot():
    lock = json.loads((ROOT / "packaging/appimage/dependencies.json").read_text())
    snapshots = set()
    for package in [*lock["packages"], lock["common_licenses"]]:
        match = re.fullmatch(
            r"https://snapshot\.ubuntu\.com/ubuntu/(\d{8}T\d{6}Z)/pool/.+\.deb",
            package["url"],
        )
        assert match, package["file"]
        snapshots.add(match[1])
    assert len(snapshots) == 1
