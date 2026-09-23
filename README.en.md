<p align="center">
  <img src="logo.svg" width="128" height="128" alt="WaySwitch">
</p>

# WaySwitch

[Русский](README.md) · **English**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Platform: GNOME Wayland](https://img.shields.io/badge/platform-GNOME%20Wayland-orange.svg)](https://wayland.freedesktop.org/)

WaySwitch fixes a word typed in the wrong keyboard layout (`ghbdtn`
instead of «привет») the instant you finish typing it, on the space bar,
with no hotkey and no manual layout switch. Tools like this have existed
on X11 for years; Wayland's whole point is that a compositor won't let an
ordinary program listen to someone else's keyboard or fake input into
another window, so the X11 approach simply has no equivalent here.
WaySwitch sidesteps the problem by working one layer below the display
stack: it reads keyboard events straight from `/dev/input` (`evdev`) and
types through the kernel's virtual keyboard (`uinput`), so it doesn't
matter to the compositor whether you're on GNOME, some other Wayland
environment, or anything else exposing the same kernel interfaces. The
daemon never grabs the keyboard device — if it ever hangs, your keyboard
keeps working normally.

## How it feels

- Type `ghbdtn ` (Latin keys while the Cyrillic layout is off) and on the
  space it becomes `привет `, layout switched along with the word.
- Start typing a URL in the wrong layout — `реезыЖ..` — and WaySwitch
  recognizes the URL scheme and fixes it right away, without waiting for
  a space.
- Autocorrect picked the wrong thing, or missed something you want fixed
  manually — a double Shift fixes the last word, a triple Shift (a third
  tap right after the double) fixes the whole phrase you just typed.
- Autocorrect fired when it shouldn't have — the same double Shift within
  a few seconds after the fix rolls the word back and remembers it as an
  exception, so WaySwitch leaves it alone next time.

## Installation

This section is a step-by-step guide for anyone opening a terminal for
the first time on Linux (experienced users: the short path from source
and the command table are at the end of this section and of "Usage").

Three terms you'll meet below: a **terminal** is a text window where you
type commands instead of clicking; **`sudo`** at the start of a command
means "run as administrator" (the system will ask for your own account
password, and while you type it, nothing appears on screen — no dots, no
asterisks, that's normal); to paste copied text into a terminal, use
**Ctrl+Shift+V** (plain Ctrl+V doesn't work there).

### 1. Check your system fits

WaySwitch only works on **GNOME**, and only on **Wayland** (the way the
system draws windows and reads the keyboard; the older alternative is
X11, where WaySwitch is useless). Open "Settings → About" (Параметры →
О системе): the desktop should say **GNOME**, and the "Windowing System"
row should say **Wayland**, not X11. Supported: GNOME 46–50, Ubuntu
24.04 and newer (including 26.04). If it says X11 — log out, click the
gear icon next to the password field on the login screen and pick
**GNOME** (not "GNOME on Xorg"), then log back in.

### 2. Add Russian and English layouts

"Settings → Keyboard → Input Sources" (Параметры → Клавиатура →
Источники ввода): you need exactly **two** layouts — Russian and English
(Latin); remove extras with the "−" button, add the missing one with
"+". Check that **Super (the logo key) + Space** switches the layout.

### 3. Open a terminal and install the package

Press **Super**, type "Terminal" (Терминал), open the app you find.
Copy the three lines below and paste them into the terminal
(**Ctrl+Shift+V**), then press Enter:

```bash
url=$(curl -s https://api.github.com/repos/Wetoshkin/WaySwitch/releases/latest | grep -o 'https://[^"]*\.deb' | head -1)
wget "$url"
sudo apt install ./wayswitch_*.deb
```

The first two lines download the latest release (a few seconds); the
third starts with `sudo`, asks for your password, lists dependencies and
asks something like `Do you want to continue? [Y/n]` — press Enter or Y.
Under a minute; `apt` pulls in GTK4, IBus and the rest on its own.

### 4. Log out and back in

Log out and log back in (a reboot works too) — the udev rule the package
installed for keyboard access only applies to a new session: without
logging back in, the daemon can't read your keyboard.

### 5. Check your environment

```bash
wayswitch doctor
```

`✓` means all is fine, `✗` means something is missing, with a hint below
it on what to do; the last line says "Всё готово." (All good) or "Не
пройдено обязательных проверок: N." (N required checks failed). The
"Расширение GNOME Shell" (GNOME Shell extension) and "systemd
user-сервис" (systemd user service) lines are optional: you'll return to
the extension below. If a required check fails, follow the hint and run
`doctor` again.

### 6. Start the daemon and enable autostart

```bash
systemctl --user enable --now wayswitch
```

`--now` starts WaySwitch right away, `enable` turns on autostart on
every login. To check it's running: `wayswitch status` or `systemctl
--user status wayswitch` (look for `Active: active (running)`).

### From source / Fedora (advanced)

```bash
git clone https://github.com/Wetoshkin/WaySwitch.git wayswitch
cd wayswitch
sudo packaging/install.sh
```

Installs the system packages, copies the code to
`/usr/local/lib/wayswitch`, and installs the udev rule, the systemd user
unit and the GNOME Shell extension into the profile of the user who ran
`sudo` (run it as your regular user via `sudo`, not as root). Then
follow steps 4–6 above. On Debian/Ubuntu prefer the `.deb` package; this
path is for Fedora and building from source.

### Uninstall

```bash
sudo apt remove wayswitch          # if you installed the .deb
sudo packaging/uninstall.sh        # if you installed from source
```

Your configuration (`~/.config/wayswitch`) is left untouched, in case
you reinstall; to remove it too: `rm -rf ~/.config/wayswitch`.

## Usage

### Your first fix

Open a text field — for example, "Text Editor" (it ships with Ubuntu by
default; find it via Super). Switch to the English layout (Super+Space),
type `ghbdtn` and press space — the word turns into «привет», and the
layout switches to Russian.

Try three more gestures right away (Shift taps in quick succession, no
pauses; exact timings are in the "Gestures" table below): a **double
Shift** right after a word fixes it manually, even if autocorrect stayed
quiet; a **triple Shift** (a third tap right after the double) fixes the
whole phrase; a **double Shift within a few seconds of an autocorrect**
undoes the word and remembers it as an exception.

### More precise: the GNOME Shell extension (optional)

After the relogin (step 4), enable the extension the package already
installed system-wide — it gives the daemon exact knowledge of the
current layout and retypes instantly: `gnome-extensions enable
wayswitch@siberia.ru`, or via the "Extensions" app. Then, in the
WaySwitch settings window (below), on the "Settings" page, set "Backend"
to "GNOME Shell extension". Without it, WaySwitch talks to IBus with a
small delay (`settle_ms`, 30 ms by default) — usually unnoticeable, but
the extension is more precise.

### Settings window and tray icon

```bash
wayswitch-gui
```

or find "WaySwitch" in the app grid (Super). Four pages:

- **Status** — whether the daemon is running, the backend and layout in
  use, a session correction counter, a test field.
- **Settings** — autocorrect on/off, sensitivity, the manual-fix
  gesture, undo window, fine-tuning delays, backend.
- **Exceptions** — words that are never autocorrected; add or remove
  them by hand.
- **Autostart** — toggles for "Daemon on login" and "Tray icon on
  login".

To have the tray icon show up right after login, turn on the second
toggle on the "Autostart" page — it needs the AppIndicator extension in
Ubuntu, which is enabled by default.

### Passwords

Before typing a password into an unusual field (not the system login
dialog, but say a form on a website), pause WaySwitch — in the tray
("Pause") or with `wayswitch pause` — and resume afterward
(`wayswitch resume`). The daemon tries not to touch password fields, but
don't rely on that alone.

### If something goes wrong

`wayswitch doctor` — check for a new `✗`; `journalctl --user -u
wayswitch -n 50` — the daemon's recent log lines; `wayswitch dry-run` in
a terminal — the daemon watches and logs what it would fix, without
changing anything on screen. If that doesn't help, file an issue in the
project's repository with the output of those three commands.

### Gestures (reference)

| Gesture | Effect |
|---|---|
| Double Shift (second tap within 0.4 s) | fix the last word; with an empty buffer, just switches the layout |
| Triple Shift (a third tap right after the double) | fix the whole phrase, in whichever layout the double Shift picked |
| Double Shift within `undo_window_sec` after an autocorrect | undo the word and remember it as an exception |

Gestures do nothing while the screen is locked or the daemon is paused.

### Commands (reference)

| Command | Effect |
|---|---|
| `wayswitch run [--verbose] [--dry-run] [--config PATH]` | run the daemon |
| `wayswitch dry-run` | daemon in observe-only mode (verbose, no actions) |
| `wayswitch doctor` | diagnose the environment |
| `wayswitch devices` | list input devices |
| `wayswitch pause` / `resume` | pause/resume over D-Bus |
| `wayswitch fix` | fix the last word over D-Bus |
| `wayswitch status` | daemon status |
| `wayswitch gui` | open the settings window |
| `wayswitch version` | print the version |
| `wayswitch-gui [--tray]` | settings; `--tray` goes straight to the tray, no window |

The tray icon opens a menu: toggle autocorrect, pause/resume, "fix last
word", settings, quit.

## Settings

`~/.config/wayswitch/config.toml` (created on first save from the GUI, or
by hand):

```toml
[general]
auto_correct = true           # whether autocorrect on space is on
sensitivity = "normal"        # conservative | normal | aggressive
phrase_timeout_sec = 8.0      # gap between words after which the phrase for triple Shift resets (1..300)
undo_window_sec = 5.0         # how many seconds after a fix a double Shift still undoes it (0..60)

[gesture]
manual = "double_shift"       # double_shift | pause_key | none
pause_hotkey = ""             # pause key, e.g. "scroll_lock" (empty — not assigned)

[typing]
settle_ms = 30                # delay after the layout-change signal on the IBus path, ms (0..2000)
key_delay_ms = 0              # delay between keystrokes sent while typing, ms (0..100)

[backend]
prefer = "auto"               # auto | shell | ibus | hotkey
```

`sensitivity` sets the confidence threshold: `conservative` fixes fewer
short or rare words but makes fewer mistakes; `aggressive` does the
opposite.

`~/.config/wayswitch/exceptions.txt` lists words that are never
autocorrected (one per line, case-insensitive); it grows when you undo a
fix with a gesture or edit exceptions in the GUI.

## How it works

1. `evdev` listens to keyboards straight from `/dev/input/event*`, without
   grabbing the device and without root: a udev rule tagged `uaccess` grants
   access to the user of the active session, no group membership required.
2. A buffer accumulates the keycodes of the current word and phrase, and
   tracks Shift/CapsLock separately.
3. On space, the detector decodes the keystrokes two ways — in the current
   layout and in the alternate one — using `libxkbcommon` tables built
   from the user's actual layouts, not a hardcoded QWERTY/ЙЦУКЕН map.
4. A "keep" or "fix" decision comes from a dictionary lookup plus a
   character-trigram language model, with stop rules for digits, mixed
   alphabets, camelCase, paths/URLs, and short words outside the
   dictionary.
5. Fixing builds a plan: N × Backspace, a layout switch through the
   selected backend (GNOME Shell extension → IBus → hotkey fallback), and
   retyping the characters through `uinput`.
6. A separate early trigger catches the start of a URL (`http://`,
   `https://`, `ftp://`) typed in the wrong layout, without waiting for a
   space.
7. Gestures (double/triple Shift) and undo-with-learning run on top of the
   same execution plan; nothing happens while the screen is locked or the
   daemon is paused.

Detector quality on a held-out slice of the dictionary (not used for
training): ru — 96.5% recall, 0.07% false-positive rate; en — 96.8%
recall, 0.07% false-positive rate.

## Requirements

- GNOME 46–50 on Wayland.
- Exactly two input layouts: one `ru*`, one Latin.
- Ubuntu 24.04+ or Fedora 40+.
- Python 3.11+.

## Status and limitations

WaySwitch v2 is an **alpha** — a full rewrite from scratch. All the logic
is covered by unit tests and exercised on Windows and in CI (Linux), but
it has not yet run on a live GNOME session; the first-run VM checklist is
`docs/testing-vm.md`. Known limitations:

- The daemon can't see text in password fields — pause it while typing a
  password.
- Autocompletion fields (a browser address bar, for instance) may behave
  unexpectedly while WaySwitch is retyping into them.
- If a correction is interrupted mid-flight (a physical keypress during
  retyping), the characters already sent are not rolled back — blindly
  trying to "fix" it would only make the on-screen text diverge further.
- The `uaccess` udev rule lets any program running as the current user
  read keystroke events, not just WaySwitch — that's the price of the
  `evdev`-based design.
- GNOME only for now; KDE and Sway/Hyprland are on the roadmap.

## Development

```bash
pip install pytest ruff
python -m pytest -q
python -m ruff check .
```

Two `keymap.py` tests that need a real `libxkbcommon`
(`tests/test_keymap_xkb.py`) are skipped on Windows and run in CI (GitHub
Actions, `ubuntu-latest`). Rebuild the dictionary data with
`python tools/build_data.py`. Build the `.deb` package on Linux with
`bash tools/build_deb.sh`; releases are cut by tagging `vX.Y.Z` to match
the version in `pyproject.toml`.

## Roadmap

- A KDE Plasma backend and IPC backends for Sway/Hyprland.
- Detecting password fields and the active application via AT-SPI, on any
  desktop, instead of GNOME-specific heuristics.
- Fixing already-selected text through the clipboard/portal.
- Additional languages as a pluggable dictionary-and-trigram package.
- A privileged helper in place of the broad `uaccess` rule on keyboards.
- An `.rpm` package (Flatpak isn't possible: it needs direct access to
  `/dev/input`).

## Licenses

Code is MIT (`LICENSE`). The dictionary data and trigrams
(`wayswitch/data/*.gz`) come from the open FrequencyWords project and are
distributed under CC-BY-SA 4.0 — see `wayswitch/data/LICENSE` for details.
