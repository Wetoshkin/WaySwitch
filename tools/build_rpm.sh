#!/bin/bash
# Сборка wayswitch-<версия>.noarch.rpm. Нужен rpmbuild (Fedora: rpm-build,
# Debian/Ubuntu: пакет rpm). Запускать из любого места.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY_VERSION="$(grep -m1 '^version = ' "$ROOT/pyproject.toml" | sed -E 's/^version = "([^"]+)"/\1/')"
# rpm сортирует «~» раньше всего: 2.0.0~a2 < 2.0.0 — как и нужно предрелизу.
RPM_VERSION="$(python3 "$ROOT/tools/debversion.py" "$PY_VERSION")"
TOP="$ROOT/build/rpm"
rm -rf "$TOP"
mkdir -p "$TOP"/{BUILD,RPMS,SOURCES,SPECS,SRPMS} "$ROOT/dist"

echo "== Версия: python=$PY_VERSION rpm=$RPM_VERSION"
rpmbuild -bb "$ROOT/packaging/rpm/wayswitch.spec" \
    --define "_topdir $TOP" \
    --define "ws_version $RPM_VERSION" \
    --define "ws_src $ROOT" \
    --define "dist %{nil}"
find "$TOP/RPMS" -name '*.rpm' -exec cp {} "$ROOT/dist/" \;
ls -l "$ROOT"/dist/*.rpm
