import io
import tarfile

import pytest

from tools import aur_update


@pytest.mark.parametrize("source_version", ["1.0.0", "1.1.0"])
def test_update_checks_pinned_source_version_before_writing(tmp_path, monkeypatch, source_version):
    project = tmp_path / "pyproject.toml"
    project.write_text('version = "1.1.0"\n', encoding="utf-8")
    pkgbuild = tmp_path / "PKGBUILD"
    original = ('pkgname=wayswitch\npkgver=1.0.0\n'
                'source=("https://example.invalid/pinned.tar.gz")\nsha256sums=("old")\n')
    pkgbuild.write_text(original, encoding="utf-8")
    srcinfo = tmp_path / ".SRCINFO"
    srcinfo.write_text("unchanged\n", encoding="utf-8")
    data = f'[project]\nversion = "{source_version}"\n'.encode()
    download = io.BytesIO()
    with tarfile.open(fileobj=download, mode="w:gz") as archive:
        member = tarfile.TarInfo("WaySwitch-commit/pyproject.toml")
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))
    monkeypatch.setattr(aur_update, "ROOT", tmp_path)
    monkeypatch.setattr(aur_update, "PKGBUILD", pkgbuild)
    monkeypatch.setattr(aur_update, "SRCINFO", srcinfo)
    monkeypatch.setattr(aur_update.urllib.request, "urlopen",
                        lambda *_a, **_kw: io.BytesIO(download.getvalue()))
    if source_version != "1.1.0":
        with pytest.raises(ValueError, match="_commit"):
            aur_update.main([])
        assert pkgbuild.read_text(encoding="utf-8") == original
        assert srcinfo.read_text(encoding="utf-8") == "unchanged\n"
    else:
        assert aur_update.main([]) == 0
        assert "pkgver=1.1.0" in pkgbuild.read_text(encoding="utf-8")
        assert "\tpkgver = 1.1.0\n" in srcinfo.read_text(encoding="utf-8")
