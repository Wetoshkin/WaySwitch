<p align="center">
  <img src=".github/assets/banner-en.svg" alt="WaySwitch — fixes words typed in the wrong keyboard layout" width="100%">
</p>

<p align="center">
  <a href="https://github.com/Wetoshkin/WaySwitch/releases"><img src="https://img.shields.io/github/v/release/Wetoshkin/WaySwitch?style=flat-square&color=c67139" alt="Release"></a>
  <a href="https://github.com/Wetoshkin/WaySwitch/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/Wetoshkin/WaySwitch/ci.yml?style=flat-square&label=tests" alt="Tests"></a>
  <img src="https://img.shields.io/badge/GNOME-46–51-7a8a5e?style=flat-square" alt="GNOME 46–51">
  <img src="https://img.shields.io/badge/KDE_Plasma-6-3daee9?style=flat-square" alt="KDE Plasma 6">
  <img src="https://img.shields.io/badge/Wayland-✓-7a8a5e?style=flat-square" alt="Wayland">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-4a4540?style=flat-square" alt="MIT"></a>
</p>

<p align="center">
  <a href="README.md">Русский</a> · <b>English</b>
</p>

**Automatic Russian–English keyboard layout correction for Linux on Wayland.**

**Version 1.1.2 — GNOME input-field detection and Writer text correction.**

WaySwitch fixes a word typed in the wrong layout (`ghbdtn` instead of
«привет») right away, on Space — no hotkeys, no manual layout switching. It
works on Wayland in GNOME, COSMIC, KDE Plasma, Sway, Hyprland, Umbriel, Xfce, MATE, Budgie
and LXQt, where classic tools of this kind can neither listen to the
keyboard nor type for you. Linux Mint (Cinnamon) is not supported yet. The interface is in Russian: WaySwitch currently
supports the Russian ↔ English pair.

<p align="center">
  <img src=".github/assets/how-en.png" alt="Type руддщ, press Space, get hello" width="100%">
</p>

## Features

- **Fix on Space.** `ghbdtn ` becomes `привет `, and the layout switches too.
  Numbers, paths, URLs, camelCase and abbreviations are left alone.
- **URLs right away.** A URL scheme typed in the wrong layout is fixed
  without waiting for Space.
- **Manual fix.** Double Shift fixes the last word, triple Shift the whole
  phrase. You can pick a single key instead (Right Ctrl, ScrollLock, an F-key).
- **Fix selection.** Select a phrase typed in the wrong layout, press your
  key — done.
- **By the start of a word.** If the first four letters clearly belong to
  the other layout (`ghbd` starts no English word but does start «прив…»),
  the layout is switched right away.
- **Transliteration and numbers in words** for the selected text, on keys of
  your choice (GNOME).
- **Undo that learns.** The same gesture right after an automatic fix
  restores the word; undo the same word a second time and it is never
  touched again.
- **Small typos** (each can be turned off in Preferences): an accidental
  Caps Lock (`пРИВЕТ` → «Привет», Caps Lock is turned off); two capitals at
  the start of a word (`ПРивет` → «Привет», Shift released too late); a
  comma or period typed as the last Russian letter on the same key
  (`пирогб` → «пирог,»); particles typed together (`чтото` → «что-то», `кембы` → «кем бы»);
  a missing space before a capitalised word (`сонатуБаха` → «сонату Баха»).
- **Exceptions.** Words and programs to leave alone. Password fields are skipped.

The following desktop integrations are illustrated with mockups. Full testing
with a physical keyboard is still needed; see [Status](#status).

<p align="center">
  <img src=".github/assets/de-kde.png" width="420" alt="WaySwitch widget in the KDE Plasma system tray">
  <br>
  <img src=".github/assets/de-panels.png" width="100%" alt="WaySwitch tray menu in Xfce, MATE and LXQt">
  <br>
  <img src=".github/assets/de-hyprland.png" width="100%" alt="WaySwitch on Hyprland: Waybar module and the WaySwitch window">
</p>

<p align="center">
  <img src="docs/screenshots/main-dark.png" width="400" alt="WaySwitch main window">
  &nbsp;
  <img src="docs/screenshots/prefs-light.png" width="400" alt="WaySwitch preferences">
</p>

## Install

**You need** a Wayland session (not X11) and two layouts — Russian and a
Latin one. Supported installation targets include Ubuntu 24.04+, Debian 13+,
Fedora 41+, openSUSE Tumbleweed, Arch and their compatible derivatives.
The installer checks the system before making changes.

**NixOS:** use the separate [package and system module guide](docs/nixos.md).
The command below is for other distributions.

1. Open a terminal (in KDE it is **Konsole**).
2. Paste the command with <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>V</kbd> and press <kbd>Enter</kbd>:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/Wetoshkin/WaySwitch/main/packaging/get.sh | sh
   ```

3. The script detects your distribution, says what it is going to do and
   asks for confirmation, then for your password (it is not shown while you
   type — that's normal).
4. **Restart the computer** (save your documents first). WaySwitch then
   starts by itself (on Sway and Hyprland — once `exec wayswitch run` is in
   the config, see below); the first time you open its window it checks the
   system and tells you if anything is missing.

| Distribution | What happens |
|---|---|
| Ubuntu 24.04+, Debian 13 and derivatives | downloads the `.deb` from the latest release, installs with `apt` |
| Fedora 41+ | installs the `.rpm` with `dnf` |
| Silverblue, Kinoite, Bazzite | layers the `.rpm` with `rpm-ostree` (reboot afterwards) |
| openSUSE Tumbleweed, Slowroll | installs the `.rpm` with `zypper` |
| Arch, CachyOS, EndeavourOS, Manjaro | AUR package via `paru`/`yay` once it is published, otherwise from source |
| NixOS | use the [package and system module](docs/nixos.md), without the generic installer |
| others | from source via `packaging/install.sh` |

For script installations, uninstall with the same command and `| sh -s -- --uninstall`.

Before installing a `.deb` or `.rpm`, the script verifies its SHA256 and size
against the selected GitHub release metadata. Missing checksums or failed
verification stop installation. This check requires `python3`; the installer
reports the prerequisite if it is missing.

### Flatpak and AppImage

Portable settings clients are available. Install and update the
native WaySwitch service on the host to version 1.1.2 using the command above; `1.0.0`
does not contain these fixes. The first-run window explains companion
installation, startup and diagnostics. Settings and pause state are shared.

Download the local bundle and `SHA256SUMS` from the same release:

```bash
grep -E '^[[:xdigit:]]{64}  WaySwitch-Settings\.flatpak$' SHA256SUMS | sha256sum -c - && \
flatpak install --user --or-update ./WaySwitch-Settings.flatpak
flatpak run io.github.Wetoshkin.WaySwitch.Gui
```

The bundle offers the official repository for GNOME Runtime 50. To update a local
bundle, download the replacement, verify it against the release's `SHA256SUMS`,
then run `flatpak install --user --or-update ./WaySwitch-Settings.flatpak`.
Remove the GUI with `flatpak uninstall --user io.github.Wetoshkin.WaySwitch.Gui`.
The application has not been published on Flathub. `flatpak update` works for
a configured application repository, rather than a standalone bundle file.

AppImage requires x86_64 Linux with glibc 2.39+ (Ubuntu 24.04+, Debian 13+):

```bash
grep -E '^[[:xdigit:]]{64}  WaySwitch-Settings-x86_64\.AppImage$' SHA256SUMS | sha256sum -c - && \
chmod +x WaySwitch-Settings-x86_64.AppImage && \
./WaySwitch-Settings-x86_64.AppImage --appimage-extract-and-run
```

Python, GTK4 and libadwaita are bundled. Close the GUI and replace the file after
verifying its release checksum to update; delete the file to remove it. AppImage
runs with the user's permissions. Flatpak has no input-device access. Removing
either portable GUI leaves the native service and shared settings in place.

### COSMIC

Configure Russian and a Latin layout in COSMIC Settings. The backend reads the
actual keyboard map and group from the compositor and waits for switch
acknowledgements. Tested on COSMIC 1.11 with multiple keyboards in one seat;
multiple keyboard seats are unsupported.

After every service start/restart and screen lock, unlock the desktop and
explicitly enable correction using the GUI/tray or `wayswitch resume`.
COSMIC does not provide a reliable public unlocked-state acknowledgement, so
correction never resumes automatically. Unknown protection state keeps it off;
`wayswitch doctor` explains recovery. Automatic Caps Lock deactivation is disabled
because the compositor's compiled keymap does not expose original XKB options.

### NixOS and Umbriel

A package and system module are provided for NixOS; see the
[setup guide](docs/nixos.md) for dependencies, device access and service startup.
Umbriel uses native IPC and requires exactly two layouts shared by ordinary
keyboards and `uinput`, plus a graphical session registered with logind.
Unknown session state blocks both automatic and manual correction.
Automatic Caps Lock deactivation is unavailable without XKB option information.

## Gestures

| Gesture | Action |
|---|---|
| <kbd>Shift</kbd> <kbd>Shift</kbd> | fix the last word; with nothing typed — just switch the layout |
| <kbd>Shift</kbd> <kbd>Shift</kbd> <kbd>Shift</kbd> | fix the whole phrase |
| <kbd>Shift</kbd> <kbd>Shift</kbd> right after an automatic fix | undo and remember the word |
| your key (Preferences → Manual) | fix the selected text |

## Settings

Everything is set with the mouse and applied immediately. Open **WaySwitch**
from the app menu; preferences are under <kbd>☰</kbd> or
<kbd>Ctrl</kbd>+<kbd>,</kbd>. On **Sway and Hyprland** the indicator is a
Waybar module (`wayswitch waybar`), pausing is bound to a hotkey (`wayswitch toggle`; the last word is
fixed with the Shift gesture), and ready-made config snippets are
copied from Preferences → System → Panel. Sway and plain Hyprland do not
start systemd user services: add `exec wayswitch run` (Sway) or
`exec-once = wayswitch run` (Hyprland) to the config. On **Xfce, MATE, Budgie, LXQt**
(labwc / Wayfire) WaySwitch reads the layouts from the compositor's XKB
settings — see the Russian README for snippets.

## Updating

The GitHub namespace changes the extension, application and portable package IDs.
See the [migration guide](docs/identifiers.md) for Flatpak, GNOME and the KDE widget.
WaySwitch settings are preserved.

Once a day WaySwitch asks GitHub whether a new version is out; if so, the
window shows a bar with an **Update** button (also in the notification, the
panel menu and Preferences → System). It opens a terminal with the install
command — confirm and enter your password. Settings are kept, no restart
needed (on GNOME the new extension version takes effect at the next login;
on Silverblue and Kinoite — after a reboot). Preferences → System → **Remove WaySwitch** works the same way.

## Troubleshooting

| Command | What it shows |
|---|---|
| `wayswitch doctor` | what works, what is missing and how to fix it |
| `journalctl --user -u wayswitch -n 50` | the latest log lines |
| `wayswitch dry-run` | detector decisions without touching the screen |

Passing the mandatory checks does not confirm that the focused field is accessible.
If the active application is unknown, run `sleep 5; wayswitch doctor` and focus a
normal text field in the target application during that delay. Protected automatic
correction waits for verified focus; the manual gesture remains available.

A word was fixed by mistake — press <kbd>Shift</kbd> <kbd>Shift</kbd> right
away. Still stuck — [open an issue](https://github.com/Wetoshkin/WaySwitch/issues/new)
with diagnostic output (also available under About → Troubleshooting).
Remove private information before posting: verbose and dry-run logs may
contain typed text. See [Contributing](CONTRIBUTING.md).

## FAQ

**What happens to my data?** Correction runs locally; typed text is not sent
over the network. Recent corrections are held in memory. Word exceptions,
including those learned from undoing corrections, are saved to
`~/.config/wayswitch/exceptions.txt`. Logs, including `--verbose` and
`dry-run`, record decisions and operation results without typed words.
When focus is unknown or stale, correction text is withheld from the
window history over D-Bus and from learning after undo.

Update checks contact the GitHub API. GitHub receives your IP address and
the WaySwitch version in the `User-Agent`, but no typed text. You can turn
update checks off in Preferences.

Password-field detection depends on the application exposing that information.
Pause WaySwitch before entering sensitive text in a field whose protection
you have not verified. The udev rule grants keyboard access to other processes
running as the active user as well.

With password protection enabled, automatic correction is disabled in terminals.
Terminals do not reliably distinguish hidden passwords from ordinary input.
Manual gestures remain available. Terminal input is withheld from correction
history and learning.

**X11?** No, WaySwitch is built for Wayland.

**No tray icon in GNOME?** Enable AppIndicator in the Extensions app. On
regular Fedora, install it with `sudo dnf install gnome-shell-extension-appindicator`
if missing. Bazzite already bundles the extension.

**Flatpak and AppImage.** These contain the settings GUI with an explicit native
host companion, as described above. The daemon needs `/dev/input`, `/dev/uinput`,
a udev rule and desktop integration. A Snap package is not available.

## Status

WaySwitch 1.1.2. Tested on CachyOS with GNOME 51, Ubuntu with GNOME 50.1,
Bazzite with GNOME, and COSMIC 1.11. A Plasma 6.6.6 Wayland VM was used to
verify Kate/KWrite, normal and password fields in Qt/GTK, and protected
Konsole behavior. Sway, Hyprland and the labwc/Wayfire desktops are covered
by tests but not yet tried live — reports are welcome.

Umbriel was tested in a native Ubuntu Wayland session, including ordinary input,
undo, a password field, screen locking and VT changes. The Nix package was built
and checked on Linux; a live NixOS graphical session has not been tested.

Writer checks cover both directions, undo after continued typing, automatic
capitalization and two consecutive words. Automatic correction in Calc remains
limited: ambiguous focus events reset the input buffer. Chrome checks cover
ordinary fields, editable page elements, passwords and readonly fields.
The original eXpress message editor was tested in an isolated environment
without sending messages; an authenticated chat has not been tested.

Firefox Flatpak checks on Bazzite cover input, textarea, contenteditable and the
address bar. Telegram was checked in an ordinary field before login; its
authenticated message editor remains untested. ONLYOFFICE Desktop Editors 10.0.0
(Flatpak on Bazzite) did not expose an AT-SPI field tree on the tested setup;
automatic correction was not confirmed.

When retyping uses virtual key presses, observed physical input stops further
changes and can leave a partial correction. A physical key can still arrive
between a check and the next virtual key.

With password-field protection enabled, automatic correction waits for
verified AT-SPI focus. Use a manual gesture if the application's accessibility
bridge is unavailable. `wayswitch doctor` shows application identifiers for
exclusions. In GNOME, update the extension too and sign in again.
Buffered text, context and undo belong to the field where input was captured.
A programmatic field change discards the old span. If focus becomes verified
partway through a word, correction waits for a boundary rather than replaying
an unverified prefix.
COSMIC correction and lock-screen handling have been tested live. Explicit
confirmation is required after service startup and unlocking, as described above.

## Development

```bash
pip install pytest ruff
python -m pytest -q
python -m ruff check .
```

Packages: `bash tools/build_deb.sh`, `bash tools/build_rpm.sh`,
`cd packaging/arch && makepkg -si`. Portable GUI builds:
`bash tools/build_flatpak.sh build/flatpak` with GNOME SDK/Platform 50;
`python3 tools/build_appimage.py build/appimage` on x86_64 Linux with
squashfs-tools and dpkg-deb.

The Flatpak is distributed through GitHub Releases and is not published on Flathub.
Its required native host service needs individual consideration by Flathub reviewers.
[Flathub requirements](https://docs.flathub.org/docs/for-app-authors/requirements)
prohibit AI-generated or AI-assisted manifests and submission PRs by AI agents.
Any potential submission requires the owner to prepare compliant materials manually
and discuss eligibility of the required host service with the reviewers.

See [Contributing](CONTRIBUTING.md), the [architecture](docs/architecture.md)
and the [live-session checklist](docs/testing-vm.md).

## In the media

OpenNET, a well-known Russian-language portal covering Linux, Unix and
open-source software, published an article about WaySwitch.

- **November 2, 2026 · OpenNET** — coverage of the WaySwitch 1.0 release and
  automatic keyboard-layout correction. [Read the article and discussion (in Russian)](https://www.opennet.ru/opennews/art.shtml?num=66588).

## License

Code — MIT. Dictionaries in `wayswitch/data` are built from
[FrequencyWords](https://github.com/hermitdave/FrequencyWords), CC-BY-SA 4.0.
