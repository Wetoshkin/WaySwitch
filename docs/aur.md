# Публикация в AUR

Пакет `wayswitch` для Arch, CachyOS, EndeavourOS и Manjaro лежит в
`packaging/arch/`: `PKGBUILD`, `wayswitch.install` и генерируемый `.SRCINFO`.
В AUR пакет — это отдельный git-репозиторий
`ssh://aur@aur.archlinux.org/wayswitch.git`; первый push создаёт пакет.

Пока пакета в AUR нет, `get.sh` на Arch сам собирает WaySwitch из исходников.
Как только пакет появится, `get.sh` поставит его через `paru`/`yay`, если
один из них установлен, — но только если сопровождающий пакета в AUR —
`AUR_MAINTAINER` из `packaging/get.sh` (сейчас `Wetoshkin`). Имя пакета может
занять кто угодно, а чужой PKGBUILD собрался бы от root без просмотра.
Зарегистрируетесь под другим именем — поправьте `AUR_MAINTAINER`.

## Один раз: аккаунт и ключ

1. Зарегистрироваться на <https://aur.archlinux.org/register>. Регистрацию
   иногда временно закрывают из-за спама. Тогда остаётся ждать: AUR
   принимает пакеты только от зарегистрированных пользователей.
2. Создать отдельный SSH-ключ:
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/aur -C "aur wayswitch"
   ```
3. Вставить содержимое `~/.ssh/aur.pub` в «My Account → SSH Public Key».
4. Добавить в `~/.ssh/config`:
   ```
   Host aur.archlinux.org
       User aur
       IdentityFile ~/.ssh/aur
   ```
5. Проверить: `ssh -T aur@aur.archlinux.org help` — должен вывести список
   команд.

## На каждый релиз

1. Поднять `version` в `pyproject.toml` и `pkgver` в `PKGBUILD` (если версия та
   же, а меняется только упаковка — увеличить `pkgrel`). `release.yml`
   проверяет, что тег совпадает с обеими версиями.
2. Поставить тег `vX.Y.Z` и дождаться, пока GitHub соберёт релиз.
3. Обновить контрольную сумму и `.SRCINFO`:
   ```bash
   python tools/aur_update.py
   ```
   Скрипт скачивает архив тега с GitHub, вписывает `sha256sums` в `PKGBUILD`
   и собирает `.SRCINFO`. На Arch то же самое делают
   `updpkgsums && makepkg --printsrcinfo > .SRCINFO`.
4. Проверить сборку в чистом окружении (по желанию, но полезно; в Git Bash
   на Windows перед командой добавьте `MSYS_NO_PATHCONV=1`, иначе путь
   `/pkg` подменится):
   ```bash
   docker run --rm -v "$PWD/packaging/arch:/pkg" archlinux bash -c \
     'pacman -Syu --noconfirm base-devel python python-evdev python-gobject gtk4 libadwaita libxkbcommon \
      && useradd -m b && cp -r /pkg /home/b/p && chown -R b /home/b/p \
      && cd /home/b/p && su b -c "makepkg --noconfirm"'
   ```
5. Закоммитить `PKGBUILD` и `.SRCINFO` в этот репозиторий.
6. Опубликовать:
   ```bash
   bash tools/aur_publish.sh --dry-run   # показать, что уйдёт
   bash tools/aur_publish.sh
   ```
   Скрипт клонирует репозиторий пакета из AUR, кладёт туда три файла и
   делает push в `master`. Если версия уже опубликована, он ничего не делает.

Пользователи AUR получают обновление обычным `paru -Syu` / `yay -Syu`.

## Чего AUR не примет

- `sha256sums=('SKIP')` для архива релиза — `aur_publish.sh` сам откажется
  публиковать такой PKGBUILD.
- `.SRCINFO`, не совпадающий с `PKGBUILD`. Проверка:
  `python tools/aur_update.py --check`.
- Ветку, отличную от `master`.
