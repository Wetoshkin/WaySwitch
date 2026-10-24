#!/bin/bash
# Публикация пакета wayswitch в AUR (Arch User Repository).
#
#   bash tools/aur_publish.sh            — опубликовать текущую версию
#   bash tools/aur_publish.sh --dry-run  — показать, что уйдёт, без push
#
# Нужно один раз: аккаунт на https://aur.archlinux.org и ваш публичный
# SSH-ключ в «My Account → SSH Public Key» (подробно — docs/aur.md).
# Пакет в AUR — это git-репозиторий ssh://aur@aur.archlinux.org/wayswitch.git
# с двумя обязательными файлами: PKGBUILD и .SRCINFO. Первый push создаёт пакет.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/packaging/arch"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

ver=$(sed -n 's/^pkgver=//p' "$SRC/PKGBUILD")
rel=$(sed -n 's/^pkgrel=//p' "$SRC/PKGBUILD")
grep -q "pkgver = $ver" "$SRC/.SRCINFO" || {
    echo ".SRCINFO не соответствует PKGBUILD ($ver): пересоберите его (docs/aur.md)" >&2
    exit 1
}
if grep -q "sha256sums=('SKIP')" "$SRC/PKGBUILD"; then
    echo "В PKGBUILD нет контрольной суммы архива — AUR такое не примет без причины." >&2
    exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
git clone -q ssh://aur@aur.archlinux.org/wayswitch.git "$WORK/aur" 2>/dev/null || {
    echo "Нет доступа к AUR по SSH. Проверьте: ssh -T aur@aur.archlinux.org help" >&2
    exit 1
}
cp "$SRC/PKGBUILD" "$SRC/.SRCINFO" "$SRC/wayswitch.install" "$WORK/aur/"
cd "$WORK/aur"
git add PKGBUILD .SRCINFO wayswitch.install
if git diff --cached --quiet; then
    echo "В AUR уже версия $ver-$rel — публиковать нечего."
    exit 0
fi
git -c user.name="$(git -C "$ROOT" config user.name)" \
    -c user.email="$(git -C "$ROOT" config user.email)" \
    commit -q -m "wayswitch $ver-$rel"
git show --stat HEAD | cat
if [ "$DRY" = 1 ]; then
    echo "(--dry-run: push не выполнен)"
    exit 0
fi
git push -q origin HEAD:master
echo "Опубликовано: https://aur.archlinux.org/packages/wayswitch"
