# clawdmeter-daemon

> New to this? See **[GUIDE-WINDOWS-MAC.md](GUIDE-WINDOWS-MAC.md)** for a
> step-by-step Windows/macOS setup walkthrough. This README is the full
> reference.

One small daemon that shows your **Claude Code usage** on a desk device. It polls
the Claude API rate-limit headers (using the OAuth token Claude Code already stores
on your machine) and delivers your **5-hour** and **7-day** usage to the device by
whichever transport fits your setup:

| Transport | How | For |
|-----------|-----|-----|
| **serial** | writes JSON lines over USB CDC | the original **Clawdmeter** (ESP32‑S3, USB‑attached) |
| **push** | HTTP `POST` to the device | a **SmallTV** behind Wi‑Fi **client isolation** (device can't reach the PC) |
| **serve** | HTTP server the device polls | a **SmallTV** in pull mode, n8n, anything |

Pick one or several — they share the same poller, token handling and tray icon.

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
pip install -r requirements.txt
```

Recommended: use the wrapper that installs deps and registers login autostart in one
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

`httpx` is required; `pyserial` is only needed for `--serial`, `pystray` +
`Pillow` only for the tray icon, and `zeroconf` only for mDNS auto-discovery on
`--push` (without it, push still works via explicit `--push-to` hosts). On
**macOS** the tray also needs `pyobjc-framework-Cocoa` (auto-installed by the
requirements marker). On **Linux** the tray needs the AppIndicator + GTK system
packages and `python3-tk` for the push-targets dialog — `install.sh` prints the
exact command for your distro; without them the daemon runs headless.

## Quick start

```sh
python clawdmeter_daemon.py --serial                 # USB Clawdmeter (auto-detect COM)
python clawdmeter_daemon.py --serial COM5            # ...or a specific port
python clawdmeter_daemon.py --push                   # push to every SmallTV it finds (mDNS)
python clawdmeter_daemon.py --push-to 192.168.1.50   # push to a specific SmallTV (or smalltv.local)
python clawdmeter_daemon.py --serve --port 8787      # serve for the device to pull
python clawdmeter_daemon.py --serial --serve         # several at once
python clawdmeter_daemon.py --no-tray --serve        # headless console
```

With no transport flag it defaults to `--push` if `CLAWDMETER_PUSH_URL`
(or a remembered push target) is set, otherwise `--serve` on `:8787`.

## Authentication (the durable way)

The daemon needs a Claude token. In order it tries:

> **Setting up a new machine?** See **[AUTHENTICATION.md](AUTHENTICATION.md)** for the
> per-harness token guide — Claude, Codex, Antigravity, z.ai, OpenRouter and Google
> Calendar — including which credentials can be copied between machines (Claude,
> z.ai, OpenRouter, Google service account) and which must be re-logged per machine
> (Codex, Calendar OAuth, Antigravity), plus the PATH/environment traps that make a
> service manager silently report no data.

1. **`CLAUDE_CODE_OAUTH_TOKEN`** env var — a **long-lived token** from
   `claude setup-token`. This is the robust choice for an always-on daemon: it
   doesn't expire, so there's nothing to refresh.
2. macOS Keychain / `~/.claude/.credentials.json`, refreshing via the OAuth
   refresh grant or by briefly spawning `claude` (same autonomous mechanisms the
   original daemon used).

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
printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\n' 'sk-ant-oat...' > .env

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
| Linux, headless (no GUI session, e.g. Raspberry Pi) | `systemd --user` service — see [below](#linux-headless--no-gui-session-systemd---user-service) | `~/.config/systemd/user/clawdmeter.service` |

```sh
python clawdmeter_daemon.py --install            # register autostart at login
python clawdmeter_daemon.py --uninstall          # remove it
python clawdmeter_daemon.py --autostart-status   # show what's registered
```

The autostart command is just `--tray`; the transport comes from the remembered
config and the env vars above, so autostart needs no edits — set them once (e.g.
`CLAWDMETER_PUSH_URL=smalltv.local` for push, otherwise it serves on `:8787`).

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

Polls the next upcoming event on a Google Calendar and pushes it to
`http://<device>/api/calendar`, alongside the usage payload above. Separate
feature, separate on/off flag — doesn't change anything about the usage side.

```json
{ "ok": true, "summary": "Team sync", "start": "2026-07-27T10:00:00+01:00", "end": "2026-07-27T11:00:00+01:00", "allDay": false }
```

`summary`/`start` are `null` when there's no upcoming event. `end` mirrors
`start` (timed `dateTime` or all-day `date`) so multi-day events can be told
apart from single-day ones on the device; for all-day events it's Google's
exclusive end date, shipped raw. Field names are
spelled out rather than terse like the usage contract's `s`/`w` — there's no
firmware consumer for this yet (see the smalltv-mod project's own notes), so
expect this shape to firm up once that side is built.

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
   daemon logs a warning on startup if it detects this combination.

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
`--calendar-auth` on the target machine by forwarding its loopback port over SSH
(`ssh -L <port>:127.0.0.1:<port> host`) and opening the printed URL locally.

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
| Windows | Works under WSL (use Linux instructions above) or skip it (titles are stripped device-side instead) |

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
app-server` and reads its already-cached rate-limit state over JSON-RPC, no
model call and no extra cost. Needs `codex login` already done on this
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
Needs `agy` already installed and authenticated on this machine.

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
page (size4 vs. size5), and the displayed number is capped at 99% (the
bar/color logic still reacts to the real value, so a genuinely exhausted
quota still shows a full red bar even though the printed number reads
"99%" rather than "100%"). This is purely a device-side rendering choice
— the pushed payload here always carries the true, uncapped percentage.
Since a shown "99%" is otherwise ambiguous (a real 99, or a real 100
silently capped), the device also colors that value text red exactly
when the true percentage is 100 (white for every other value) — a
correctness flag, unrelated to the bar's own severity-based coloring.

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
--no-discover       disable mDNS discovery for push (only push to --push-to hosts)
--push-interval N   seconds between pushes (default 20)
--serve             run the HTTP server (default when no transport is chosen)
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
--calendar          enable the Google Calendar feature (needs --calendar-auth run once first)
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

Pushes z.ai's own account quota (5h cycle % and a token-usage %) to a
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
  (serve) or the PC the device (push). On Wi‑Fi with **client/AP isolation** the
  device can't open a connection back — use **push** mode. Also open the PC's
  firewall for `--serve` (`New-NetFirewallRule -DisplayName clawdmeter -Direction
  Inbound -Protocol TCP -LocalPort 8787 -Action Allow`).
- **Device IP keeps changing.** Push to its mDNS name (e.g. `smalltv.local`) or set
  a DHCP reservation.
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
- **Several SmallTVs on one network.** With firmware **2.8.0+** just run `--push`:
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
