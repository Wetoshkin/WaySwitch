#!/bin/bash
# Local build only. Install GNOME SDK 50 and flatpak-builder first.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/build/flatpak}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
if command -v flatpak-builder >/dev/null; then
    BUILDER=(flatpak-builder)
else
    BUILDER=(flatpak run org.flatpak.Builder)
fi
"${BUILDER[@]}" --user --force-clean --repo="$OUT/repo" "$OUT/app" \
    "$ROOT/packaging/flatpak/ru.siberia.WaySwitch.Gui.json"
flatpak build-bundle --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo \
    "$OUT/repo" "$OUT/WaySwitch-Settings.flatpak" ru.siberia.WaySwitch.Gui
(cd "$OUT" && sha256sum WaySwitch-Settings.flatpak > SHA256SUMS)
