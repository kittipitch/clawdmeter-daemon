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

When it finishes, open a **new** PowerShell and run `python --version`. It must print
3.10 or higher. If the Microsoft Store opens instead, that is the Store stub — uninstall
it (Settings → Apps) and use the python.org install.

### 2. Get this daemon onto your machine

Download/clone this repo, then open PowerShell **in this folder** — Shift+right-click
the folder → *Open PowerShell window here*. Type the commands; do not double-click the
`.bat` files (they take flags, and a double-clicked window closes before you can read
an error).

### 3. Run the installer

```bat
install.bat
```

This creates a self-contained `.venv` here, installs everything it needs
into it, and registers the daemon to start automatically at login (no admin
rights required).

### 4. Get a Claude token

You need a paid Claude plan for this; `claude setup-token` does not work on a free
account.

**Already use `claude` on this PC?** Skip this step — the daemon reads Claude Code's own
login. Do this only for a machine that never runs `claude` interactively, or when the
tray later says *Token expired*.

If `claude` is not a command here, install it first:
`npm install -g @anthropic-ai/claude-code`, then run `claude` once and log in.

```bat
claude setup-token
```

This **prints** a token starting `sk-ant-oat…` (it does not store it anywhere). Set it
permanently:

```bat
setx CLAUDE_CODE_OAUTH_TOKEN "sk-ant-oat...your-token..."
```

**Or**, instead of `setx`, put it in a `.env` file in this folder (copy
`.env.example` to `.env` and fill it in) — same effect, easier to find again
later. Either way, close and reopen your terminal afterward so the token is
picked up.

### 5. Point it at your device (if using a SmallTV over Wi-Fi)

Your device must be running the `kittipitch/smalltv-mod` firmware — upstream
`giovi321/smalltv-mod` only accepts the usage page, so calendar and weather stay blank
there. The web UI is `http://<device-ip>/` or `http://smalltv-XXXX.local/`.

Use the device's **hostname**, not its IP — it survives DHCP changes. The name is
`smalltv-XXXX` (four hex characters), shown on the device's **WiFi tab → Device name**,
and on screen for about 4 seconds right after power-on (unplug and replug to see it
again). Then either:

```bat
start-daemon.bat --push-to smalltv-XXXX.local
```

If the log later shows `[Errno 11001]` or `nodename nor servname`, mDNS is not resolving
on this machine — fall back to the IP from your router and give the device a DHCP
reservation.

Or set it once in `.env` (`CLAWDMETER_PUSH_URL=smalltv-XXXX.local`) so you never
need the flag again — the tray icon also remembers whatever you last
configured via its **Configure push targets…** menu item.

### 6. Check it's running

Look for the mascot icon in the system tray (it may start in the `⌃`
overflow area — drag it onto the taskbar to pin it). Hover it to see live
`5h % / 7d %`. If you don't see it, open `%USERPROFILE%\.clawdmeter-daemon.log`
(paste that into Explorer's address bar) and read the last lines.

**What success looks like:** within about 30 seconds the device swaps the animated
mascot for two percentage bars. To watch it happen, run once in the foreground first:

```bat
.venv\Scripts\python clawdmeter_daemon.py --no-tray --push-to smalltv-XXXX.local
```

You want a `5h=12% sr=143 7d=4% wr=9876 st=allowed` line, then
`Pushing to http://smalltv-XXXX.local/api/usage OK`. `Polling Claude every 60s` followed
by silence means no usable token (step 4).

---

## macOS

### 1. Install Python

macOS ships a Python, but Homebrew's is the one to use for this — the system one blocks
`pip install` (PEP 668) and is often 3.9, too old. No Homebrew yet? Install it from
[brew.sh](https://brew.sh) first, then:

```sh
brew install python
python3 --version        # must be 3.10 or higher
```

### 2. Get this daemon onto your machine

Clone or download this repo, then open Terminal in this folder
(`clawdmeter-daemon`).

### 3. Token and push target FIRST, then the installer

On macOS `./install.sh` **starts the daemon immediately** (its LaunchAgent has
`RunAtLoad`), and `.env` is read once at startup. So write `.env` before you install, or
the running copy will not see it and you will end up with two menu-bar icons:

```sh
cp .env.example .env
# put CLAUDE_CODE_OAUTH_TOKEN=... (step 4, only if needed) and
# CLAWDMETER_PUSH_URL=smalltv-XXXX.local in it
chmod +x install.sh
./install.sh
```

This creates a self-contained `.venv` here (with `--system-site-packages` so
the tray can still see native GTK/AppIndicator-equivalent bits), installs
everything, registers a LaunchAgent, and starts it — a mascot appears in the menu bar
within a few seconds. You do not also need `./start-daemon.sh`.

Any later `.env` change needs a restart: **Quit** from the menu-bar icon, then
`./start-daemon.sh`.

> ⚠ **The LaunchAgent it writes is fine for Claude usage and for the HTTP-only
> features** (Weather, Calendar, z.ai, OpenRouter) — those need only the remembered
> config and the `.env` beside the script. What it lacks is a `PATH`, so anything that
> shells out to another program fails silently: `codex`, `agy` (Antigravity) and `trans`
> (non-English calendar titles). For those, write the plist by hand — there is a complete
> working one in [AUTHENTICATION.md](AUTHENTICATION.md) under "New machine, start to
> finish", step 6.
>
> It also builds the venv from whatever `python3` resolves to, which on a stock
> macOS is **3.9** — too old; the daemon dies at import. Use Homebrew's:
> `PYTHON=$(brew --prefix)/bin/python3 ./install.sh`.
>
> If you see `TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'`, an
> old 3.9 `.venv` is being reused — `rm -rf .venv`, then run the line above.

### 4. Get a Claude token

**Already use `claude` on this Mac?** Skip this — the daemon reads that login from the
Keychain. Needed only on a machine that never runs `claude`, or when the icon says
*Token expired*. No `claude` command? `npm install -g @anthropic-ai/claude-code`, then
run `claude` once and log in.

```sh
claude setup-token
```

**Prints** a token starting `sk-ant-oat…` — it stores nothing. Add it either to your shell profile
(`~/.zshrc` for the default shell) or to `.env`. The shell profile works for running the
daemon by hand only — a service manager never reads a shell profile, so if you later
install it to start automatically, move the token to `.env`; see
[AUTHENTICATION.md](AUTHENTICATION.md):

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
./start-daemon.sh --push-to smalltv-XXXX.local
```

or set `CLAWDMETER_PUSH_URL=smalltv-XXXX.local` in `.env` (preferred on macOS — see
step 3).

### 6. Check it's running

**What success looks like:** within about 30 seconds the device swaps the animated
mascot for two percentage bars. To watch the log while it happens:

```sh
.venv/bin/python clawdmeter_daemon.py --no-tray --push-to smalltv-XXXX.local
```

You want a `5h=…` line and then `Pushing to http://smalltv-XXXX.local/api/usage OK`.
`Polling Claude every 60s` and nothing after it means no usable token (step 4). Ctrl-C
when satisfied; the LaunchAgent copy keeps running.

Look for the mascot icon in the menu bar (top-right). Click it to see the
transport-switch menu and live `5h % / 7d %`. If it's missing, check
`~/.clawdmeter-daemon.log` for what happened — a common cause is
`pyobjc-framework-Cocoa` not installing cleanly; rerun `./install.sh` and
watch for errors during that step.

---

## Linux

Same three ideas, fewer surprises:

```sh
sudo apt install python3-venv          # Debian/Ubuntu; the installer says so if missing
git clone https://github.com/kittipitch/clawdmeter-daemon
cd clawdmeter-daemon
cp .env.example .env                   # CLAWDMETER_PUSH_URL=smalltv-XXXX.local
chmod +x install.sh && ./install.sh    # .venv + deps + a .desktop autostart entry
.venv/bin/python clawdmeter_daemon.py --no-tray --push-to smalltv-XXXX.local   # watch it work
```

The autostart entry starts at your next **graphical** login, not immediately. The tray
icon needs the AppIndicator + GTK packages `install.sh` names for your distro (and, on
GNOME Wayland, the *AppIndicator and KStatusNotifier Support* extension); without them
the daemon still runs, just headless. `.local` addresses need `avahi-daemon` running.

---

## Common to both

- **`.env` file**: copy `.env.example` to `.env` in this folder. The whole minimum is
  two lines:

  ```ini
  # only needed if this machine never runs `claude` interactively
  CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat...
  CLAWDMETER_PUSH_URL=smalltv-XXXX.local
  ```

  Loaded automatically at every start, no flags required; real env vars/exports take
  priority. It is read **once at startup**, so restart the daemon after editing it.
- **Changing the push target later**: right-click (macOS: click) the tray
  icon → **Configure push targets…** — no restart, no file editing needed.
- **Making it permanent**: the installers already register autostart (Windows Run key,
  macOS LaunchAgent, Linux `.desktop`). Verify with
  `.venv/bin/python clawdmeter_daemon.py --autostart-status`. For a headless box — a
  Raspberry Pi or server with no desktop — use a `systemd --user` service plus
  `loginctl enable-linger` instead; full recipe:
  <https://kittipitch.github.io/smalltv-mod/getting-started/keep-it-running/>.
- **Uninstalling**: `uninstall.bat` (Windows) / `./uninstall.sh` (macOS) —
  removes the autostart registration and stops the running daemon.
- **Full flag reference, Google Calendar / Weather setup, troubleshooting**:
  see `README.md` in this same folder.
