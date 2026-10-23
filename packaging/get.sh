#!/bin/sh
# WaySwitch — установка одной командой для любого дистрибутива:
#   curl -fsSL https://raw.githubusercontent.com/Wetoshkin/WaySwitch/main/packaging/get.sh | sh
# Удаление:
#   curl -fsSL https://raw.githubusercontent.com/Wetoshkin/WaySwitch/main/packaging/get.sh | sh -s -- --uninstall
#
# Запускать от обычного пользователя, не через sudo: пароль спросят только на
# шаге установки пакета. Без подтверждения ничего не ставится (--yes —
# не спрашивать). Для проверки на своих исходниках: WAYSWITCH_SRC=/путь/к/WaySwitch.

set -eu

REPO="Wetoshkin/WaySwitch"
API="https://api.github.com/repos/$REPO/releases?per_page=1"   # включает альфа-релизы, в отличие от /latest
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
  printf '%s ' "$( [ "$RU" = 1 ] && echo "$1 [Д/н]" || echo "$2 [Y/n]" )"
  read -r a </dev/tty || a=""
  case "$a" in ""|[YyДд]*) return 0 ;; *) return 1 ;; esac
}
have() { command -v "$1" >/dev/null 2>&1; }

[ "$(id -u)" = 0 ] && die "Запустите без sudo — пароль спросят, когда он понадобится." \
                          "Run without sudo — you will be asked for a password when needed."
[ -r /etc/os-release ] || die "Не удалось определить дистрибутив (/etc/os-release)." \
                              "Cannot detect the distribution (/etc/os-release)."
. /etc/os-release
ID_ALL=" ${ID:-} ${ID_LIKE:-} "
ATOMIC=0; [ -e /run/ostree-booted ] && ATOMIC=1

FAMILY=source
case "$ID_ALL" in
  *" debian "*|*" ubuntu "*)                                   FAMILY=deb ;;
  *" fedora "*|*" rhel "*)                                     FAMILY=fedora ;;
  *" opensuse"*|*" suse "*|*" sles "*)                         FAMILY=suse ;;
  *" arch "*|*" cachyos "*|*" endeavouros "*|*" manjaro "*)    FAMILY=arch ;;
esac
# Свои исходники (проверка перед релизом) — всегда установка из исходников.
[ -n "${WAYSWITCH_SRC:-}" ] && FAMILY=source

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

asset_url() { # $1 = расширение: deb | rpm
  have curl || die "Нужен curl." "curl is required."
  curl -fsSL "$API" | grep -o "https://[^\"]*\.$1\"" | tr -d '"' | head -n1
}
fetch() { # скачать пакет релиза во временную папку, напечатать путь
  url=$(asset_url "$1"); [ -n "$url" ] || die "В последнем релизе нет пакета .$1." "No .$1 package in the latest release."
  curl -fL --progress-bar -o "$TMP/${url##*/}" "$url"; printf '%s' "$TMP/${url##*/}"
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
  git clone --depth 1 "https://github.com/$REPO.git" "$TMP/src" >/dev/null 2>&1
  sudo bash "$TMP/src/packaging/$1"
}
aur_helper() { for h in paru yay; do have "$h" && { echo "$h"; return; }; done; }

enable_extension() { # GNOME: расширение из пакета включить сразу (подхватится после входа)
  case "${XDG_CURRENT_DESKTOP:-}" in *GNOME*) ;; *) return 0 ;; esac
  gnome-extensions enable wayswitch@siberia.ru 2>/dev/null && return 0
  have gsettings || return 0
  cur=$(gsettings get org.gnome.shell enabled-extensions 2>/dev/null || echo "@as []")
  case "$cur" in
    *wayswitch@siberia.ru*) ;;
    "@as []"|"[]") gsettings set org.gnome.shell enabled-extensions "['wayswitch@siberia.ru']" ;;
    *) gsettings set org.gnome.shell enabled-extensions "${cur%]}, 'wayswitch@siberia.ru']" ;;
  esac
}

# --- Удаление ----------------------------------------------------------------
if [ "$MODE" = uninstall ]; then
  step "Удаляю WaySwitch (${PRETTY_NAME:-$ID})" "Removing WaySwitch (${PRETTY_NAME:-$ID})"
  systemctl --user disable --now wayswitch 2>/dev/null || true
  if   have dpkg   && dpkg -s wayswitch >/dev/null 2>&1; then sudo apt remove -y wayswitch
  elif [ "$ATOMIC" = 1 ];                                 then sudo rpm-ostree uninstall wayswitch
  elif have rpm    && rpm -q wayswitch >/dev/null 2>&1;  then
       if have dnf; then sudo dnf remove -y wayswitch; else sudo zypper --non-interactive remove wayswitch; fi
  elif have pacman && pacman -Q wayswitch >/dev/null 2>&1; then sudo pacman -Rns --noconfirm wayswitch
  else from_source uninstall.sh
  fi
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
  *Cinnamon*) say "  Рабочий стол: Cinnamon" "  Desktop: Cinnamon" ;;
  *Budgie*)   say "  Рабочий стол: Budgie" "  Desktop: Budgie" ;;
  *LXQt*)     say "  Рабочий стол: LXQt" "  Desktop: LXQt" ;;
  *)       warn "Рабочий стол «${XDG_CURRENT_DESKTOP:-неизвестно}»: WaySwitch его не знает. Поддерживаются GNOME, KDE, Xfce, MATE, Cinnamon, Budgie, LXQt, Sway, Hyprland." \
                "Desktop \"${XDG_CURRENT_DESKTOP:-unknown}\": WaySwitch does not know it. Supported: GNOME, KDE, Xfce, MATE, Cinnamon, Budgie, LXQt, Sway, Hyprland." ;;
esac
if [ "${XDG_SESSION_TYPE:-}" != wayland ]; then
  warn "Сеанс не Wayland (${XDG_SESSION_TYPE:-неизвестно}). На экране входа выберите сеанс Wayland." \
       "Not a Wayland session (${XDG_SESSION_TYPE:-unknown}). Pick a Wayland session on the login screen."
fi

h=""
case "$FAMILY" in
  deb)    PLAN_RU="скачать пакет .deb и установить через apt";             PLAN_EN="download the .deb package and install it with apt" ;;
  fedora) if [ "$ATOMIC" = 1 ]; then
          PLAN_RU="скачать пакет .rpm и добавить через rpm-ostree (нужна перезагрузка)"; PLAN_EN="download the .rpm and layer it with rpm-ostree (reboot required)"
          else PLAN_RU="установить пакет .rpm через dnf"; PLAN_EN="install the .rpm package with dnf"; fi ;;
  suse)   PLAN_RU="скачать пакет .rpm и установить через zypper";          PLAN_EN="download the .rpm package and install it with zypper" ;;
  arch)   h=$(aur_helper)
          if [ -n "$h" ] && "$h" -Si wayswitch >/dev/null 2>&1; then
          PLAN_RU="установить пакет wayswitch из AUR через $h";            PLAN_EN="install the wayswitch AUR package with $h"
          else h=""; PLAN_RU="собрать из исходников (git + packaging/install.sh)"; PLAN_EN="install from source (git + packaging/install.sh)"; fi ;;
  *)      PLAN_RU="установить из исходников (packaging/install.sh)";       PLAN_EN="install from source (packaging/install.sh)" ;;
esac
say "  План: $PLAN_RU." "  Plan: $PLAN_EN."
ask "Продолжить?" "Continue?" || exit 0

# --- Установка ---------------------------------------------------------------
step "Устанавливаю — может понадобиться пароль вашей учётной записи" \
     "Installing — you may be asked for your account password"
say "  (при вводе пароля символы не видны — это нормально)" "  (the password is not shown while you type — that's normal)"
case "$FAMILY" in
  deb)    f=$(fetch deb); sudo apt install -y "$f" ;;
  fedora) f=$(fetch rpm)
          if [ "$ATOMIC" = 1 ]; then sudo rpm-ostree install -y "$f"; else sudo dnf install -y "$f"; fi ;;
  suse)   f=$(fetch rpm); sudo zypper --non-interactive install --allow-unsigned-rpm "$f" ;;
  arch)   if [ -n "$h" ]; then "$h" -S --needed --noconfirm wayswitch; else from_source install.sh; fi ;;
  *)      from_source install.sh ;;
esac

systemctl --user enable wayswitch >/dev/null 2>&1 || true
enable_extension || true

# --- Что дальше ----------------------------------------------------------------
step "WaySwitch установлен" "WaySwitch is installed"
if [ "$ATOMIC" = 1 ]; then
  say "  Осталось перезагрузить компьютер. После перезагрузки WaySwitch запустится сам." \
      "  Now restart the computer. WaySwitch will start on its own after the restart."
else
  say "  Осталось выйти из системы и войти снова — так WaySwitch получит доступ к клавиатуре." \
      "  Now log out and log back in — this gives WaySwitch access to the keyboard."
  say "  После входа он запустится сам." "  It will start on its own after you log in."
fi
case "${XDG_CURRENT_DESKTOP:-}" in *Hyprland*|*sway*|*Sway*)
  say "  Модуль для Waybar и горячие клавиши: wayswitch gui → Параметры → Система." \
      "  Waybar module and keybindings: wayswitch gui → Preferences → System." ;;
esac
say "  Проверить, что всё в порядке: wayswitch doctor" "  To check that everything is fine: wayswitch doctor"
