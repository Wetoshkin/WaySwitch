#!/bin/sh
# WaySwitch — установка одной командой для любого дистрибутива:
#   curl -fsSL https://raw.githubusercontent.com/Wetoshkin/WaySwitch/main/packaging/get.sh | sh
# Удаление:
#   curl -fsSL https://raw.githubusercontent.com/Wetoshkin/WaySwitch/main/packaging/get.sh | sh -s -- --uninstall
#
# Запускать от обычного пользователя, не через sudo: пароль спросят только на
# шаге установки пакета. Без подтверждения ничего не ставится (--yes —
# не спрашивать). Повторный запуск обновляет WaySwitch до последней версии.
# Для проверки на своих исходниках: WAYSWITCH_SRC=/путь/к/WaySwitch.
#
# Скрипт — POSIX sh (в Debian и Ubuntu это dash): без bash-конструкций.

set -eu

REPO="Wetoshkin/WaySwitch"
API="https://api.github.com/repos/$REPO/releases?per_page=1"   # включает альфа-релизы, в отличие от /latest
SOURCE_UNINSTALL=/usr/local/share/wayswitch/uninstall.sh       # кладёт packaging/install.sh
MODE="install"
YES=0
for arg in "$@"; do
  case "$arg" in
    --uninstall) MODE="uninstall" ;;
    --yes|-y)    YES=1 ;;
  esac
done

case "${LANG:-}${LC_ALL:-}" in ru*|*ru_*) RU=1 ;; *) RU=0 ;; esac
say()  { if [ "$RU" = 1 ]; then printf '%s\n' "$1"; else printf '%s\n' "$2"; fi; }
step() { printf '\n\033[1m==> '; say "$1" "$2"; printf '\033[0m'; }
warn() { printf '\033[33m! '; say "$1" "$2"; printf '\033[0m'; }
die()  { { printf '\033[31m✗ '; say "$1" "$2"; printf '\033[0m'; } >&2; exit 1; }
ask()  { # вопрос да/нет; читаем с терминала, потому что stdin занят самим скриптом
  [ "$YES" = 1 ] && return 0
  # /dev/tty есть всегда, но без управляющего терминала (cron, контейнер)
  # не открывается — проверяем открытием, а не наличием.
  # Подоболочка: в dash ошибка перенаправления у «:» завершила бы весь скрипт.
  if ! (true </dev/tty) 2>/dev/null; then
    die "Нет терминала, чтобы спросить подтверждение. Запустите с --yes, если уверены." \
        "No terminal to ask for confirmation. Run with --yes if you are sure."
  fi
  printf '%s ' "$( [ "$RU" = 1 ] && echo "$1 [Д/н]" || echo "$2 [Y/n]" )"
  read -r a </dev/tty || a="н"
  # Шаблоны-префиксы: в dash [Дд] сравнивается побайтно, и «н» сошло бы за «да».
  case "$a" in ""|Y*|y*|Д*|д*) return 0 ;; *) return 1 ;; esac
}
have() { command -v "$1" >/dev/null 2>&1; }

[ "$(id -u)" = 0 ] && die "Запустите без sudo — пароль спросят, когда он понадобится." \
                          "Run without sudo — you will be asked for a password when needed."
[ -r /etc/os-release ] || die "Не удалось определить дистрибутив (/etc/os-release)." \
                              "Cannot detect the distribution (/etc/os-release)."
. /etc/os-release
ID_ALL=" ${ID:-} ${ID_LIKE:-} "
ATOMIC=0; [ -e /run/ostree-booted ] && ATOMIC=1
have systemctl || die "WaySwitch работает как служба systemd, а systemd в этой системе нет." \
                      "WaySwitch runs as a systemd service, and this system has no systemd."

FAMILY=source
case "$ID_ALL" in
  *" debian "*|*" ubuntu "*)                                   FAMILY=deb ;;
  *" fedora "*|*" rhel "*)                                     FAMILY=fedora ;;
  *" opensuse"*|*" suse "*|*" sles "*)                         FAMILY=suse ;;
  *" arch "*|*" cachyos "*|*" endeavouros "*|*" manjaro "*)    FAMILY=arch ;;
esac
# Свои исходники (проверка перед релизом) — всегда установка из исходников.
[ -n "${WAYSWITCH_SRC:-}" ] && FAMILY=source

# Права 755: apt читает пакет от имени своего пользователя и иначе пугает
# предупреждением «Download is performed unsandboxed as root».
TMP=$(mktemp -d); chmod 755 "$TMP"; trap 'rm -rf "$TMP"' EXIT

need_sudo() {
  have sudo || die "Нужна команда sudo. Войдите как root (su -), установите sudo и добавьте себя в группу sudo (usermod -aG sudo $(id -un)), затем выйдите из системы и войдите снова." \
                   "sudo is required. Become root (su -), install sudo and add yourself to the sudo group (usermod -aG sudo $(id -un)), then log out and back in."
  say "  Сейчас спросят пароль вашей учётной записи (при вводе символы не видны — это нормально)." \
      "  You will be asked for your account password (it is not shown while you type — that's normal)."
  sudo -v || die "sudo не сработал: ваша учётная запись не может выполнять команды администратора. Добавьте её в группу sudo (или wheel) от имени root и войдите снова." \
                 "sudo failed: your account cannot run admin commands. Add it to the sudo (or wheel) group as root and log in again."
}
fetch() { # скачать пакет релиза во временную папку, напечатать путь
  # Код возврата: 0 — скачан, 2 — в последнем релизе нет такого пакета,
  # 1 — GitHub недоступен.
  api=$(curl -fsSL "$API") || return 1
  url=$(printf '%s' "$api" | grep -o "https://[^\"]*\.$1\"" | tr -d '"' | head -n1)
  [ -n "$url" ] || return 2
  curl -fL --progress-bar -o "$TMP/${url##*/}" "$url" || return 1
  printf '%s' "$TMP/${url##*/}"
}
get_package() { # $1 = deb | rpm; путь в $PKG, пусто — ставить из исходников
  PKG=$(fetch "$1") && rc=0 || rc=$?
  case "$rc" in
    0) ;;
    2) PKG=""
       [ "$ATOMIC" = 1 ] && die "В последнем релизе нет пакета .$1 — на этой системе без него не поставить. Попробуйте позже."                                 "The latest release has no .$1 package; this system cannot install without it. Try again later."
       warn "В последнем релизе нет пакета .$1 — ставлю из исходников."             "The latest release has no .$1 package — installing from source." ;;
    *) die "Не удалось связаться с GitHub. Проверьте интернет и запустите команду снова."            "Could not reach GitHub. Check your internet connection and run the command again." ;;
  esac
}
from_source() { # $1 = install.sh | uninstall.sh
  if [ -n "${WAYSWITCH_SRC:-}" ]; then
    sudo bash "$WAYSWITCH_SRC/packaging/$1"
    return
  fi
  have git || {
    case "$FAMILY" in
      arch) sudo pacman -S --needed --noconfirm git ;;
      *)    die "Нужен git. Установите его менеджером пакетов и запустите снова." \
                "git is required. Install it with your package manager and run again." ;;
    esac
  }
  git clone -q --depth 1 "https://github.com/$REPO.git" "$TMP/src" \
    || die "Не удалось скачать исходники с GitHub (нет сети?)." \
           "Could not download the source from GitHub (no network?)."
  sudo bash "$TMP/src/packaging/$1"
}
TRAY_AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/ru.siberia.WaySwitch.Settings.desktop"
drop_source_copy() { # копия из исходников мешает пакету: её служба и расширение перекрывают пакетные
  if [ -f "$SOURCE_UNINSTALL" ]; then
    say "  Убираю прежнюю установку из исходников (/usr/local)…" \
        "  Removing the previous from-source installation (/usr/local)…"
    # Значок при входе сохраняем сами: старые копии uninstall.sh его удаляли.
    tray=""; [ -f "$TRAY_AUTOSTART" ] && tray=$(cat "$TRAY_AUTOSTART")
    sudo WAYSWITCH_MIGRATE=1 bash "$SOURCE_UNINSTALL" >/dev/null
    if [ -n "$tray" ] && [ ! -f "$TRAY_AUTOSTART" ]; then
      printf '%s\n' "$tray" > "$TRAY_AUTOSTART"
    fi
  fi
}
aur_helper() { for h in paru yay; do have "$h" && { echo "$h"; return; }; done; }
# Ставим из AUR, только если пакет wayswitch там ведёт автор: имя пакета может
# занять кто угодно, а paru --noconfirm собрал бы чужой PKGBUILD от root.
AUR_MAINTAINER=Wetoshkin
aur_is_ours() {
  have curl || return 1
  curl -fsSL "https://aur.archlinux.org/rpc/v5/info?arg[]=wayswitch" 2>/dev/null \
    | grep -q "\"Maintainer\":\"$AUR_MAINTAINER\""
}

enable_extension() { # $1 = uuid; GNOME: включить расширение сразу (подхватится после входа)
  case "${XDG_CURRENT_DESKTOP:-}" in *GNOME*) ;; *) return 0 ;; esac
  gnome-extensions enable "$1" 2>/dev/null && return 0
  have gsettings || return 0
  cur=$(gsettings get org.gnome.shell enabled-extensions 2>/dev/null || echo "@as []")
  case "$cur" in
    *"$1"*) ;;
    "@as []"|"[]") gsettings set org.gnome.shell enabled-extensions "['$1']" 2>/dev/null ;;
    *) gsettings set org.gnome.shell enabled-extensions "${cur%]}, '$1']" 2>/dev/null ;;
  esac
}
A11Y_PREV="${XDG_CONFIG_HOME:-$HOME/.config}/wayswitch/a11y-prev"
enable_a11y() { # мост доступности: без него не работают защита паролей и исключения программ
  have gsettings || return 0
  # Установка из исходников сохраняет прежнее значение сама (install.sh).
  [ -f "/var/lib/wayswitch/a11y-prev-$(id -un)" ] && return 0
  # Уже включали раньше — при обновлении не перебиваем то, что человек мог
  # выключить сам.
  [ -f "$A11Y_PREV" ] && return 0
  key="org.gnome.desktop.interface toolkit-accessibility"
  prev=$(gsettings get $key 2>/dev/null) || return 0
  mkdir -p "$(dirname "$A11Y_PREV")" && printf '%s\n' "$prev" > "$A11Y_PREV"
  gsettings set $key true 2>/dev/null || true
}
restore_a11y() {
  [ -f "$A11Y_PREV" ] && have gsettings || return 0
  prev=$(cat "$A11Y_PREV")
  case "$prev" in true|false) gsettings set org.gnome.desktop.interface toolkit-accessibility "$prev" 2>/dev/null || true ;; esac
  rm -f "$A11Y_PREV"
}
check_adwaita_deb() { # окну нужна libadwaita 1.5+ (Ubuntu 24.04+, Debian 13+)
  have apt-cache || return 0
  v=$(apt-cache policy gir1.2-adw-1 2>/dev/null | awk '/Candidate:/ {print $2}')
  if [ -z "$v" ] || [ "$v" = "(none)" ] || ! dpkg --compare-versions "$v" ge 1.5; then
    die "Эта версия системы слишком старая: WaySwitch нужны Ubuntu 24.04 или новее (Linux Mint 22+, Debian 13+)." \
        "This system is too old: WaySwitch needs Ubuntu 24.04 or newer (Linux Mint 22+, Debian 13+)."
  fi
}

# --- Удаление ----------------------------------------------------------------
if [ "$MODE" = uninstall ]; then
  step "Удаляю WaySwitch (${PRETTY_NAME:-$ID})" "Removing WaySwitch (${PRETTY_NAME:-$ID})"
  ask "Удалить WaySwitch?" "Remove WaySwitch?" || exit 0
  need_sudo
  systemctl --user disable --now wayswitch 2>/dev/null || true
  if   have dpkg   && dpkg -s wayswitch >/dev/null 2>&1; then sudo apt purge -y wayswitch
  elif [ "$ATOMIC" = 1 ] && rpm -q wayswitch >/dev/null 2>&1; then sudo rpm-ostree uninstall wayswitch
  elif have rpm    && rpm -q wayswitch >/dev/null 2>&1;  then
       if have dnf; then sudo dnf remove -y wayswitch; else sudo zypper --non-interactive remove wayswitch; fi
  elif have pacman && pacman -Q wayswitch >/dev/null 2>&1; then sudo pacman -Rns --noconfirm wayswitch
  fi
  if [ -f "$SOURCE_UNINSTALL" ]; then sudo bash "$SOURCE_UNINSTALL"
  elif [ -n "${WAYSWITCH_SRC:-}" ]; then from_source uninstall.sh
  fi
  rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/ru.siberia.WaySwitch.Settings.desktop" \
        "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/ru.siberia.WaySwitch.desktop"
  restore_a11y
  say "Готово. Настройки остались в ~/.config/wayswitch — удалите папку, если они не нужны." \
      "Done. Settings are kept in ~/.config/wayswitch — delete it if you don't need them."
  exit 0
fi

# --- Проверка окружения --------------------------------------------------------
step "Проверяю систему" "Checking the system"
say  "  Дистрибутив: ${PRETTY_NAME:-$ID}" "  Distribution: ${PRETTY_NAME:-$ID}"
case "${XDG_CURRENT_DESKTOP:-}" in
  *GNOME*)    say "  Рабочий стол: GNOME" "  Desktop: GNOME" ;;
  *KDE*)      say "  Рабочий стол: KDE Plasma" "  Desktop: KDE Plasma" ;;
  *Hyprland*) say "  Композитор: Hyprland" "  Compositor: Hyprland" ;;
  *sway*|*Sway*) say "  Композитор: Sway" "  Compositor: Sway" ;;
  *XFCE*)     say "  Рабочий стол: Xfce" "  Desktop: Xfce" ;;
  *MATE*)     say "  Рабочий стол: MATE" "  Desktop: MATE" ;;
  *Cinnamon*) say "  Рабочий стол: Cinnamon (поддержка экспериментальная)" "  Desktop: Cinnamon (experimental support)" ;;
  *Budgie*)   say "  Рабочий стол: Budgie" "  Desktop: Budgie" ;;
  *LXQt*)     say "  Рабочий стол: LXQt" "  Desktop: LXQt" ;;
  *)       warn "Рабочий стол «${XDG_CURRENT_DESKTOP:-неизвестно}»: WaySwitch его не знает. Поддерживаются GNOME, KDE, Xfce, MATE, Cinnamon, Budgie, LXQt, Sway, Hyprland." \
                "Desktop \"${XDG_CURRENT_DESKTOP:-unknown}\": WaySwitch does not know it. Supported: GNOME, KDE, Xfce, MATE, Cinnamon, Budgie, LXQt, Sway, Hyprland." ;;
esac
if [ "${XDG_SESSION_TYPE:-}" != wayland ]; then
  warn "Сеанс не Wayland (${XDG_SESSION_TYPE:-неизвестно}). На экране входа выберите сеанс Wayland." \
       "Not a Wayland session (${XDG_SESSION_TYPE:-unknown}). Pick a Wayland session on the login screen."
fi
case "$FAMILY" in deb|fedora|suse)
  have curl || die "Нужен curl: установите его менеджером пакетов и запустите снова." \
                   "curl is required: install it with your package manager and run again." ;;
esac
[ "$FAMILY" = deb ] && check_adwaita_deb

h=""
case "$FAMILY" in
  deb)    PLAN_RU="скачать пакет .deb и установить через apt";             PLAN_EN="download the .deb package and install it with apt" ;;
  fedora) if [ "$ATOMIC" = 1 ]; then
          PLAN_RU="скачать пакет .rpm и добавить через rpm-ostree (нужна перезагрузка)"; PLAN_EN="download the .rpm and layer it with rpm-ostree (reboot required)"
          else PLAN_RU="установить пакет .rpm через dnf"; PLAN_EN="install the .rpm package with dnf"; fi ;;
  suse)   PLAN_RU="скачать пакет .rpm и установить через zypper";          PLAN_EN="download the .rpm package and install it with zypper" ;;
  arch)   h=$(aur_helper)
          if [ -n "$h" ] && aur_is_ours; then
          PLAN_RU="установить пакет wayswitch из AUR через $h";            PLAN_EN="install the wayswitch AUR package with $h"
          else h=""; PLAN_RU="собрать из исходников (git + packaging/install.sh)"; PLAN_EN="install from source (git + packaging/install.sh)"; fi ;;
  *)      PLAN_RU="установить из исходников (packaging/install.sh)";       PLAN_EN="install from source (packaging/install.sh)" ;;
esac
say "  План: $PLAN_RU." "  Plan: $PLAN_EN."
ask "Продолжить?" "Continue?" || exit 0

# --- Установка ---------------------------------------------------------------
# Обновление: автозапуск службы, выключенный человеком, не включаем снова.
WAS=$(systemctl --user is-enabled wayswitch 2>/dev/null || true)
step "Устанавливаю" "Installing"
need_sudo
case "$FAMILY" in
  deb)    get_package deb
          if [ -n "$PKG" ]; then drop_source_copy; sudo apt install -y "$PKG"; else from_source install.sh; fi
          # Значок в верхней панели GNOME — через AppIndicator (в Ubuntu уже есть).
          case "${XDG_CURRENT_DESKTOP:-}" in *GNOME*)
            sudo apt install -y gnome-shell-extension-appindicator >/dev/null 2>&1 \
              && enable_extension appindicatorsupport@rgcjonas.gmail.com || true ;;
          esac ;;
  fedora) get_package rpm
          if [ -z "$PKG" ]; then from_source install.sh
          elif [ "$ATOMIC" = 1 ]; then drop_source_copy
            if rpm -q wayswitch >/dev/null 2>&1; then
              # Обновление: старый наложенный пакет заменяем новым за один шаг.
              sudo rpm-ostree install -y --uninstall=wayswitch "$PKG"
            else sudo rpm-ostree install -y "$PKG"; fi
          else drop_source_copy; sudo dnf install -y "$PKG"; fi ;;
  suse)   get_package rpm
          if [ -n "$PKG" ]; then drop_source_copy; sudo zypper --non-interactive install --allow-unsigned-rpm "$PKG"
          else from_source install.sh; fi ;;
  arch)   if [ -n "$h" ]; then drop_source_copy; "$h" -S --needed --noconfirm wayswitch; else from_source install.sh; fi ;;
  *)      from_source install.sh ;;
esac

systemctl --user daemon-reload >/dev/null 2>&1 || true
[ "$WAS" = disabled ] || systemctl --user enable wayswitch >/dev/null 2>&1 || true
# Обновление: работающая служба перечитает новый код.
systemctl --user try-restart wayswitch >/dev/null 2>&1 || true
enable_extension wayswitch@siberia.ru || true
enable_a11y || true

# --- Что дальше ----------------------------------------------------------------
step "WaySwitch установлен" "WaySwitch is installed"
if [ "$ATOMIC" = 1 ]; then
  say "  Осталось перезагрузить компьютер. После перезагрузки откройте WaySwitch из меню приложений — он проверит систему и включит службу." \
      "  Now restart the computer. After the restart open WaySwitch from the app menu — it will check the system and enable the service."
else
  say "  Осталось выйти из системы и войти снова — так WaySwitch получит доступ к клавиатуре." \
      "  Now log out and log back in — this gives WaySwitch access to the keyboard."
  say "  После входа откройте WaySwitch из меню приложений: он проверит систему и подскажет, если чего-то не хватает." \
      "  After logging in, open WaySwitch from the app menu: it checks the system and tells you if anything is missing."
fi
case "${XDG_CURRENT_DESKTOP:-}" in *Hyprland*|*sway*|*Sway*)
  say "  Автозапуск в Sway и Hyprland: добавьте в конфиг exec wayswitch run (Hyprland: exec-once = wayswitch run)." \
      "  Autostart in Sway and Hyprland: add exec wayswitch run to the config (Hyprland: exec-once = wayswitch run)."
  say "  Модуль для Waybar: wayswitch gui → Параметры → Система." \
      "  Waybar module: wayswitch gui → Preferences → System." ;;
esac
say "  Проверить, что всё в порядке: wayswitch doctor" "  To check that everything is fine: wayswitch doctor"
