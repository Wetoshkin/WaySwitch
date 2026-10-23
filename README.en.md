<p align="center">
  <img src="packaging/icons/ru.siberia.WaySwitch.svg" width="112" height="112" alt="">
</p>

<h1 align="center">WaySwitch</h1>

<p align="center">
  Fixes words typed in the wrong keyboard layout — on Wayland, in GNOME and KDE Plasma.<br>
  <code>ghbdtn</code> + Space → «привет», and the layout switches by itself.
</p>

<p align="center">
  <a href="README.md">Русский</a> · <b>English</b>
</p>

<p align="center">
  <a href="https://github.com/Wetoshkin/WaySwitch/releases"><img alt="Release" src="https://img.shields.io/github/v/release/Wetoshkin/WaySwitch?include_prereleases"></a>
  <a href="https://github.com/Wetoshkin/WaySwitch/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Wetoshkin/WaySwitch/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Wayland" src="https://img.shields.io/badge/Wayland-GNOME%2046%E2%80%9351%20%C2%B7%20KDE%20Plasma%206-c46a32">
  <a href="LICENSE"><img alt="MIT" src="https://img.shields.io/badge/license-MIT-7a8a5e"></a>
</p>

<p align="center">
  <img src="docs/screenshots/main-light.png" width="420" alt="WaySwitch main window">
  &nbsp;
  <img src="docs/screenshots/main-dark.png" width="420" alt="Main window, dark theme">
</p>

---

On X11 this kind of tool has existed for years (Punto Switcher, Caramba
Switcher, xneur). On Wayland the compositor deliberately stops programs from
listening to the keyboard or faking input, so those tools do not work there.
WaySwitch works one level lower: it reads the keyboard from `/dev/input`
(evdev) and types through the kernel's virtual keyboard (uinput). The layout
is switched by its own GNOME Shell extension or by KWin on KDE. The daemon
never grabs the keyboard: if it hangs, typing keeps working.

The UI is in Russian: WaySwitch currently supports the Russian ↔ English
pair.

## Features

| | |
|---|---|
| **Fix on Space** | `ghbdtn` → «привет», `руддщ` → `hello`. A 48k-word dictionary plus character trigrams; digits, paths, URLs, camelCase and abbreviations are left alone |
| **Manual fix** | double Shift fixes the last word, triple Shift the whole phrase. Instead of double Shift you can pick a single key (Right Ctrl, ScrollLock, an F-key…) or Pause |
| **Fix selection** | select a phrase typed in the wrong layout and convert it with one key — any key you choose, or a system shortcut bound to `wayswitch fix-selection` |
| **Undo that learns** | the same gesture within 5 s after an automatic fix restores the word and remembers it |
| **Small typos** | `пирогб` → «пирог,» (comma from the other layout), `чтото` → «что-то», `кембы` → «кем бы», `сонатуБаха` → «сонату Баха», `ПРивет` → «Привет» |
| **Exceptions** | words and programs to leave alone (Telegram, terminal, IDE). Password fields are skipped |
| **Edit guard** | for a second after Backspace, arrows or a manual layout switch, nothing is auto-corrected |
| **Panel icon** | status, pause, fix last word, fix selection |
| **Update notice** | asks GitHub once a day whether a new version is out; can be turned off |

Nothing leaves your machine except the anonymous “what is the latest version”
request (which can be disabled). Corrected words are never written to the log.

## Install

Requirements: **Wayland** (not X11), **GNOME 46–51** or **KDE Plasma 6**,
exactly two layouts — Russian and English.

<details open>
<summary><b>Arch, CachyOS, Manjaro, Fedora — from source</b></summary>

```bash
git clone https://github.com/Wetoshkin/WaySwitch.git
cd WaySwitch
sudo packaging/install.sh
```

The script installs dependencies (`pacman`/`dnf`/`apt`), copies the program
to `/usr/local`, installs the udev rule, the systemd service and the GNOME
Shell extension (and enables it). Then **log out and back in**.

</details>

<details>
<summary><b>Ubuntu, Debian — .deb package</b></summary>

```bash
url=$(curl -s "https://api.github.com/repos/Wetoshkin/WaySwitch/releases?per_page=1" | grep -o 'https://[^"]*\.deb' | head -1)
wget "$url"
sudo apt install ./wayswitch_*.deb
gnome-extensions enable wayswitch@siberia.ru
```

Then **log out and back in**.

</details>

After logging in, open **WaySwitch** from the app menu. The first run checks
the system, tells you what to fix and enables the service.

## Updating

**How you learn about a new version.** Once a day WaySwitch asks GitHub
whether a new version is out. If so, the window shows a “new version” bar, the
panel menu gets an item with the version number, and a system notification is
sent once. Turn it off in Preferences → System. You can also watch the
repository: **Watch → Custom → Releases**.

**How to update:**

```bash
# from source (Arch, CachyOS, Fedora)
cd WaySwitch && git pull && sudo packaging/install.sh

# .deb — download the new package and install it over the old one
sudo apt install ./wayswitch_<version>_all.deb
```

Settings and exceptions are kept. If the GNOME Shell extension was updated,
log out and back in: Shell keeps the old copy loaded until then.

## Usage

| What | How |
|---|---|
| Fix the last word manually | double Shift (or your own key from Preferences) |
| Fix the whole phrase | triple Shift |
| Undo an automatic fix and remember the word | the same gesture within 5 s |
| Fix selected text | your own key (Preferences → Manual), panel menu, or `wayswitch fix-selection` bound to a system shortcut |
| Pause | switch in the window, Ctrl+P, panel menu, `wayswitch pause` |
| Preferences / Gestures and keys | Ctrl+, / Ctrl+? |

Terminal commands: `wayswitch doctor` (system check with fixes),
`status`, `pause`/`resume`, `fix`, `fix-selection`, `dry-run` (observe only),
`devices`; `wayswitch-gui [--tray]`. The config file is
`~/.config/wayswitch/config.toml` — see the Russian README for every key.

## Troubleshooting

1. `wayswitch doctor` — shows what is missing and how to fix it.
2. In the window: menu → About → Troubleshooting — details to attach to an issue.
3. Common cases: the space after a fixed word disappears — raise “wait after
   layout switch” to 150–200 ms (Preferences → System → Advanced); the layout
   does not switch in GNOME — is the extension enabled, and did you log out
   and back in?
4. Still stuck — [open an issue](https://github.com/Wetoshkin/WaySwitch/issues)
   with the output of `wayswitch doctor` and `journalctl --user -u wayswitch -n 100`.

## Good to know

- **Keyboard access.** The udev rule lets programs in your session read
  keyboard events — otherwise WaySwitch could not see what you type. Every
  layout fixer on Wayland pays this price. WaySwitch never writes typed text
  to files or logs and never sends it anywhere.
- **Accessibility bridge (at-spi).** To know which program you are typing in
  and whether it is a password field, the installer enables
  `toolkit-accessibility` (`uninstall.sh` restores the previous value). Use
  `--no-a11y` to skip it; only per-program exceptions and password detection
  stop working.
- **Tested on** CachyOS, GNOME 51, Wayland. KDE Plasma 6 is covered by tests
  but not yet tried live. Sway, Hyprland and others offer no way to read the
  layout, so auto-correction is unavailable there.
- **The GNOME Shell extension** types only on request of WaySwitch itself;
  other programs on the bus cannot use it.

## Uninstall

```bash
sudo packaging/uninstall.sh      # installed from source
sudo apt remove wayswitch        # installed from .deb
```

## Development

```bash
pip install pytest ruff
python -m pytest -q
python -m ruff check .
```

Core logic does not import GTK or evdev at module level and is tested
anywhere, Windows included. See [CLAUDE.md](CLAUDE.md) for the module map.

## License

Code — [MIT](LICENSE). Dictionaries in `wayswitch/data` are built from
[FrequencyWords](https://github.com/hermitdave/FrequencyWords)
(OpenSubtitles), CC-BY-SA 4.0 — see `wayswitch/data/LICENSE`.
