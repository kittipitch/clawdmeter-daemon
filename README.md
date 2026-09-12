# clawdmeter-daemon

> New to this? See **[GUIDE-WINDOWS-MAC.md](GUIDE-WINDOWS-MAC.md)** for a
> step-by-step Windows/macOS setup walkthrough. This README is the full
> reference.

**In plain words:** the desk device cannot talk to Anthropic itself. This small program
runs on your computer, asks Anthropic how much of your Claude plan you have used, and
sends two percentages to the device over your home WiFi every 20 seconds. If your
computer sleeps or the program stops, the device keeps the last numbers and after a few
minutes dims the `CLAUDE` heading to mark the reading stale — which is why the installer
registers it to start at login.

### Reading this to set someone up? (human helper or AI assistant)

Ask these four questions **before** giving any command. Each one changes the path:

1. **Which OS runs the daemon** — macOS, Windows, Linux desktop, or a headless box
   (Raspberry Pi, server)? The headless case needs a systemd user service, not autostart.
2. **Where is the device right now — home network, or on the go?**
   - *Home, and the router has a 2.4 GHz SSID:* join the device to it once — a flashed
     device that has never been configured brings up its own setup AP at
     `http://192.168.4.1`, see
     [First-time setup](https://kittipitch.github.io/smalltv-mod/getting-started/setup/) —
     and after that nothing extra is needed.
   - *On the go (hotel, workshop, train), or the router is 5 GHz only / band-steered /
     isolates clients:* the device still needs some 2.4 GHz network to join, and that is a
     prerequisite, not an optional extra. Ask first whether the device can simply join the
     **same** network the laptop is already on — if that network has a 2.4 GHz SSID and
     does not isolate clients, nothing on this list is needed.

     If it cannot, there are two reliable answers, and on a **Mac** they are the only two:

     1. **A phone.** Either a hotspot forced to 2.4 GHz (iOS *Maximize Compatibility*,
        Android's 2.4 GHz option) with the laptop joined to it too — mobile data is the
        uplink and the laptop shares nothing — or, on a Mac, an iPhone connected by
        **USB** as the uplink, with macOS Internet Sharing then broadcasting over WiFi.
     2. **A small, cheap piece of hardware.** A pocket 2.4 GHz travel router (GL.iNet
        GL-MT300N-V2, TP-Link TL-WR802N and similar, around USD 30) is the one to own if
        you travel with the device often: it makes its own 2.4 GHz network and never asks
        the laptop for anything. A **USB Ethernet** adapter also works wherever there is a
        wired port — it gives the Mac a non-WiFi uplink, which is all Internet Sharing
        needs. A USB **WiFi** adapter is not an option on Apple Silicon: macOS has no
        third-party WiFi driver interface there, so those dongles do not work at all.

     Why a Mac has no third answer: macOS refuses to share WiFi out over the same WiFi
     adapter it is receiving on, in any configuration — the hardware can do it, the OS
     will not offer it. A **Linux** laptop sometimes can (AP+STA on one radio, locked to
     the channel its own connection is already using), and **Windows** Mobile Hotspot
     does it outright. Send them to
     [Sharing your laptop's WiFi](https://kittipitch.github.io/smalltv-mod/getting-started/sharing-wifi/)
     and finish that before returning here.
3. **Does this machine already run `claude` interactively?** If yes, no token step at
   all — the daemon reads that login. If no, install Claude Code
   (`npm install -g @anthropic-ai/claude-code`, needs Node.js LTS) and either sign in with
   `claude`, or run `claude setup-token` on a machine where you are signed in and paste the
   token into `.env`. A paid plan is required either way. If the answer to question 1 was
   *headless*, always use a `setup-token` in `.env` — a box nobody logs into keeps no
   refreshable login.
4. **What is the device's hostname?** Its web UI, **WiFi tab → Device name → Hostname**,
   something like `smalltv-3fa2`. Everything below needs it, and guessing wastes an hour.

Then follow [Daemon setup, start to finish](https://kittipitch.github.io/smalltv-mod/getting-started/first-setup/),
which has the whole path per OS with the output each command should print.

**Setting this up for the first time?** The click-by-click guides live in the firmware
docs: [what to install](https://kittipitch.github.io/smalltv-mod/getting-started/daemon-requirements/),
[Google Calendar](https://kittipitch.github.io/smalltv-mod/getting-started/google-calendar/),
[sharing your laptop's WiFi](https://kittipitch.github.io/smalltv-mod/getting-started/sharing-wifi/).

It polls the Claude API rate-limit headers (using the OAuth token Claude Code already stores
on your machine) and delivers your **5-hour** and **7-day** usage to the device by
whichever transport fits your setup:

**If you have a SmallTV, use push** — `--push-to smalltv-XXXX.local` — and leave the
Usage daemon URL blank on the device. The other two are for other situations:

| Transport | How | For |
|-----------|-----|-----|
| **push** *(use this)* | HTTP `POST` to the device | any **SmallTV** on your WiFi; also the only one that works when the device cannot open a connection back to your PC (PC firewall, VPN on the PC). Full client isolation blocks *both* directions — that needs your own hotspot instead, see Sharing your laptop's WiFi |
| **serve** | HTTP server the device polls | a SmallTV in pull mode, n8n, anything — needs your PC's IP typed into the device and a firewall hole |
| **serial** | writes JSON lines over USB CDC | the original **Clawdmeter** (ESP32‑S3, USB‑attached) — different hardware, not a SmallTV |

**Pick exactly one.** They share the same poller, token handling and tray icon, but the
daemon runs a single transport at a time — if you pass several, `--serve` beats `--push`/`--push-to`, which beat `--serial`, whatever order you type them in.

This merges the two device-specific daemons into one:
- **Clawdmeter (ESP32‑S3, serial):** https://github.com/giovi321/clawdmeter-win
- **SmallTV (ESP8266, HTTP):** https://github.com/kittipitch/smalltv-mod — the
  fork this daemon is built for. Plain upstream `giovi321/smalltv-mod` only has
  `/api/usage`; the Calendar/Weather/z.ai/OpenRouter/Codex/Antigravity features this
  README documents need the fork's `/api/calendar`, `/api/weather`, `/api/zai`,
  `/api/openrouter`, `/api/codex`, and `/api/antigravity` endpoints, which
  upstream doesn't have.

> Not affiliated with Anthropic. The throwaway API call it makes (cheapest model,
> `max_tokens: 1`) is only to read the rate-limit response headers.

## Install

Needs Python 3.10+. Works on Windows, macOS and Linux.

```sh
git clone https://github.com/kittipitch/clawdmeter-daemon
cd clawdmeter-daemon
ls          # clawdmeter_daemon.py  install.sh  install.bat  requirements.txt  .env.example
```

Downloaded the ZIP instead of cloning? The shell scripts lose their execute bit —
`chmod +x install.sh start-daemon.sh uninstall.sh` first.

### The whole thing, minimally, so this file is enough on its own

macOS / Linux, device named `smalltv-3fa2`:

```sh
python3 --version                                   # must be 3.10+
claude --version                                    # if "command not found", see the note below
cp .env.example .env                                # then set CLAWDMETER_PUSH_URL=smalltv-3fa2.local
./install.sh                                        # .venv + deps + autostart (macOS: also starts it now)

# One foreground test run to see the log. It never exits on its own -- read a few
# lines, then press Ctrl+C. On macOS install.sh has ALREADY started a background
# copy, so quit this one rather than leaving two running.
.venv/bin/python clawdmeter_daemon.py --no-tray --push-to smalltv-3fa2.local
```

Windows PowerShell:

```powershell
python --version
claude --version                                    # if not found, see the note below
Copy-Item .env.example .env                         # then set CLAWDMETER_PUSH_URL=smalltv-3fa2.local
.\install.bat

# One foreground test run. It never exits on its own -- read a few lines, Ctrl+C.
.venv\Scripts\python clawdmeter_daemon.py --no-tray --push-to smalltv-3fa2.local
```

**No `claude` on this machine?** The daemon reads an existing Claude Code login, so
install it and sign in once — `npm install -g @anthropic-ai/claude-code` (needs Node.js
LTS), then run `claude` and log in. No interactive login available (a headless Pi or
server)? Run `claude setup-token` on a machine where you *are* logged in and put the
token it prints into `.env` as `CLAUDE_CODE_OAUTH_TOKEN=`. Either way a **paid** Claude
plan is required; the free tier reports no usage.

You are looking for a `5h=…` line and then `Pushing to http://smalltv-3fa2.local/api/usage
OK`, and for the device to swap its mascot for two percentage bars within ~30 s. The
device's own web UI is `http://smalltv-3fa2.local/` (or `http://<its-ip>/`); its name and
IP appear on screen for ~4 s at power-on, under **WiFi tab → Device name → Hostname** in
that UI, and in your router's client list. A device still showing the mascot may simply
be rotating: **Display → Mode → Claude usage** pins the page while you test.

Prefer the wrapper that installs deps and registers login autostart in one
step: `install.bat` (Windows) / `./install.sh` (macOS/Linux). Both create a
self-contained `.venv` beside the script and register autostart to use it, so the
daemon always starts with its dependencies. This matters because the interpreter a
launcher resolves at start time is not always the one you installed deps into — on
Windows the Microsoft Store Python installs packages to a sandboxed location another
launch can't import, which silently drops the tray to headless; on modern
Homebrew/Debian the system Python refuses a plain `pip install` (PEP 668). The
macOS/Linux venv uses `--system-site-packages` so a Linux tray still sees the system
GTK/AppIndicator bindings.

A manual `pip install -r requirements.txt` into your own environment works too, as
long as you launch the daemon with that same interpreter.

`httpx` and `python-aqi` are imported unconditionally, so both are required;
`google-auth` is needed for `--calendar`; `pyserial` is only needed for `--serial`, `pystray` +
`Pillow` only for the tray icon, and `zeroconf` only for mDNS auto-discovery on
`--push` (without it, push still works via explicit `--push-to` hosts). On
**macOS** the tray also needs `pyobjc-framework-Cocoa` (auto-installed by the
requirements marker). On **Linux** the tray needs the AppIndicator + GTK system
packages and `python3-tk` for the push-targets dialog — `install.sh` prints the
exact command for your distro; without them the daemon runs headless.

## Quick start

**Do the token first.** If this machine already runs `claude`, you are done — the daemon
reads that login. Otherwise finish [Authentication](#authentication-the-durable-way)
before you start the daemon, or the log will poll forever with no `5h=` line.

**Which `python` to type.** The installers create a `.venv` beside the script and put
the dependencies *inside it*, so a bare `python`/`python3` will fail with
`ModuleNotFoundError: No module named 'httpx'`. Use the venv's own interpreter in every
command below:

| | Run the daemon with |
|---|---|
| macOS / Linux | `.venv/bin/python clawdmeter_daemon.py ...` |
| Windows (PowerShell) | `.venv\Scripts\python clawdmeter_daemon.py ...` |

(The examples below write plain `python` for brevity — substitute one of those.)

Three things trip up first-time setups, each with a guide:

| Problem | Guide |
|---|---|
| "What do I actually need installed?" | [What the daemon needs installed](https://kittipitch.github.io/smalltv-mod/getting-started/daemon-requirements/) — per feature, incl. `translate-shell` for non-English calendar titles |
| "The calendar page stays empty" | [Google Calendar (service account)](https://kittipitch.github.io/smalltv-mod/getting-started/google-calendar/) — click by click |
| "The device can't see my WiFi" | [Sharing your laptop's WiFi](https://kittipitch.github.io/smalltv-mod/getting-started/sharing-wifi/) — the device is 2.4 GHz only |

```sh
python clawdmeter_daemon.py --serial                 # USB Clawdmeter (auto-detect COM)
python clawdmeter_daemon.py --serial COM5            # ...or a specific port
python clawdmeter_daemon.py --push                   # push to every SmallTV it finds (mDNS).
                                                     # Discovery feeds the USAGE page only --
                                                     # calendar/weather/quota pages need --push-to
python clawdmeter_daemon.py --push-to 192.168.1.50   # push to a specific SmallTV (or smalltv.local)
python clawdmeter_daemon.py --serve --port 8787      # serve: device polls you (set the device's
                                                     # Usage daemon URL to http://<pc-ip>:8787/)
python clawdmeter_daemon.py --no-tray --push-to smalltv-XXXX.local   # foreground, watch the log
python clawdmeter_daemon.py --no-tray --serve        # headless console
```

With no transport flag it defaults to `--push` if `CLAWDMETER_PUSH_URL`
(or a remembered push target) is set, otherwise `--serve` on `:8787`.

**Flags are sticky.** Every feature flag and `--push-to` target is remembered in
`~/.clawdmeter-daemon.json` and re-applied on every later start, including autostart.
Turn a feature off with its `--no-...` flag (`--no-calendar`, `--no-antigravity`, …);
simply leaving the flag out does nothing. That matters for `--antigravity`, where every
poll is billable.

### Your first run: what you should see

Run it once in the foreground, so you can read the log:

```sh
.venv/bin/python clawdmeter_daemon.py --no-tray --push-to smalltv-XXXX.local
```

```text
Polling Claude every 60s
HTTP push every 20s (static targets + mDNS discovery)
clawdmeter-daemon: transport = push
5h=12% sr=143 7d=4% wr=9876 st=allowed          <- your numbers, within a few seconds
Pushing to http://smalltv-XXXX.local/api/usage OK   <- printed once, within ~20 s
```

**On the device, within about 30 seconds, the animated mascot is replaced by two
percentage bars.** That is what success looks like. After that the log is quiet: one
`5h=` line per minute, nothing else.

If you get `Polling Claude every 60s` and then silence, with no `5h=` line, the daemon
has no usable Claude token — see Authentication below. If you get `5h=` lines but the
device never changes, the push target is wrong: check the hostname on the device's WiFi
tab, and that `Display → Mode` is **Claude usage** (a fresh device boots into Carousel,
so the usage page is only on screen part of the time).

Then stop it with Ctrl-C and start it the normal way (`./start-daemon.sh`,
`start-daemon.bat`, or autostart from the installer).

**On the device itself, five things and nothing else:**

1. joined to your 2.4 GHz WiFi
2. **WiFi tab → Device name** — this is the `smalltv-XXXX` you push to
3. **Display → Mode** — Claude usage (or Carousel with Usage ticked)
4. **Usage tab → Usage daemon URL** — leave **blank** for push
5. **Update tab → Daemon source IP** — blank, unless you are locking pushes to one IP

## Authentication (the durable way)

The daemon needs a Claude token. In order it tries:

> **Setting up a new machine?** Start with **[AUTHENTICATION.md](AUTHENTICATION.md)** —
> it opens with an ordered start-to-finish walkthrough (get Claude working first,
> then add one feature at a time, install the service last), and then serves as the
> per-harness token guide — Claude, Codex, Antigravity, z.ai, OpenRouter and Google
> Calendar — including which credentials can be copied between machines (Claude,
> z.ai, OpenRouter, Google service account) and which must be re-logged per machine
> (Codex, Calendar OAuth, Antigravity), plus the PATH/environment traps that make a
> service manager silently report no data.

1. **`CLAUDE_CODE_OAUTH_TOKEN`** env var — a **long-lived token** from
   `claude setup-token`. This is the robust choice for an always-on daemon: it
   doesn't expire, so there's nothing to refresh.
2. macOS Keychain, or `~/.claude/.credentials.json` on Linux and Windows (run `claude`
   once and complete `/login` to create it). On **Linux and Windows** this path renews
   itself: the daemon checks expiry, runs the OAuth refresh grant, and can briefly spawn
   `claude`.

   ⚠ **On macOS there is no refresh.** `read_token()` returns the Keychain value and
   returns right there — no expiry check, no refresh grant, no spawning `claude`. A Mac
   you use interactively stays fine because your own `claude` use keeps that Keychain
   item fresh; an unattended Mac eventually sends an expired token, gets 401s and goes
   quiet with no error in the log. For anything unattended, use
   `CLAUDE_CODE_OAUTH_TOKEN`.

**How to tell a token was actually found:** there is no "token source" line in the log —
the proof is the `5h=…` line appearing within a few seconds of `Polling Claude every 60s`.
If it never appears, no usable token was found (on macOS you also get a
`Keychain read failed: … exit status 44` line per poll when `claude` has never logged in
there). The tray icon carries the reason: hover or click it.

The on-disk session credentials expire (often every few hours) and, for some
subscription logins, carry **no refresh token** — then nothing can renew them
headlessly. So for a set-and-forget daemon:

```sh
claude setup-token        # subscription required; prints a token (sk-ant-oat…)
```

Then put it where the daemon will actually see it. **A shell profile is not such a
place** — service managers (systemd, launchd) never read `~/.zshrc`, `~/.bashrc` or
`~/.zprofile`, so a token exported there works in your terminal and is invisible to
the running daemon:

```sh
# .env beside clawdmeter_daemon.py — read by the daemon itself
umask 077
printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\n' 'sk-ant-oat...' >> .env   # >> appends; a single > would
                                                              # wipe the CLAWDMETER_PUSH_URL line

# or, under systemd: ~/.config/clawdmeter/token.env + EnvironmentFile=
```

One `KEY=VALUE` per line, no `export`, and **no line break inside the token** — a
wrapped paste is silently ignored. See **[AUTHENTICATION.md](AUTHENTICATION.md)**
for the per-harness details.

## Tray icon + autostart (Windows, macOS, Linux)

By default the daemon shows a **tray / menu-bar icon** (the mascot): grey while
waiting, red if you're not logged in, full colour once it's serving data. Hover for
live `5h % / 7d %`. **Right-click (macOS: click) to pick the transport** — *Serial
(USB)* / *HTTP push to device* / *HTTP serve* — which switches **live** and is
**remembered** (in `~/.clawdmeter-daemon.json`), plus **Configure push targets…**,
**Refresh now** and **Quit**. So you don't need flags after the first run; the tray
is the switch. **Configure push targets…** opens a box to type one or more device
IPs/hostnames (comma-separated, e.g. `192.168.1.44, 192.168.1.45`); it applies
immediately and is remembered. Leave it blank to rely on mDNS auto-discovery only.
(*HTTP push* also seeds its targets from `CLAWDMETER_PUSH_URL` / `SMALLTV_PUSH_URL`
/ `--push-to`.)

### Autostart at login

`--install` registers the tray daemon to start at login, per-user and without admin,
using each OS's native mechanism — no hardcoded Python path (it registers the
interpreter you run it with):

| OS | Mechanism | Where |
|----|-----------|-------|
| Windows | `HKCU\…\Run` value (windowless `pythonw`) | Task Manager → Startup |
| macOS | LaunchAgent | `~/Library/LaunchAgents/com.giovi321.clawdmeter.plist` |
| Linux | XDG autostart `.desktop` (GUI session) | `~/.config/autostart/clawdmeter-daemon.desktop` |
| Linux, headless (no GUI session, e.g. Raspberry Pi) | `systemd --user` service — **not written by `--install`**, which only ever drops an XDG `.desktop` that a headless box never runs. Follow the manual recipe [below](#linux-headless--no-gui-session-systemd---user-service) | `~/.config/systemd/user/clawdmeter.service` |

```sh
python clawdmeter_daemon.py --install            # register autostart at login
python clawdmeter_daemon.py --uninstall          # remove it
python clawdmeter_daemon.py --autostart-status   # show what's registered
```

The autostart command is just `--tray`; the transport comes from the remembered
config and the env vars above, so autostart needs no edits — set them once (e.g.
`CLAWDMETER_PUSH_URL=smalltv-XXXX.local` for push, otherwise it serves on `:8787`).

⚠ **`CLAWDMETER_PUSH_URL` only seeds an EMPTY config.** `main()` does
`if not cfg.get("push_url"): cfg["push_url"] = os.environ.get("CLAWDMETER_PUSH_URL")…`,
so once a target is saved in `~/.clawdmeter-daemon.json` — one `--push-to` run, or one use
of the tray's **Configure push targets…**, does that — editing `.env` has **no effect at
all**, silently. Change it later with `--push-to <host>`, the tray dialog, or by editing
that JSON file. The transport is sticky the same way: a remembered `serve` beats an
env-derived push default.

Convenience scripts wrap dependency install + `--install`:

- **Windows** — `install.bat` (creates `.venv`, installs deps, registers autostart),
  `start-daemon.bat [flags]` (start now, silent, using the `.venv` interpreter),
  `uninstall.bat` (remove autostart, stop the process, and clear the **legacy**
  `SmallTVUsageDaemon` / `ClaudeUsageDaemon` shortcuts this merged daemon replaced).
- **macOS / Linux** — `./install.sh`, `./start-daemon.sh [flags]`, `./uninstall.sh`
  (set `PYTHON=/path/to/python3` to force an interpreter).

> Windows Microsoft-Store `pythonw` stub, or a non-default interpreter? Set
> `CLAWDMETER_PYTHONW` before `start-daemon.bat`, e.g.
> `set CLAWDMETER_PYTHONW=C:\Python314\pythonw.exe`.

> The tray icon starts in the Windows 11 `⌃` overflow area — drag it onto the
> taskbar to pin it. On **GNOME/Wayland** there is no tray at all without the
> *AppIndicator and KStatusNotifier Support* extension; without a usable tray backend
> the daemon logs a note and runs headless (it keeps working, just no icon).

Anything the daemon logs also goes to **`~/.clawdmeter-daemon.log`** — the place to
look if a windowless/headless launch seems to do nothing.

### Linux headless / no GUI session (systemd `--user` service)

The XDG autostart `.desktop` above only fires inside a **GUI session** — it
does nothing on a headless box (a Raspberry Pi with no desktop, a server, a
container). For that case, run the daemon as a `systemd --user` service
instead: it starts at boot without needing anyone to log into a desktop,
restarts itself if it crashes, and its logs go to the normal system
journal instead of a flat file.

```sh
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/clawdmeter.service <<'EOF'
[Unit]
Description=clawdmeter-daemon (Claude usage -> device)
After=network-online.target
Wants=network-online.target

[Service]
# Point at your own .venv (created by install.sh) and pick the flags/
# transport you actually want -- this is just an example combination.
ExecStart=%h/clawdmeter-daemon/.venv/bin/python %h/clawdmeter-daemon/clawdmeter_daemon.py --push-to smalltv.local --no-discover --no-tray
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now clawdmeter
```

Three things a headless box needs that a desktop session gets for free:

- **`loginctl enable-linger $(whoami)`** (run once, as a normal user, not
  root) — without this, `--user` services stop the moment your SSH session
  ends, since there's no login session left to own them. With linger set,
  the service keeps running after you log out and starts again on reboot
  before anyone logs in at all.
- **Secrets go in the environment, not the unit file.** Put
  `CLAUDE_CODE_OAUTH_TOKEN=...` (and any of `CLAWDMETER_ZAI_KEY`, etc. you
  use) in a separate file, e.g.
  `~/.config/clawdmeter/token.env` (`chmod 600` it), and reference it from
  the unit instead of pasting the token into `ExecStart` — add
  `EnvironmentFile=%h/.config/clawdmeter/token.env` under `[Service]`
  above.
- **`systemd --user` services run with a minimal PATH** that does NOT include
  `~/.local/bin` or npm global directories. This matters if you use features
  that shell out to external CLIs — `codex` (Codex quota) and `trans`
  (translate-shell for non-English calendar titles) will be silently "not
  found" (Codex reports no rate limits, `trans` is simply skipped). Fix: add
  an explicit `Environment=PATH=` line under `[Service]` with the full path,
  e.g.:

  ```
  Environment=PATH=/home/YOU/.local/bin:/home/YOU/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  ```

  **macOS LaunchAgents have the same problem** — launchd's default PATH is
  `/usr/bin:/bin:/usr/sbin:/sbin`, with no Homebrew and no `~/.local/bin`, so
  `codex`/`agy`/`claude` are invisible there too. Add an `EnvironmentVariables`
  dict with a `PATH` string to the plist (note that re-running `--install`
  rewrites the plist and drops it). Symlinking the tools into `/usr/local/bin`
  is an equally good fix on both platforms.

  The failure is not actually silent — the log says
  `` `codex` not found on PATH - check this daemon's actual runtime PATH ``.

Useful commands:

```sh
systemctl --user status clawdmeter          # is it running
journalctl --user -u clawdmeter -f          # follow its log live
systemctl --user restart clawdmeter         # apply a flag/unit-file change
```

Edit `ExecStart` any time you want to change flags (add `--calendar`,
switch `--push-to`, etc.), then `systemctl --user daemon-reload &&
systemctl --user restart clawdmeter` to pick it up.

## The payload contract

Every transport delivers the same object:

```json
{ "s": 29, "sr": 142, "w": 4, "wr": 9876, "st": "allowed", "ok": true }
```

| field | meaning |
|-------|---------|
| `s` / `w` | 5‑hour / 7‑day window utilization (%) |
| `sr` / `wr` | minutes until each window resets |
| `st` | rate-limit status (`allowed`, `allowed_warning`, `rejected`, …) |
| `ok` | `false` when there's no data (e.g. not logged in) |

- **serial:** one JSON line per update; reads `{"ready"}` / `{"refresh"}` back from
  the device to re-poll. `--no-hid` sends `{"hid":false}` on connect.
- **push:** `POST` to `http://<device>/api/usage`.
- **serve:** `GET http://host:port/` returns the latest object (`/healthz` too).

## Google Calendar (optional, off by default)

**Never set up a Google Cloud service account before? Follow the click-by-click guide:
<https://kittipitch.github.io/smalltv-mod/getting-started/google-calendar/>** — every page, every button, the calendar-share
step everybody misses, and a troubleshooting table. The section here is the reference
version for people who already know the console.

Polls upcoming events on a Google Calendar and pushes them to
`http://<device>/api/calendar`, alongside the usage payload above. Separate
feature, separate on/off flag — doesn't change anything about the usage side.

```json
{ "ok": true, "summary": "Team sync", "start": "2026-07-27T10:00:00+01:00", "end": "2026-07-27T11:00:00+01:00", "allDay": false }
```

The real payload is `{"ok": true, "events": [...]}` with up to **6** events, not
the single-event shape shown above — the firmware's agenda mode splits them across
two rotating pages. `end` mirrors `start` (timed `dateTime` or all-day `date`) so
multi-day events can be told apart from single-day ones; for all-day events it is
Google's exclusive end date, shipped raw. Field names are spelled out rather than
terse like the usage contract's `s`/`w`. This is live on every deployed unit — the
"no firmware consumer yet" note that used to be here is long out of date.

Two ways to authenticate. **Use the service account** unless you have a
specific reason not to — the interactive-OAuth path below is real but its
refresh token silently expires after 7 days unless you can publish the GCP
project to Production, which isn't always possible (this project's own
history: a different, unrelated OAuth client already in the project had a
non-HTTPS redirect URI, and Google refuses to publish while *any* client in
the project has one — the token expiry from this exact trap is what made the
service-account path get added).

### Option A: service account (recommended)

No consent screen, no browser, no refresh token to expire. The tradeoff: the
key file itself is a long-lived credential with no expiry, so treat it like a
password (see step 4).

1. [console.cloud.google.com/projectcreate](https://console.cloud.google.com/projectcreate)
   — create a project (any name), or reuse one you already have.
2. [Enable the Calendar API](https://console.cloud.google.com/apis/library/calendar-json.googleapis.com)
   for that project.
3. [console.cloud.google.com/iam-admin/serviceaccounts](https://console.cloud.google.com/iam-admin/serviceaccounts)
   — **Create Service Account** (any name, no roles needed). Open it → **Keys**
   tab → **Add Key** → **Create new key** → JSON. This downloads a `.json` key
   file. `chmod 600` it, then either:
   - save it as `~/.clawdmeter-google-service-account.json` (the daemon's
     default path, zero config needed), **or**
   - keep it wherever you like and point at it via `.env` (same file the
     daemon already loads `CLAWDMETER_ZAI_KEY`/etc. from — one in this
     script's own directory, one in your cwd):
     ```
     GOOGLE_APPLICATION_CREDENTIALS=/path/to/your-key.json
     ```
     This is Google's own standard env var name for a service-account key
     path, recognized by their other client libraries too — not invented for
     this project.
4. Note the service account's email from the file (`client_email`, looks like
   `something@your-project.iam.gserviceaccount.com`). In
   [Google Calendar](https://calendar.google.com) → your calendar's settings
   → **Share with specific people** → add that email, permission **"See event
   details"** (confirmed live: there is no separate read-only "see all" tier —
   the options are "See only free/busy", "See event details", then two "Make
   changes..." edit tiers; pick "See event details"). This is the only way a
   service account can see a personal Gmail calendar — there's no admin
   console to grant broader access on a non-Workspace account.
5. `pip install google-auth` (already in `requirements.txt`).
6. Find your calendar's ID — for your primary calendar it's just your Gmail
   address. Then enable with an **explicit `--calendar-id`**:

   ```
   python clawdmeter_daemon.py --calendar --calendar-id you@gmail.com --push-to <device>
   ```

   `--calendar-id` is **required** with a service account — the default
   auto-detect relies on Google's "selected calendars" sidebar state, which a
   service account doesn't have (it isn't a real Google Calendar user, just a
   grantee on the calendars you explicitly shared). Omitting it doesn't error;
   it silently polls to zero events forever, which looks like success. The
   daemon logs a one-time startup warning only when **no push target** is
   configured; with a push target it reads the device's own **Calendar ID(s)**
   field instead, and if that is empty too it stays silent and still returns
   zero events.

   **Alternative to the CLI flag: set it on the device instead.** Google's
   Calendar API gives a service account no way to discover a calendar that's
   been shared with it — there is no "list calendars shared with me" endpoint
   anywhere in the API (confirmed against Google's own docs; there's also a
   filed Google issue specifically about this:
   [issuetracker.google.com/issues/148804709](https://issuetracker.google.com/issues/148804709)).
   So every time you share a new calendar with the service account, *something*
   has to be told its ID by hand. Instead of editing `--calendar-id` and
   restarting the daemon, the device's own web UI (Agenda & weather tab →
   "Calendar ID(s)") accepts the same comma-separated format — the daemon
   re-reads that field from the device's own `/api/config` on every poll
   cycle (same pattern as `--weather` already uses for lat/lon), so sharing a
   calendar and pasting its ID into the device is enough; no restart needed.
   If both are set, the device's field wins; `--calendar-id` is only used
   when the device's field is empty (or there's no `--push-to` target to read
   it from).

If the daemon runs elsewhere (e.g. a headless Pi), copy the key file there
the same way as the OAuth token below (`scp
~/.clawdmeter-google-service-account.json <host>:~/`).

**Event colors will look wrong (or all the same) until you run one more
command.** A service account can't see a calendar's real color on its own —
`backgroundColor` only exists on a per-viewer `CalendarListEntry`, and
sharing a calendar doesn't add one to the grantee's own list. Without this
step every event falls back to a Google-assigned arbitrary color, not the
one you actually see in your own Google Calendar:

```
python clawdmeter_daemon.py --calendar-auth          # one-time, if you haven't already
python clawdmeter_daemon.py --calendar-sync-color you@gmail.com
```

The first command is needed only because reading *your own* view of the
color requires *your own* OAuth login — there's no way around that, even a
domain-wide-delegated service account can't do it on a personal Gmail
account (no Workspace admin console to grant that). It's a one-time read,
not an ongoing dependency: the color gets applied to the service account's
own `calendarList` entry and stays there. Re-run `--calendar-sync-color` any
time you change the calendar's color in Google's UI.

**One real limitation, not fixable**: Google Calendar's API only accepts
colors from its fixed 24-color palette for a `calendarList` entry. If
you've picked a **custom color** beyond that palette (Google Calendar's UI
allows this), the closest the API can do is the nearest palette color, not
your exact hex — confirmed by testing directly against Google's API, not an
assumption. `--calendar-sync-color` gets you the closest possible match
either way.

### Option B: interactive OAuth ("Desktop app" client)

**One-time setup** (you do this once, on any machine with a browser — not
over a headless SSH session, since the OAuth redirect must land on the same
machine that's waiting for it):

```
python clawdmeter_daemon.py --calendar-auth
```

With no Google OAuth client configured yet, this prints step-by-step
instructions (create a Google Cloud project, enable the Calendar API, create
a "Desktop app" OAuth client, save its `client_id`/`client_secret` to
`~/.clawdmeter-google-client.json`). Re-run the command afterwards — it opens
your browser for one-time consent, then saves a **refresh token** to
`~/.clawdmeter-google-token.json`. After that, no more browser needed —
polling refreshes its access token silently, same as `claude setup-token`
being a one-time thing on the Claude side. If the daemon runs elsewhere (e.g. a headless
Pi), copying that one file there has worked once and failed once with
`invalid_grant` — even while the source machine kept refreshing the same file
successfully. Treat it as unreliable and prefer the service account, or run
`--calendar-auth` on the target machine over SSH. It binds a **random** loopback
port at runtime, so the tunnel cannot be set up in advance — it takes two terminals:
(1) `ssh host`, run `--calendar-auth`, read the port out of the URL it prints;
(2) in a second terminal `ssh -L <port>:127.0.0.1:<port> host`; (3) open that URL in
your local browser. `~/.clawdmeter-google-client.json` must already be on the target
machine.

> Keep the OAuth consent screen in "Testing" and Google expires your refresh
> token after 7 days — you'd have to re-run `--calendar-auth` weekly. Publish
> it to "Production" instead (the setup instructions above cover this) for a
> refresh token that just keeps working — but see the note at the top of this
> section for a case where that's not achievable, which is exactly why
> Option A exists.

Then enable it:

```
python clawdmeter_daemon.py --calendar --push-to <device>
```

If both `~/.clawdmeter-google-service-account.json` and
`~/.clawdmeter-google-client.json`/`~/.clawdmeter-google-token.json` are
present, the service account wins — the OAuth files are only read as a
fallback.

### Non-English event titles (optional, auto-detected — no flag)

The device's font can only render ASCII, so a non-English event title (Thai,
Japanese, emoji, etc.) would otherwise get silently stripped down to
whatever ASCII characters happen to remain. If [translate-shell]
(https://github.com/soimort/translate-shell)'s `trans` command is
installed and on `PATH`, `poll_calendar()` automatically romanizes/
translates any title containing a non-ASCII character to English before
pushing it — confirmed live against a real Thai event
(`กาดโก้งโค้ง`, a Chiang Mai market) correctly coming back as
`Kad Kong Khong` rather than a garbled or empty string. English titles are
left untouched (never sent through `trans` at all — cheap local ASCII check
first).

Install it:

| OS | Installation |
|----|---------------|
| macOS | `brew install translate-shell` |
| Debian/Ubuntu/Raspberry Pi OS | `sudo apt install translate-shell` |
| Other Linux (no package manager) | Clone and build (requires `gawk`):<br>`git clone --depth 1 https://github.com/soimort/translate-shell.git && cd translate-shell && gawk -f build.awk build && cp build/trans ~/.local/bin/trans` |
| Windows | Only if the **whole daemon** runs under WSL — a `trans` installed in WSL is invisible to a native-Windows daemon. Otherwise skip it (titles are stripped device-side instead) |

**Nothing to configure — no flag, no env var.** If `trans` isn't found on
`PATH`, the daemon logs one line the first time it would've needed it
(`Calendar: \`trans\` (translate-shell) not found on PATH — non-English
event titles will be stripped device-side instead of translated`) and
falls straight back to the pre-existing behavior (the device's own
`stripNonAscii()` drops what it can't render) — every other feature keeps
working exactly as before, nothing crashes or blocks waiting for a binary
that isn't there. This is why installing it is optional and gets no on/off
flag of its own: the code path degrades to "as if this feature didn't
exist" rather than needing to be explicitly disabled.

Each real translation call is a live network round-trip (~5–6s, confirmed
live) — results are cached per exact title string so a repeating event
only ever pays that cost once, not on every 300s poll.

## Weather + AQI (optional, off by default)

Fetches current temperature, rain probability, WMO weather code, PM2.5, and
US AQI from [Open-Meteo](https://open-meteo.com/) — free, no API key — and
pushes them to `http://<device>/api/weather`. The **location comes from
the device itself**, not a daemon flag: set lat/lon in the device's own web
UI (Agenda & weather tab), and the daemon reads it back via `GET
/api/config` each poll.

```json
{ "ok": true, "tempC": 26.0, "precipPct": 89, "weatherCode": 53, "pm25": 4.7, "aqi": 38, "city": "Chiang Mai" }
```

Every field is independently optional — a temp-only result with no AQI (or
vice versa) is a normal degraded state, not an error. `city` is
reverse-geocoded from the same lat/lon (cached, never caches a failure).

```
python clawdmeter_daemon.py --weather --push-to <device>
```

## z.ai (GLM/Zhipu) quota (optional, off by default)

Pushes your z.ai account's own usage to `http://<device>/api/zai`, for a
`smalltv-mod` build with the z.ai quota page. Needs a z.ai API key
(`--zai-key`/`CLAWDMETER_ZAI_KEY`) — log in at
[z.ai](https://z.ai), open your account/profile menu → **API Keys**
(dashboard path, not a documented API — z.ai may move this), and copy an
existing key or create one. No login flow to run, no headless-machine
caveat — it's a plain static key, so generate it on any machine with a
browser and paste it wherever the daemon runs.

```json
{ "ok": true, "pct5h": 1, "r5h": 205, "pctMcp": 0, "rMcp": 10242 }
```

`pct5h`/`r5h` are the real rolling 5-hour cycle; `pctMcp`/`rMcp` are the
monthly MCP-tools quota (search-prime/web-reader/zread). **This hits an
endpoint z.ai has not publicly documented** — found via a community tool,
confirmed working against a real account, no stability guarantee.

```
python clawdmeter_daemon.py --zai --zai-key <your-key> --push-to <device>
```

## OpenRouter spend (optional, off by default)

Pushes your OpenRouter account dollar spend to
`http://<device>/api/openrouter`, for a `smalltv-mod` build with the
OpenRouter spend page. This is a lightweight authenticated key read, no
model call and no per-call cost. The key is read from `--openrouter-key`,
`CLAWDMETER_OPENROUTER_KEY`, or `~/.openrouter_dot_ai_key` in that order.

```json
{ "ok": true, "usd_daily": 0.12, "usd_weekly": 0.83, "usd_total": 42.5, "free_tier": false }
```

```
python clawdmeter_daemon.py --openrouter --push-to <device>
```

## Codex CLI quota (optional, off by default)

Pushes your ChatGPT-plan Codex CLI rate-limit usage to
`http://<device>/api/codex`. **Free** — each poll briefly spins up `codex
app-server` and reads the rate-limit state over JSON-RPC — one live, free quota
read, never stale, with no model call and no extra cost. Needs `codex login` already done on this
machine (not a separate API key) — specifically the ChatGPT-plan login, not
an API key login (`--with-api-key` logs into separate, billed API usage
that has no rate-limit headers to read here, and won't give this feature
anything to show).

**Headless / no browser on this machine?** Run `codex login --device-auth`
instead of plain `codex login` — the CLI itself prints this as the
recommended path for "a remote or headless machine": it prints a URL +
code to open on any other device with a browser, and once you approve
there, this machine's session completes on its own. (Plain `codex login`
opens a local callback server and expects *this* machine to have the
browser — don't use it over SSH.)

**Don't rely on copying `~/.codex/auth.json` between machines.** It's
tempting (it's just JSON) but confirmed unreliable in practice: copied a
freshly-working `auth.json` from one machine to a second and got `401
Provided authentication token is expired` there immediately, even though
the source machine kept working fine with the same file seconds later.
Same failure mode hit the Calendar OAuth token when copied the same way
(see below). Just run `codex login --device-auth` on each machine
individually — it takes under a minute and avoids this entirely.

**"no rate limits returned" even though `codex login status` says you're
logged in?** Every case found so far reduces to the same fix — re-run
`codex login --device-auth` for a fresh token — regardless of which of
these shows up first:

- A stderr line like `Codex's Linux sandbox uses bubblewrap and needs
  access to create user namespaces` is **cosmetic noise** — confirmed
  twice now, on two different machines/`codex-cli` versions (0.146.0 and
  0.147.0) — it does *not* block `account/rateLimits/read`, which never
  actually needs the sandbox. **Do not chase kernel/AppArmor settings**
  (e.g. `kernel.apparmor_restrict_unprivileged_userns`, or writing an
  AppArmor profile for `bwrap`) — tried both live, neither changed the
  outcome. It's a red herring for this specific RPC call, every time.
- An old `codex-cli` build can silently swallow the real RPC error —
  upgrading (`npm install -g @openai/codex@latest`) can surface a
  `refresh_token_reused` 401 underneath, meaning the locally-stored OAuth
  refresh token was already consumed server-side (can happen if a
  previous `codex` process completed a token exchange but got killed
  before writing the new token back — e.g. during manual debugging with a
  timeout/kill).
- Or the RPC just returns a plain `401 Provided authentication token is
  expired` directly (no upgrade needed to see it) — the access token
  simply went stale with no automatic refresh, which happens easily on a
  daemon box that never runs `codex` interactively (that's what refreshes
  it normally) — confirmed on a token that hadn't refreshed in 6 days.

In short: whatever the exact error text, don't diagnose further — just
`codex login --device-auth` again.

```json
{ "ok": true, "pct5h": 12, "r5h": 180, "pctWeek": 47, "rWeek": 9186, "resetCredits": 1, "resetCreditExpireMins": 3911 }
```

`pct5h`/`r5h` are the shorter rate-limit window (often absent — this
account's plan tier has none); `pctWeek`/`rWeek` are the longer window.
`resetCredits`/`resetCreditExpireMins` are free "full rate-limit reset"
credits Codex occasionally grants (an undocumented field, found by reading
the raw RPC response) — the countdown is to the soonest one expiring
unused, so you know to use it before it's gone.

```
python clawdmeter_daemon.py --codex --push-to <device>
```

## Antigravity CLI quota (optional, off by default)

Pushes your Google Antigravity (`agy`) account quota to
`http://<device>/api/antigravity`. **Not free like Codex** — `agy`'s local
quota data only populates after a real cascade/agent prompt has run, so
every poll fires a real, cheap (`gemini-3.6-flash-low`) prompt. Small but
real cost — kept to a long default interval (30 min) for that reason.
Needs `agy` already installed and authenticated on this machine, plus **`lsof`** on
`PATH` — the daemon uses it to find `agy`'s local port (`sudo apt install lsof` on
Debian/Ubuntu). macOS and Linux only; `lsof` does not exist on native Windows, so the
page stays empty there.

**Install** (native Go binary, no Node/npm needed, auto-updates):

```bash
curl -fsSL https://antigravity.google/cli/install.sh | bash
```

Installs to `~/.local/bin/agy`. If this machine runs the daemon as a
`systemd --user` service (see the headless section above), that service's
PATH is minimal and won't include `~/.local/bin` — symlink it somewhere
already on PATH instead:

```bash
sudo ln -sf ~/.local/bin/agy /usr/local/bin/agy
```

**Headless / no browser on this machine?** `agy` has no separate
`login`/`auth` subcommand and its login flow needs a real interactive
terminal (it errors with `bubbletea: could not open TTY` over a plain
non-interactive SSH command) — but it works over SSH given a real pty,
confirmed live:

```bash
ssh <headless-host>
tmux new-session -s agyauth 'agy'
```

Pick **Google OAuth**, then open the printed URL in any browser, sign in,
and paste the code it shows you back into that same `agy` prompt (**do
this by typing directly into the terminal, never by pasting the code
into a chat client or anywhere else it'd be logged** — it's a real,
time-limited OAuth code). The token lands at
`~/.gemini/antigravity-cli/antigravity-oauth-token`. First run also asks two
one-time questions: whether to share CLI usage data with Google (opt-out
is a real, separate checkbox — toggle with `enter`, not `space`, on the
entry it's pre-checked on) and whether to trust the current
directory (grants `agy` read/edit/execute there — needed for the
daemon's own unattended polls to work, not just this interactive
session). Answer both before it drops you into a normal prompt; then
exit (`Ctrl+C` twice) — no need to leave the session running, the daemon
starts its own `agy` process per poll.

**Sign in on each machine.** Copying the session to another box is unverified,
and the path this README previously gave for it does not exist on a signed-in
machine — the CLI keeps its state under `~/.gemini/antigravity-cli/`, which is
separate from the IDE's directory. Note also that signing in to the Antigravity
IDE does **not** sign in the CLI, and that sign-in needs a real TTY (over SSH,
run it inside `tmux new-session 'agy'`).

```json
{ "ok": true, "pctPro": 4, "labelPro": "3.1 Pro", "rPro": 9180, "pctFlash": 2, "labelFlash": "3.6 Flash", "rFlash": 284 }
```

Two real numbers, not one: this account's 11 real model configs split
into a Gemini Pro family (`gemini-3.1-pro-*`) and a Gemini Flash family
(`gemini-3.5/3.6-flash-*`) — non-Gemini models this account also has
access to (Claude Sonnet/Opus, GPT-OSS run through `agy`) aren't shown
here (they have their own dedicated pages on the device already).
Selection is **version-first, not quota-first**: each family reports its
*newest* version present (this account's Flash family spans two
generations, 3.5 and 3.6 — the daemon always shows 3.6 even if 3.5
happens to be numerically tighter at some poll), breaking ties among
that version's own reasoning-tier variants (High/Medium/Low) by lowest
remaining fraction. `labelPro`/`labelFlash` are the full
`"<version> <family>"` string (e.g. `"3.6 Flash"`, `"3.1 Pro"`) so the
device can show which variant is currently backing the number, since it
can change poll to poll.

**Device rendering note**: fitting a full 9-10 character label next to a
big percentage forced a device-side tradeoff — the two Antigravity cards
render their percentage one font size smaller than every other quota
page (size4 vs. size5). Like every quota page (Claude, z.ai, Codex,
Antigravity), the displayed number is capped at 99%, and turns red exactly
when the true percentage is 100 (white otherwise), so a red 99% always
means fully used. The bar still reacts to the real value. This is purely a
device-side rendering choice — the pushed payload here always carries the
true, uncapped percentage.

```
python clawdmeter_daemon.py --antigravity --push-to <device>
```

## Options

```
--serial [PORT]     USB serial; optional COM port, else auto-detect (VID 0x303A)
--no-hid            tell the serial device to disable its HID keys
--push              HTTP-push with mDNS auto-discovery of every SmallTV on the LAN
                    (mDNS is link-local: won't cross subnets/VLANs, see Troubleshooting)
--push-to DEVICE    HTTP-push to a device (IP or hostname). Repeatable
                    (--push-to A --push-to B) and/or comma-separated
                    (--push-to "A,B"); env CLAWDMETER_PUSH_URL accepts the same list
--no-discover       disable mDNS discovery for push (only push to --push-to hosts).
                    Sticky: saved to ~/.clawdmeter-daemon.json, and there is no
                    --discover to undo it -- remove "discover": false from that file
--push-interval N   seconds between pushes (default 20)
--serve             run the HTTP server (the default only when no transport is
                    chosen AND no push target is saved -- see Quick start)
--host / --port     bind address for --serve (default 0.0.0.0:8787)
--interval N        seconds between Claude API refreshes (default 60)
--no-tray           run headless in the console
--install           register autostart at login (per-user) and exit
--uninstall         remove the autostart entry and exit
--autostart-status  print whether autostart is registered and exit
--calendar-auth     one-time interactive Google Calendar authorization, then exit
--calendar-sync-color CALENDAR_ID  one-time: read this calendar's real color
                    via your own OAuth login and apply it to the service
                    account, then exit -- see Google Calendar section
--calendar          enable the Google Calendar feature. Service account (recommended):
                    a key file plus a calendar shared with its client_email -- no
                    --calendar-auth needed. OAuth: run --calendar-auth once first
--no-calendar       disable the Google Calendar feature (overrides a remembered --calendar)
--calendar-id ID[,ID...]  comma-separated calendar IDs to poll, overriding
                    auto-detection. Default: auto-detect every calendar
                    checked ("selected") in your Google Calendar sidebar --
                    events from all of them are merged, sorted by time, and
                    each tagged with its source calendar's real color so
                    the device can show which calendar an event came from.
                    REQUIRED when using a service account (see Google
                    Calendar section) -- a service account has no "selected"
                    sidebar state, so auto-detect silently finds nothing
--calendar-interval N  seconds between Calendar refreshes (default 300)
--calendar-push-interval N  seconds between Calendar pushes to the device
                    (default: same as --calendar-interval). Separate from
                    the poll interval so you can push more/less often than
                    you re-check Google without hammering either side.
--weather           enable weather + AQI push (Open-Meteo, no API key
                    needed). Reads the device's own lat/lon from its web UI
                    (GET /api/config) -- set that there, not via a flag here
--no-weather        disable the weather feature (overrides a remembered --weather)
                    NOTE: DELETING `--weather` from your command line does NOT
                    disable weather. Every flag is remembered in
                    ~/.clawdmeter-daemon.json and merged back on the next start,
                    so the feature stays on until `--no-weather` explicitly
                    turns it off. Verify the stored value, not the command line:
                      python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.clawdmeter-daemon.json')))['weather_enabled'])"
                    Also: the `Weather: {...}` log line is printed only when a
                    POLL SUCCEEDS. The push loop prints nothing on success, and a
                    failed poll keeps pushing the last good payload. So the
                    absence of `Weather:` lines means polling stopped working --
                    it does NOT mean the device stopped receiving weather.
--weather-interval N  seconds between weather/AQI refreshes (default 600)
--zai               enable z.ai (GLM) quota push
--no-zai            disable the z.ai feature (overrides a remembered --zai)
--zai-key KEY       z.ai API key. Env: CLAWDMETER_ZAI_KEY. Required for
                    --zai to do anything.
--zai-interval N    seconds between z.ai quota refreshes (default 300)
--openrouter        enable OpenRouter dollar-spend push -- lightweight key
                    read, no per-call cost
--no-openrouter     disable the OpenRouter feature (overrides a remembered --openrouter)
--openrouter-key KEY  OpenRouter API key. Env: CLAWDMETER_OPENROUTER_KEY;
                    fallback: ~/.openrouter_dot_ai_key. Required for
                    --openrouter to do anything.
--openrouter-interval N  seconds between OpenRouter spend refreshes (default 300)
--codex             enable Codex CLI quota push -- needs `codex login`
                    already done on this machine. Rides your ChatGPT
                    plan's included usage; no separate API key or cost
--no-codex          disable the Codex feature (overrides a remembered --codex)
--codex-interval N  seconds between Codex quota refreshes (default 300)
--antigravity       enable Antigravity CLI (`agy`) quota push -- needs
                    `agy` authenticated on this machine. UNLIKE --codex,
                    every poll fires a real cheap-model prompt -- a small
                    but real cost, not a free background read
--no-antigravity    disable the Antigravity feature (overrides a remembered --antigravity)
--antigravity-interval N  seconds between Antigravity quota refreshes
                    (default 1800 -- kept long since each poll has a real cost)
```

### Daemon source IP (optional, device-side)

`smalltv-mod` devices have no write-auth key -- this is plaintext HTTP on
your own LAN, and a key never bought real security, only accidental-write
protection. If you want that protection, set a "Daemon source IP" in the
device's own web UI (Update tab) instead: pushes from any other address are
then ignored. Nothing to configure on the daemon side for this -- it's
purely a device-side filter.

**If you set it, know how it fails.** A mismatch rejects every push with
`HTTP 403`, and the daemon log is where you'll see it -- while the device
looks perfectly healthy over HTTP, because only the push endpoints are
filtered. It bites hardest on a **multi-homed daemon host**: the address the
device sees is the source address the kernel picked for that route, not
whichever of your machine's IPs you had in mind. A Raspberry Pi with both
`eth0` and `wlan0` in one `/23` will happily send from the wired address
while you filled in the wireless one. Check what the device will actually
see before setting it:

```bash
ip route get <device-ip>      # Linux: the "src" field is what the device sees
```

Leave it empty (the default) unless you actually want the filter -- it is
accidental-write protection, not security.

### z.ai quota

Pushes z.ai's own account quota (5h cycle % and the **monthly MCP-tools**
quota %, not a token-usage %) to a
`smalltv-mod` build with the "Z.AI quota" mode/carousel page. Needs a z.ai
API key (`--zai-key`/`CLAWDMETER_ZAI_KEY`, from your z.ai account) --
**unlike Claude usage, this hits an endpoint z.ai has not publicly
documented**, found via a community tool and confirmed working against a
real account, but with no stability guarantee. If it stops working after a
z.ai-side change, that's why.

## Troubleshooting

- **No tray icon appears.** First check `~/.clawdmeter-daemon.log`. If it says
  `pystray/Pillow not installed - running headless`, the daemon is running under a
  Python that lacks the tray deps (a launcher resolved a different interpreter than
  you installed into — common with the Microsoft Store Python). Fix: run `install.bat`
  / `./install.sh`, which pin a `.venv`, or set `CLAWDMETER_PYTHONW` to a Python that
  has `pystray` + `Pillow`. If the log instead shows the daemon polling, it's running
  and the icon is just hidden or unsupported. **Windows 11:** the icon starts in the
  `⌃` overflow flyout; drag it onto the taskbar. **Linux (GNOME/Wayland):** there is no
  tray without the *AppIndicator and KStatusNotifier Support* extension, and the icon
  needs the AppIndicator/GTK packages (see Install); without a backend the daemon logs
  a note and runs headless. **macOS:** it's a menu-bar icon (no Dock icon by design).
- **Tray says "Token expired - run: claude setup-token".** Your on-disk credentials
  expired and can't be renewed headlessly. Use a long-lived token (see
  [Authentication](#authentication-the-durable-way)).
- **Device never shows data (push/serve).** The device must be able to reach the PC
  (serve) or the PC the device (push). If the device can't open a connection back to
  the PC (PC firewall, VPN on the PC), use **push** mode. Full **client/AP isolation**
  is different: it blocks traffic in *both* directions, so no transport helps and the
  fix is to put both ends on your own hotspot instead — see
  [Sharing your laptop's WiFi](https://kittipitch.github.io/smalltv-mod/getting-started/sharing-wifi/). Also open the PC's
  firewall for `--serve` (inbound TCP 8787). **Windows**, in an administrator PowerShell:
  `New-NetFirewallRule -DisplayName clawdmeter -Direction Inbound -Protocol TCP
  -LocalPort 8787 -Action Allow`. **Ubuntu with UFW**: `sudo ufw allow 8787/tcp`.
  **macOS**: allow incoming connections for the venv's Python when prompted, or add it
  under System Settings → Network → Firewall → Options.
- **Reading a push failure.** Each failing target logs every 20 s, and the tail says
  which layer broke: `Push http://… failed: [Errno 8] nodename nor servname provided`
  (the name does not resolve. **On Linux this usually means `avahi-daemon` is not
  running** — `sudo apt install avahi-daemon`, then check with
  `avahi-resolve -n smalltv-XXXX.local`; macOS and Windows 10+ resolve `.local` natively.
  Otherwise it is a typo, or use the device's raw IP), `… failed: [Errno 61] Connection
  refused` (wrong host, or the device is not serving HTTP), `… failed: timed out` (device
  asleep or off-network), `Push … HTTP 403` (the device's **Update → Daemon source IP**
  names a different machine). No `Push` line at all means no target: it started in serve
  mode.
- **Device IP keeps changing.** Push to its mDNS name (`smalltv-XXXX.local`, the name on
  its WiFi tab) or set a DHCP reservation.
- **Log shows `Polling Claude every 60s` and then nothing — no `5h=` line.** No usable
  Claude token: the tray icon is red and its menu says which case it is (no credentials,
  not logged in, or token expired). Headless? Set `CLAUDE_CODE_OAUTH_TOKEN` in `.env`.
- **`ModuleNotFoundError: No module named 'httpx'` (or `aqi`).** You started the daemon
  with the wrong interpreter — use `.venv/bin/python` (Windows `.venv\Scripts\python`).
- **macOS: `TypeError: unsupported operand type(s) for |`.** The `.venv` was built with
  the 3.9 system Python. `rm -rf .venv`, then `PYTHON=$(brew --prefix)/bin/python3 ./install.sh`.
- **Every push logs `HTTP 403`.** The device is rejecting the daemon by source IP:
  you (or a past you) set a **Daemon source IP** in its web UI and the daemon is
  reaching it from a different address. Nothing on the daemon side is broken -- the
  device answers `/api/status` and the web UI normally, because only the push
  endpoints are filtered. Confirm with `ip route get <device-ip>` (the `src` field is
  the address the device actually sees), then either correct the field or clear it:
  `curl -X POST -H 'Content-Type: application/json' -d '{"daemonIp":""}'
  http://<device>/api/config`. See [Daemon source IP](#daemon-source-ip-optional-device-side).
- **A push succeeded once and then the log went quiet.** That is success, not a
  stall. `Pushing to <url> OK` is logged only the *first* time a given URL
  succeeds; after that only failures are logged. A URL that logs `OK` a second
  time dropped out and recovered in between.
- **Several SmallTVs on one network.** With any `kittipitch/smalltv-mod` release
  (its `v1.0.0-kitt*` versions are numerically *lower* than upstream's 2.8.x, but
  newer) just run `--push`:
  each device advertises itself over mDNS (`_clawdmeter._tcp`) and the daemon
  discovers them all and pushes the same usage to every one, no per-device address.
  Devices that join or drop off are picked up on the next push. Needs `zeroconf`
  (in `requirements.txt`).
- **Auto-discovery finds nothing (but the devices are reachable).** mDNS is
  **link-local** — it does not cross routers/VLANs. If the daemon PC and the SmallTVs
  are on **different subnets** (e.g. PC on `192.168.2.x`, devices on `192.168.10.x`),
  discovery sees nothing and `.local` names won't resolve, even though direct IP
  still routes. Fixes: run the daemon on a machine **on the same subnet** as the
  devices (then `--push` just works), or enable an **mDNS reflector/repeater** on
  your router between the VLANs, or skip discovery and **list the device IPs
  explicitly** — via the tray's *Configure push targets…*, or
  `--push-to 192.168.10.44 --push-to 192.168.10.45` (or `--push-to "192.168.10.44,192.168.10.45"`).
  For a fixed IP list, add **DHCP reservations** so the addresses don't drift.
- **Only some devices update.** You listed one host but have several — add the rest
  (tray *Configure push targets…* or repeated/comma-separated `--push-to`), or use
  `--push` if they're all on the daemon's subnet. On older firmware, push to each
  device's unique hostname (`smalltv-3fa2.local`) by hand.
- **Serial device not found.** Check the cable/driver; pass the port explicitly
  (`--serial COM5`). Find it in Device Manager.

## Credits

- Original **Clawdmeter** (ESP32‑S3 desk dashboard):
  [HermannBjorgvin/Clawdmeter](https://github.com/HermannBjorgvin/Clawdmeter).
- USB/Windows fork: [clawdmeter-win](https://github.com/giovi321/clawdmeter-win).
- SmallTV firmware, original: [giovi321/smalltv-mod](https://github.com/giovi321/smalltv-mod).
  This daemon is built to pair with [kittipitch/smalltv-mod](https://github.com/kittipitch/smalltv-mod),
  a fork adding the Agenda/Weather/z.ai/Codex/Antigravity quota pages this
  README documents (see that repo for the firmware side).

## License

[WTFPL](LICENSE) — Do What The F*ck You Want To Public License.
