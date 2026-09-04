# Setup guide: Windows & macOS

Step-by-step first-time setup for `clawdmeter-daemon` on your own machine. This
is the beginner walkthrough — `README.md` in this same folder is the full
reference (every flag, the payload contract, Calendar/Weather setup) if you
need more detail on any step below.

Pick your OS and follow the numbered steps in order.

---

## Windows

### 1. Install Python

Get Python 3.10+ from **python.org** (not the Microsoft Store version — its
sandboxing breaks the tray icon, see the note in `README.md`'s Install
section if you hit that). During install, tick **"Add python.exe to PATH"**.

### 2. Get this daemon onto your machine

Download/clone this repo, then open a terminal (PowerShell or `cmd`) in this
folder (`clawdmeter-daemon`).

### 3. Run the installer

```bat
install.bat
```

This creates a self-contained `.venv` here, installs everything it needs
into it, and registers the daemon to start automatically at login (no admin
rights required).

### 4. Get a Claude token

```bat
claude setup-token
```

This prints a token starting `sk-ant-oat…`. Set it permanently:

```bat
setx CLAUDE_CODE_OAUTH_TOKEN "sk-ant-oat...your-token..."
```

**Or**, instead of `setx`, put it in a `.env` file in this folder (copy
`.env.example` to `.env` and fill it in) — same effect, easier to find again
later. Either way, close and reopen your terminal afterward so the token is
picked up.

### 5. Point it at your device (if using a SmallTV over Wi-Fi)

Find your SmallTV's IP address (router's DHCP client list, look for an
`ESP-xxxxxx` hostname), then either:

```bat
start-daemon.bat --push-to 192.168.1.50
```

or set it once in `.env` (`CLAWDMETER_PUSH_URL=192.168.1.50`) so you never
need the flag again — the tray icon also remembers whatever you last
configured via its **Configure push targets…** menu item.

### 6. Check it's running

Look for the mascot icon in the system tray (it may start in the `⌃`
overflow area — drag it onto the taskbar to pin it). Hover it to see live
`5h % / 7d %`. If you don't see it, check `~/.clawdmeter-daemon.log` (in your
Windows user profile folder) for what happened.

---

## macOS

### 1. Install Python

macOS ships a Python, but Homebrew's is the one to use for this
(`brew install python`) — the system one blocks `pip install` (PEP 668).

### 2. Get this daemon onto your machine

Clone or download this repo, then open Terminal in this folder
(`clawdmeter-daemon`).

### 3. Run the installer

```sh
chmod +x install.sh
./install.sh
```

This creates a self-contained `.venv` here (with `--system-site-packages` so
the tray can still see native GTK/AppIndicator-equivalent bits), installs
everything, and registers a LaunchAgent so the daemon starts at login.

### 4. Get a Claude token

```sh
claude setup-token
```

Prints a token starting `sk-ant-oat…`. Either add it to your shell profile
(`~/.zshrc (interactive use only — a service never reads it; see AUTHENTICATION.md)` for the default shell):

```sh
export CLAUDE_CODE_OAUTH_TOKEN="sk-ant-oat...your-token..."
```

then open a **new** terminal tab so it's picked up — **or** put it in a
`.env` file in this folder instead (copy `.env.example` → `.env`, fill in
`CLAUDE_CODE_OAUTH_TOKEN=`). Either works; `.env` is easier to find again
later and doesn't touch your shell config.

### 5. Point it at your device (if using a SmallTV over Wi-Fi)

Same as Windows step 5 — find the device's IP via your router's DHCP client
list, then either:

```sh
./start-daemon.sh --push-to 192.168.1.50
```

or set `CLAWDMETER_PUSH_URL=192.168.1.50` in `.env`.

### 6. Check it's running

Look for the mascot icon in the menu bar (top-right). Click it to see the
transport-switch menu and live `5h % / 7d %`. If it's missing, check
`~/.clawdmeter-daemon.log` for what happened — a common cause is
`pyobjc-framework-Cocoa` not installing cleanly; rerun `./install.sh` and
watch for errors during that step.

---

## Common to both

- **`.env` file**: see `.env.example` in this folder — copy it to `.env` and
  fill in what you need (token, push target). Loaded automatically on every
  start, no flags required. Real env vars/exports still take priority if you
  set both.
- **Changing the push target later**: right-click (macOS: click) the tray
  icon → **Configure push targets…** — no restart, no file editing needed.
- **Uninstalling**: `uninstall.bat` (Windows) / `./uninstall.sh` (macOS) —
  removes the autostart registration and stops the running daemon.
- **Full flag reference, Google Calendar / Weather setup, troubleshooting**:
  see `README.md` in this same folder.
