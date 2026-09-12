# Changelog

## Unreleased

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
