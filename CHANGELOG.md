# Changelog

## Unreleased

- **Movies page (`--movies`).** Rotten Tomatoes Certified Fresh movies in theaters and at home,
  released this year or last, one an hour per unit, in list order and never random. The daemon
  paints the whole frame (poster, Tomatometer, Popcorn score, release year, where it is showing)
  in the device's own pixel font and pushes it to the new `/api/movie` route; each unit's own
  Movies settings decide what it gets. New module `movies.py`, tests `test_movies_*.py`.

## v1.0.0-kitt36 (2026-10-27)

- **Untranslatable titles read "Not translated".** A non-English calendar title that cannot be translated (no `trans`, or
  translate-shell failing) used to reach the device untranslated and get stripped to leftover digits and punctuation.
  It now shows "Not translated" until a later poll translates it. A refused Bing call (it refuses quick repeats) is
  retried once after 3 s, and an `[ERROR]` line is never taken as a title. Covered by `test_translate_fallback.py`.
- **Translate non-English calendar titles on native Windows too.** `contrib/translate-shell-windows/install-trans.bat`
  installs translate-shell using Git for Windows' `bash` and `gawk`, with a `trans.cmd` wrapper and a `hexdump` shim.
  README lists the macOS, Linux and Windows steps.

- **Tests no longer write into the real daemon log** (`~/.clawdmeter-daemon.log`); each test logs to a temp file.
- **`CLAUDE_CODE_OAUTH_TOKEN_FILE` reads the Claude token from a file**, so the token never sits in a systemd
  `EnvironmentFile` or the process environment (same pattern as `CLAWDMETER_BETTERSTACK_KEY_FILE`). The file wins over
  the inline variable; a missing or empty file falls back to the other sources with a hint and never crashes.
- **uv is the supported way to run the daemon:** `pyproject.toml` + a committed `uv.lock`, run with
  `uv run --project <dir> python clawdmeter_daemon.py ...`. `requirements.txt` stays for plain pip.
- **Docs:** recreating the venv after an OS Python upgrade (a pi5 OS update moved Python 3.12 to 3.14 under the venv);
  making the Windows login autostart push, and undoing it; `--push --interval 300 --push-interval 30` recommended for
  every autostart.

## v1.0.0-kitt35 (2026-10-27)

- **Fix the Antigravity quota on Windows.** The daemon gave every `agy` call an `SSH_CONNECTION` (a Linux keyring
  workaround), which on Windows sent `agy` to an OAuth login prompt that timed out after 60 s, so the page stayed
  blank although `agy` worked in PowerShell. `_agy_env()` now keeps the workaround on Linux/macOS and drops `SSH_*`
  on Windows, where `agy` reads Credential Manager. Covered by `test_agy_env.py`.
- **Extend Better Stack Uptime feed with incident cause and duration (`cause`, `sinceS`).**
  Each target in the `/api/uptime` payload now carries the open incident cause (sanitised,
  ASCII printable, max 40 chars) and elapsed seconds in current state (`sinceS`). An UP target
  derives `sinceS` from the latest resolved incident or resource `created_at`. If the incidents
  endpoint fails, the payload falls back cleanly with `cause` and `sinceS` as `null`.
- **Add `CONTRIBUTING.md`:** the rule behind each platform-specific bug and the test that guards it. `test_uptime.py`
  now also fakes `USERPROFILE`, so it passes on Windows too.

## v1.0.0-kitt34 (2026-10-27)

- **Fix black console windows flashing on Windows.** Seven child-process calls (`agy` quota and probe,
  `agy --version`, `codex app-server`, `trans`, `lsof`, `ping`) bypassed the `CREATE_NO_WINDOW` helper, so the
  pythonw tray daemon popped a console on every poll. All spawns now go through `_run()`/`_popen()`;
  `test_no_console_window.py` fails if a direct `subprocess` call comes back.
- **Add optional Better Stack Uptime feed (`--uptime`).** Polls monitors and heartbeats
  from the Better Stack Uptime API, normalises statuses (up, down, paused, pending),
  sorts down items first, never copies a monitor's or heartbeat's URL (heartbeat URLs embed a secret ping token),
  and optionally pushes the status payload with `ageS` to `/api/uptime` (`--uptime-push`). Covered by `test_uptime.py`.

## v1.0.0-kitt32 (2026-10-25)

Report the Copilot premium allowance and send a next-3-hours weather slice.

- **Copilot sends the `premium_interactions` allowance as optional keys `pctPremium`/`rPremium`.** When the Chat
  and Completions snapshots report unlimited, `premium_interactions` is the snapshot carrying the real usage;
  it is converted like the others, and a disabled or missing snapshot simply stops being sent. Firmware from
  this release shows the Premium allowance as the whole card on top with Chat | Code halves below; older
  firmware ignores the new keys and is unchanged.
- **The weather payload carries an optional `hourly` array: the three hours strictly after the running one.**
  Each entry has a local HH:MM `time`, rounded `temp` and weather `code`, and — only when the source has a
  reading — `precip` (probability), `aqi` (per-hour EPA AQI from the local PM2.5 forecast) and `isDay` (1/0),
  so the device can draw the moon for clear hours after dark. Nothing is derived from a clock. Firmware from
  this release draws the slice as thin-line rows; older firmware ignores the key and is unchanged.
- `test_quota_windows.py` covers the premium conversion and the never-invented rule.

## v1.0.0-kitt31 (2026-10-23)

Read the Copilot quota through the GitHub CLI and push it to the device.

- **New `--copilot` feature polls `gh api /copilot_internal/user`** (an undocumented endpoint, treated as
  unavailable on any change and never logged raw) and converts the Chat and Completions snapshots to used
  percentages, keeping only snapshots that report `has_quota`; both share `quota_reset_date_utc`, the next
  monthly billing-cycle reset. The result is pushed to `/api/copilot` on the same schedule as the other pages.
- **Off by default.** `--copilot` enables it, `--copilot-interval` sets the refresh rate (default 300 s), and
  the daemon host needs `gh auth login` — one authenticated quota read per poll, no model request. `README.md`
  and `AUTHENTICATION.md` document the setup.
- `test_quota_windows.py` covers the snapshot conversion, the `has_quota` filter, a missing reset date and
  malformed responses.

## v1.0.0-kitt30 (2026-10-19)

Show the Gemini 4 Argon limit, which is the one that runs out.

- **Antigravity sends Gemini 4 Argon's weekly window** as optional keys `pctArgon`/`labelArgon`/`rArgon`
  plus the tags `tagFlash` (`Fl`) and `tagArgon` (`Ar`), only when `agy -p "/quota"` reports a group named
  like "argon". On the reference plan Argon was at 0% remaining while the Gemini group was nearly unused,
  so `agy` answered "out of quota" while the page read 0%. Firmware from this release draws the bottom
  card as two halves (`7d ... Fl`, `7d ... Ar`); older firmware ignores the keys and is unchanged.
  `5h`/`7d` keys are untouched.
- `test_quota_windows.py` covers the Argon keys, their absence, and Argon as the only group.

## v1.0.0-kitt29 (2026-10-18)

Keep the quota pages correct after a plan change.

- **Antigravity now reads `agy -p "/quota"`** (agy 1.1.11 and later) instead of scraping its local RPC and
  spending a model prompt per poll. agy 1.4 removed models the old poll was hard-coded to and put the
  RPC behind a CSRF token, so the page went blank. `/quota` is free and needs neither. The device shows
  the model group's shortest window on top and its longest below, labelled by length (`5h` over `7d`),
  like the Claude page. A failed `/quota` never falls back to a paid prompt on a new agy; an older agy
  keeps the original method.
- **Codex sends the long window's real length (`weekMins`).** A free account's only window is 30 days and
  was labelled `7d`. Firmware from this release labels it `30d`; Plus and Pro stay `7d`, and older firmware
  ignores the new key.
- `test_quota_windows.py` covers both.

Make a silent Google Calendar failure visible, and fix stale setup docs.

- **401/403 from the Calendar API are now logged once per failure streak.**
  `poll_calendar()` used to `continue` on 401/403 with no log line at all, and
  `do_calendar_poll()` discarded the retry's `auth_failed`, so the most common
  first-run mistake — the Calendar API not enabled on the project the key came
  from, which Google answers with a 403 carrying "has not been used in project" —
  produced total silence after the startup line. The flag clears on the next
  successful fetch, so a transient 401 that the token refresh fixes logs at most once.
- `--calendar`'s help text no longer claims `--calendar-auth` is required; the
  service-account path (the recommended one) never needs it.
- `AUTHENTICATION.md`: the daemon does not *merge* CLI and device calendar ids — a
  non-empty device list replaces the CLI list. Named the device tab correctly
  ("Agenda & weather").
- `GUIDE-WINDOWS-MAC.md`: device hostnames are `smalltv-<4 hex>`, not `ESP-xxxxxx`,
  and the screen shows the IP at boot; fixed a truncated sentence about where the
  Claude token can live.
- New end-user walkthroughs live in the firmware docs site: a click-by-click
  [Google Calendar service-account guide], a per-feature
  [requirements page] (including `translate-shell` for non-English event titles), and
  a rewritten laptop-hotspot guide with real macOS and Ubuntu steps.

[Google Calendar service-account guide]: https://kittipitch.github.io/smalltv-mod/getting-started/google-calendar/
[requirements page]: https://kittipitch.github.io/smalltv-mod/getting-started/daemon-requirements/

Fix Antigravity polls failing about half the time on headless Linux hosts.

- **The poller sampled `agy` too early.** `_antigravity_wait_for_status()` returned
  the first `GetUserStatus` response that carried a `userStatus` key. For roughly
  the first 0.6-1.5 s after spawn, `agy` answers with a stale
  `not logged into Antigravity` error — its `loadCodeAssistResponse` cache refresh
  fires about 38 ms *before* its own token auth completes — so a single early
  sample froze that transient error as the final answer. The same process was
  serving 14 real `clientModelConfigs` a second later. It now keeps polling until
  `clientModelConfigs` is non-empty or the deadline expires. On one affected host
  this was 25 of 25 first attempts failing, with the once-only retry succeeding
  14 times out of 25; after the fix the first attempt succeeds.
- **The failure was misdiagnosed as a keyring problem for a long time**, including
  by the daemon's own log text (`keyring read timed out headlessly`). On Linux the
  keyring is usually not involved at all: with no default Secret Service collection
  the read fails in under a millisecond and `agy` falls straight through to
  `~/.gemini/antigravity-cli/antigravity-oauth-token`. See `AUTHENTICATION.md` for
  how `agy` 1.1.28 actually picks its token store, why the same command works over
  SSH but fails as a service, and why loosening the credential files' permissions
  is the wrong fix.
- **`agy` no longer touches the OS keyring at all.** The spawned process now
  gets `SSH_CONNECTION` in its environment, which selects agy's file-based
  token store outright. Without it a service always took the keyring path:
  harmless where no default collection exists (fails in under a millisecond),
  but a 5 s + 10 s stall once an hour against a `login` collection that exists
  and is locked, which is the normal state on a headless boot.
- **Port discovery and status polling no longer share one 20 s budget.**
  `_antigravity_wait_for_status()` takes an explicit `port_deadline` (90 s) and
  `status_window_s` (20 s, measured from when the ports actually appear). On a
  host that reboots on a schedule, `agy` was stalling ~58 s before its language
  server started listening while DNS was still down; that ate the whole shared
  window and surfaced as `is \`agy\` authenticated on this machine?` roughly
  two minutes after every reboot.

## v1.0.2

Keep the tray menu compact when pushing to several devices.

- The menu header no longer lists every full push URL inline (which stretched the
  popover very wide with multiple targets). It now shows a one-line summary with the
  first host trimmed to its IP/hostname plus a `(+N more)` count.
- The full target list moved to the icon's hover tooltip, with hosts trimmed and
  capped at six plus a `(+N more)`.
- Also fixes the header running lines together (e.g. `Connected5h 9%`), since it is
  now a single explicitly-spaced line.

## v1.0.1

Fix the Windows tray silently starting headless (no icon).

- `install.bat` now creates a self-contained `.venv`, installs the dependencies into
  it, and registers autostart to use it. `start-daemon.bat` launches that same
  `.venv` interpreter. Previously the launchers relied on the `py`/`pyw` launcher,
  which can resolve a different Python than the one the deps were installed into (the
  Microsoft Store Python installs to a sandboxed location another launch can't
  import), so `pystray`/`Pillow` failed to import and the daemon fell back to headless
  with no tray icon.
- `uninstall.bat` removes the autostart registry value directly, so it no longer
  depends on a working Python, and points at the `.venv` for cleanup.

## v1.0.0

First cross-platform release. The daemon now runs with full feature parity on
Windows, macOS and Linux, including a tray / menu-bar icon and login autostart on
each.

### Added
- `--install` / `--uninstall` / `--autostart-status`: per-user login autostart, no
  admin, using each OS's native mechanism (Windows `HKCU\...\Run` via `winreg`,
  macOS LaunchAgent plist, Linux XDG `.desktop`). The registered command uses the
  interpreter you install with (windowless `pythonw` on Windows), never a hardcoded
  path.
- Cross-platform tray backend detection: on Linux the daemon checks for a display and
  an AppIndicator/Xlib backend and falls back to headless with a clear message instead
  of crashing; on macOS it registers as a menu-bar accessory (no Dock icon).
- File logging to `~/.clawdmeter-daemon.log`, so a windowless or headless launch is no
  longer silent when something goes wrong.
- POSIX helper scripts `install.sh`, `start-daemon.sh`, `uninstall.sh`. `install.sh`
  creates a self-contained `.venv` with `--system-site-packages`, which sidesteps the
  PEP 668 "externally managed environment" block and still lets a Linux tray see the
  system GTK / AppIndicator bindings.

### Changed
- Windows launchers no longer hardcode `C:\Python314\pythonw.exe` or rely on the
  Microsoft Store `pythonw` alias stub. `start-daemon.bat` uses the windowless `pyw`
  launcher; `install.bat` / `uninstall.bat` pick one interpreter for both dependency
  install and autostart so they cannot diverge, and report if autostart registration
  fails instead of claiming success.
- Autostart replaces the previous Startup-folder shortcut + VBS wrapper chain with the
  registry Run key. `uninstall.bat` still clears the legacy shortcuts.
- README documents install, autostart and tray for all three platforms.

### Notes
- Tray backend on Linux needs system packages (`install.sh` prints the command for
  your distro); GNOME on Wayland also needs the AppIndicator extension. Without a
  backend the daemon runs headless.
- macOS tray needs `pyobjc-framework-Cocoa` (installed automatically via a
  requirements marker).
