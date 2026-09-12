# Getting a token for each harness

The daemon reads quota from several tools, and **each authenticates differently**.
This is the per-harness reference: where the credential comes from, where it is
stored, whether it survives being copied to another machine, and how to tell it
worked.

## New machine, start to finish

Do these in order — the order matters, and each step has a check. If a check fails,
fix it before continuing; the failures downstream all look the same
(`{"ok": false}`, a blank page on the device) and become hard to tell apart.

### 0. Prerequisites

**You need a device already running this firmware.** The daemon only pushes; it
cannot set one up. That means a SmallTV-family unit flashed with
[`kittipitch/smalltv-mod`](https://github.com/kittipitch/smalltv-mod) — see that
repo for hardware variants and flashing. Get its hostname from the device's web UI
(`/api/config` → `hostname`, or the **WiFi tab → Device name**). That field is the bare
name, `smalltv-XXXX` — append `.local` yourself when you use it as an address. Confirm
before going further:

```bash
curl -s http://<device>.local/api/status      # expect JSON with "fw":"smalltv-mod"
```

**Tools.** The daemon shells out to these; none are present on a fresh machine, and
each needs its own account:

| Tool | Install | Needs |
|---|---|---|
| Claude Code | `npm install -g @anthropic-ai/claude-code` | Claude subscription |
| Codex CLI | `npm install -g @openai/codex@latest` | ChatGPT plan (not an API key) |
| `agy` (optional) | see README's Antigravity section | Google account; **costs money per poll** |

Only Claude is needed to get started. Skip the others until their step.

**System packages.** On Debian/Ubuntu `git` and the venv module are often absent:

```bash
sudo apt install git python3-venv
```

```bash
python3 --version          # must be 3.10+ — 3.9 dies at import on PEP 604 annotations
```

macOS ships 3.9 as `/usr/bin/python3`. Install a newer one (python.org or Homebrew)
and build the venv from **that explicit path**, e.g. `/usr/local/bin/python3.13`.

```bash
git clone https://github.com/kittipitch/clawdmeter-daemon.git
cd clawdmeter-daemon
python3 -m venv .venv                      # or the explicit 3.10+ path on macOS
.venv/bin/pip install httpx google-auth python-aqi
.venv/bin/python clawdmeter_daemon.py --help    # check: prints usage, no traceback
```

That is the **headless minimum**. `pip install -r requirements.txt` also works but
pulls the tray stack (`pystray`, `Pillow`, `pyobjc` on macOS), which a service never
uses — and on 32-bit ARM `Pillow` may want to compile. Add `zeroconf` only if you
want mDNS discovery instead of naming the device explicitly.

### 1. Claude — do this one first

It is the only feature that works with nothing else configured, so it proves the
whole path (poll → push → device) before other variables are added.

**Laptop you actually use:** run `claude` once and complete `/login`. That is enough
— the daemon reads Claude Code's own credentials (macOS Keychain, or
`~/.claude/.credentials.json` on Linux **and Windows** — run `claude` once and
complete `/login` to create it). This is a proven configuration.

**Headless box (Pi, server):** use an environment token instead.

```bash
claude setup-token         # on ANY machine with a browser; prints sk-ant-oat...
```

Long-lived and **copyable**, so mint it on your laptop and move it.

```bash
umask 077
printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\n' 'sk-ant-oat...' > .env
```

One `KEY=VALUE` per line, **no `export`**, and **no line break inside the token** —
a wrapped paste is skipped silently, giving a daemon with no token and no error.

```bash
.venv/bin/python clawdmeter_daemon.py --push-to <device>.local --no-discover --no-tray
```

`--push-to` alone selects push mode (`--push` is redundant beside it).
`--no-discover` stops mDNS browsing so only your named device is pushed to.
`--no-tray` is optional on a headless Linux box — it falls back to console
automatically — but harmless everywhere.

**Check:** `5h=..% 7d=..%` and `Pushing to http://<device>/api/usage OK`. Look for
numbers, not the absence of errors: a missing token logs **nothing at all**. The
`Pushing to … OK` line prints only on the *first* success per URL, so it will not
repeat every cycle.

Stop here until that works. Everything below is additive.

### 2. Codex (free with a ChatGPT plan)

```bash
codex login                  # or: codex login --device-auth   (headless / SSH)
```

**Check:** add `--codex`; log shows `Codex: {'ok': True, ...}`.

⚠ Never run bare `codex login` "just to look" on a machine with a working session —
it wipes the session before completing the new one.

### 3. Weather (no credential at all)

Set the location **on the device**, in its Agenda & weather tab (lat/lon). The
daemon reads it from `/api/config`; there is no daemon-side fallback.

**Check:** add `--weather`; log shows `Weather: {'ok': True, 'tempC': ...}`.
If you see *neither* a `Weather:` line nor an error, the device's lat/lon are still
0/0 — that case is silent.

### 4. Google Calendar (service account)

Longest step. Click-by-click walkthrough with every URL:
<https://kittipitch.github.io/smalltv-mod/getting-started/google-calendar/>; terse
detail in [Google Calendar](#google-calendar). Summary: create a
project, enable the Calendar API, create a service account with **no roles**,
download a **JSON key**, then **share your calendar with the service account's
`client_email`** using **"See event details"**. That share step is the one everybody
misses.

**Check:** add `--calendar --calendar-id you@gmail.com`; log shows
`Calendar: N upcoming, next = '...'`.

### 5. Antigravity — optional, and it costs money

⚠ Every poll fires a **real billable prompt**. Skip unless you want the page. Note
that feature flags are remembered in `~/.clawdmeter-daemon.json`, so once enabled it
stays on until you pass `--no-antigravity`.

`agy` must trust the directory the **service** will run from — decide that now
(step 6 uses the repo checkout; `systemd --user` defaults to `$HOME`):

```bash
cd ~/clawdmeter-daemon       # the future WorkingDirectory
agy                          # bare, in a GUI terminal; accept "trust this folder"
```

**Check:** `agy models` lists models, then add `--antigravity
--antigravity-interval 3600` and look for `Antigravity: {'ok': True, ...}`.

### 6. Only now, install it as a service

Get everything working in the foreground first. A service adds a minimal
environment, a different working directory, and no shell profile — three new failure
modes at once, all silent.

**Linux:** use the complete unit in
[README → Linux headless](README.md#linux-headless--no-gui-session-systemd---user-service),
and add the `EnvironmentFile=` and `Environment=PATH=` lines shown in
[PATH and environment](#path-and-environment-the-quiet-failure). Then
`loginctl enable-linger $USER`, or it dies at logout.

**macOS:** write `~/Library/LaunchAgents/com.giovi321.clawdmeter.plist` yourself —
do **not** use `--install`, which writes a plist that cannot work beyond Claude
usage (no `WorkingDirectory`, no PATH). This one is proven:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.giovi321.clawdmeter</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/YOU/clawdmeter-daemon/.venv/bin/python</string>
        <string>/Users/YOU/clawdmeter-daemon/clawdmeter_daemon.py</string>
        <string>--no-tray</string>
        <string>--push-to</string><string>DEVICE.local</string>
        <string>--no-discover</string>
        <string>--codex</string>
        <string>--weather</string>
    </array>
    <key>WorkingDirectory</key><string>/Users/YOU/clawdmeter-daemon</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>/Users/YOU/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
    <key>ThrottleInterval</key><integer>30</integer>
    <key>ProcessType</key><string>Background</string>
    <key>StandardOutPath</key><string>/Users/YOU/Library/Logs/clawdmeter.out.log</string>
    <key>StandardErrorPath</key><string>/Users/YOU/Library/Logs/clawdmeter.err.log</string>
</dict>
</plist>
```

```bash
plutil -lint ~/Library/LaunchAgents/com.giovi321.clawdmeter.plist
launchctl bootout gui/$(id -u)/com.giovi321.clawdmeter 2>/dev/null
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.giovi321.clawdmeter.plist
launchctl print gui/$(id -u)/com.giovi321.clawdmeter | grep -E 'state|pid'
```

`bootout` then `bootstrap` immediately can race — wait for it to actually unload, or
you get `Bootstrap failed: 5: Input/output error`.

**Check:** reboot. On **Linux** with linger enabled the service returns on its own.
On **macOS** a LaunchAgent starts at **login**, not at boot — so log back in, then
wait a minute. For a genuinely unattended Mac you need auto-login, or a
LaunchDaemon (not covered here).

## Where the daemon looks for secrets

Two separate mechanisms, easy to confuse:

| Mechanism | Who reads it | Notes |
|---|---|---|
| **`.env` beside `clawdmeter_daemon.py`** (and `.env` in the working directory) | the daemon itself, at import | `KEY=VALUE` per line. Uses `setdefault`, so a **real environment variable always wins**. |
| **`~/.config/clawdmeter/token.env`** | **systemd**, via `EnvironmentFile=` | The daemon never opens this path. It works only because systemd injects it into the process environment first. This is what the Raspberry Pi deployment uses. |

Either is fine. Use `.env` if you start the daemon directly or under launchd; use
`EnvironmentFile=` if you run it under systemd. You do not need both.

⚠ Under `systemd --user` the working directory defaults to `$HOME`, so a stray
`~/.env` gets loaded too. Worth knowing if secrets appear from nowhere.

### `.env` format traps

The parser takes one `KEY=VALUE` per line, strips surrounding quotes, and
**silently skips any line without `=`**. So:

- no `export` prefix
- **no line break inside the value** — Claude Code displays long tokens wrapped;
  pasting the wrap produces a daemon with no token and *no error message*
- then confirm with the log, not by eye

## What can be copied between machines

Static credentials copy fine. Short-lived OAuth ones refresh per-device and reject
a copied refresh token.

| Credential | Copyable? |
|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`) | **Yes** — long-lived |
| `CLAWDMETER_ZAI_KEY` | **Yes** — static API key |
| OpenRouter key | **Yes** — static API key |
| Google **service-account** JSON | **Yes** — no expiry |
| `~/.codex/auth.json` | **No** — `401 token expired` on the copy |
| Calendar **OAuth** token | **Unreliable** — worked once, failed once with `invalid_grant` |
| `agy` / Antigravity session | **Unverified** — sign in per machine |

What was actually observed for Codex is `401 token expired` on the copy.
(`refresh_token_reused` has been seen too, but after a client was killed mid-exchange
— not proven to be the cross-machine mechanism.)

---

## Claude usage

Lookup order in `read_token()`:

1. **`CLAUDE_CODE_OAUTH_TOKEN`** environment variable (including via `.env` /
   `EnvironmentFile=`)
2. Otherwise, whatever Claude Code itself stored **on this machine** — and this
   differs by platform:
   - **macOS**: the Keychain item `Claude Code-credentials`, only
   - **Linux / Windows**: `~/.claude/.credentials.json`, only

**An environment token bypasses Claude Code's own login completely.** A headless box
that has never run `claude` works fine with one — that is exactly how the Raspberry
Pi deployment runs. Without a token, the daemon needs Claude Code logged in *on that
machine*.

```bash
claude setup-token          # subscription required; prints sk-ant-oat...
```

```bash
umask 077
printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\n' 'sk-ant-oat...' > /path/to/clawdmeter-daemon/.env
chmod 600 /path/to/clawdmeter-daemon/.env
```

**Use the env token on any unattended machine.** The macOS Keychain branch has no
refresh path of its own, and the Linux fallback spawns `claude` to refresh (with a
30 s timeout), which is more moving parts than an unattended box needs.

That said, the Keychain path is a *proven* configuration on a laptop: one working
macOS deployment runs under launchd with **no** `CLAUDE_CODE_OAUTH_TOKEN` at all,
purely on the `Claude Code-credentials` Keychain item, kept fresh by the user's own
interactive `claude` use. Laptop: Keychain is fine. Headless: use the env token.

⚠ **A missing token is silent in the log.** There is no "no token" line — the only
symptoms are `{"ok":false}` pushes and the tray/console status. Check for a real
`5h=..% 7d=..%` line instead.

## Codex

Uses your existing Codex CLI login — nothing to paste. The daemon briefly spawns
`codex app-server` and reads quota over RPC, which is **free**.

```bash
codex login                 # browser flow
codex login --device-auth   # headless / over SSH — real device-code flow
```

Three things that bite:

- **It goes stale on a box that never runs `codex` interactively.** A Pi went six
  days and then returned `401 token expired`. Fix is `codex login --device-auth`
  again.
- **Never run bare `codex login` "just to check" on a machine with a live session** —
  it wipes the existing session before completing the new one.
- Must be a **ChatGPT-plan login**, not `--with-api-key`; the API-key path reports no
  rate limits.

Stored in `~/.codex/auth.json`. Do not copy it to another machine.

## Antigravity (`agy`)

⚠ **Every poll costs real money.** Unlike Codex's free RPC read, `agy` only reports
quota once a cascade session has run, so the daemon fires a small real prompt each
poll. Default interval is 1800 s; consider `--antigravity-interval 3600`, or leave
the feature off.

Traps, in the order people hit them:

1. **The `agy` CLI is a separate install from the Antigravity IDE.** Having the app
   does not give you `agy`, and the app bundles no CLI.
2. **Signing in to the IDE does not sign in the CLI.** Separate state.
3. **Sign-in needs a real TTY.** A plain `ssh host 'agy'` dies with
   `bubbletea: could not open TTY`. Over SSH, use `tmux new-session 'agy'`.
4. **The daemon's working directory must be trusted by `agy`.** This is the one
   that looks exactly like an auth failure. `agy` runs its cascade session in
   whatever directory it is launched from and refuses in an untrusted one,
   reporting the same `not logged into Antigravity` error text as a real sign-in
   problem.

   The directory is the **service's** working directory, not your shell's:
   `WorkingDirectory` in a launchd plist, `$HOME` by default under
   `systemd --user`, and **`/`** for a plist written by `--install` (it sets no
   `WorkingDirectory` at all — see the warning at the end of this file).

   **What was actually proven to fix it** — do this in a GUI terminal on the
   machine:

   ```bash
   cd <the service's WorkingDirectory>
   agy                      # bare; accept the "trust this folder" prompt
   ```

   Then restart the service. Editing `trustedWorkspaces` in
   `~/.gemini/antigravity-cli/settings.json` by hand *looks* equivalent and is the
   obvious thing to try, but on its own it did **not** unblock the poll in the one
   traced case — the interactive trust prompt did. Treat the hand-edit as
   unverified.

   ```bash
   cat ~/.gemini/antigravity-cli/settings.json     # inspect trustedWorkspaces
   ```

   Trusting only `$HOME` is not enough when the service runs from a repo checkout.
5. **`lsof` must be on PATH** — the daemon finds the spawned `agy`'s port with it.

```bash
agy            # bare, no arguments — this triggers sign-in
agy models     # free auth check; also confirms the model id below exists
```

State lives in `~/.gemini/antigravity-cli/`. Not signed in looks like:

```
Error: Please sign in to view available models. Launch the CLI without arguments to sign in.
```

and in `~/.gemini/antigravity-cli/log/cli-*.log`:

```
error getting token source: You are not logged into Antigravity.
```

⚠ **Do not trust this message at face value.** It is reported for at least two
different underlying causes, only one of which is an actual sign-in problem:

- **An untrusted working directory** produces this exact `errorMessage`. In the one
  setup where this was traced through the logs, that was the real blocker — the
  account was signed in and the keyring was being read successfully on 33 of 34
  runs.
- **A genuine keyring timeout**, which is rarer than previously written here (once
  in 39 runs). Its symptom is *different*: the daemon logs `agy prompt still
  running after 60s, killing` and `agy stderr: Authentication required. Please
  visit the URL to log in`. When the keyring write also times out, `agy` falls back
  to a plaintext token file at `~/.gemini/antigravity-cli/antigravity-oauth-token`.

- **A race inside `agy`'s own startup**, added here after it was traced end to end
  on Linux in September 2026 and turned out to be the most common cause of all.
  For roughly the first **0.6–1.5 s** after spawn, `agy` answers `GetUserStatus`
  with a stale `not logged into Antigravity` error: its `loadCodeAssistResponse`
  cache refresh fires about **38 ms before its own token auth completes**. Sample
  once inside that window and you get a deterministic "failure" from a process
  that is authenticating perfectly well — the very next second the same port
  returns 14 real `clientModelConfigs`. Fixed in the daemon by polling until
  `clientModelConfigs` is non-empty rather than accepting the first status
  snapshot; if you run an older copy of `clawdmeter_daemon.py`, this alone can
  cost you **half your polls**. Reproduce it by polling `GetUserStatus` every
  250 ms from spawn and watching `configs=0` flip to `configs=14`.

The daemon's own `keyring read timed out headlessly (transient) - retrying once`
line is a **heuristic, and is frequently misattributed** — it fires on the error
text, not on evidence of a timeout. On Linux it is usually wrong twice over: the
keyring rarely times out there, and the real cause is normally the startup race
above or an untrusted working directory.

### How `agy` 1.1.28 actually chooses its token store (Linux)

Confirmed from the CLI's own logs on two headless hosts. Three rules, in order:

1. `Using file-based token storage because SSH session detected` — if an SSH
   session is detected (keyed on `SSH_CONNECTION`), the keyring is **never
   touched**.
2. `Using file-based token storage because a keyring timeout was recorded within
   the last 1h0m0s` — after one timeout it skips the keyring for an hour, then
   re-probes and eats the timeout again.
3. Otherwise it tries the keyring first, then falls back to
   `~/.gemini/antigravity-cli/antigravity-oauth-token`.

**This explains the classic "works when I ssh in, fails as a service" report.**
`ssh` sets `SSH_CONNECTION`, so your hand-run test takes rule 1 and skips the
keyring entirely. A systemd unit sets no `SSH_*` variables, so it takes rule 3
and meets the keyring every time. It is not a permissions or sign-in difference.

What rule 3 costs depends on the box, and both variants were observed:

| Keyring state | What happens | Cost |
|---|---|---|
| No default collection at all (`ReadAlias default` → `/`) | `failed to unlock correct collection` | fails in **under 1 ms**, falls back to the file, harmless |
| `login` collection exists but is **locked** (headless boot, nobody typed the password) | blocks on an unlock prompt no one can answer | **5 s + 10 s** per run, once an hour |

To skip rule 3 entirely on a headless host, give the `agy` subprocess an
`SSH_CONNECTION` value — either in the service unit
(`Environment=SSH_CONNECTION=127.0.0.1 0 127.0.0.1 22`) or in the code that
spawns it. **This daemon now does the latter**: `poll_antigravity()` sets
`SSH_CONNECTION` in the environment it hands to `agy`, so the keyring is never
consulted and the hourly stall does not happen.

**This is a deliberate, accepted workaround** — kept because it works, is
verified live on both hosts, and the supported alternative is unavailable here
(see below). It is not a temporary hack awaiting removal; it is the current
answer, with a known expiry risk.

⚠ **It relies on undocumented behaviour, not a supported switch.** `agy` 1.1.28's
`--help` exposes no auth/keyring/token-storage flag, and no `ANTIGRAVITY_*` or
`GEMINI_*` environment variable for it exists in the binary; the rule was read
out of the CLI's own logs. Google's documentation describes only the intended
setup (an active D-Bus session with a running, unlocked keyring) and offers no
file-only mode. So treat this as a workaround that could stop working on any
`agy` update. **If it regresses, the symptom is the hourly 5 s + 10 s stall
coming back**, visible as `Keyring LoadStoredToken timed out after 5s` in
`~/.gemini/antigravity-cli/log/cli-*.log`; check whether the line
`Using file-based token storage because SSH session detected` still appears
there. The supported alternative is to give the machine a genuinely unlocked
login keyring, which needs a real login password reaching PAM — not possible
under autologin, which is exactly why both hosts here are in this state. Unlocking the collection instead needs a login password typed on the
machine, or a GUI login for PAM auto-unlock, and buys nothing: the file token is
what `agy` ends up using either way.

**Do not try to fix this with file permissions.** The daemon runs as the same
user that owns `~/.gemini/oauth_creds.json` and
`~/.gemini/antigravity-cli/antigravity-oauth-token`, both mode `600`. There is no
access problem to solve, and loosening those files (a group, `644`) only widens
read access to a live Google OAuth token for no functional gain. Keep them
owner-only, and authenticate each host separately rather than copying the files
around — they are per-host credentials, not config to sync.

Settle it from `agy`'s own logs rather than guessing:

```bash
grep -l 'timed out' ~/.gemini/antigravity-cli/log/cli-*.log        # keyring actually timed out?
grep -h 'authenticated via keyring\|effective: file' ~/.gemini/antigravity-cli/log/cli-*.log | tail -3
```

Note the token file is **not** a reliable sign of anything: it exists only when a
keyring save fell back, so it can be absent on a perfectly healthy signed-in Mac.

**Checking auth on a headless Linux box** (where "run it in a GUI terminal" is not
an option): use `tmux new-session 'agy models'` — sign-in needs a TTY anyway. With
no secret service present the token store is the file rather than a keyring, so the
Keychain caveat above should not apply there. Unverified; stated so you know which
part is assumption.

### If Antigravity returns `{"ok": False}` and nothing else

Three different causes produce that same empty result, and fixing one changes
nothing visible until all three are right. Work through them in order:

1. **Actually signed in?** `agy models` **in a GUI terminal** (not over SSH — see
   the warning above). Should list models.
2. **Is the prompt model available on the plan?** The daemon hardcodes
   `gemini-3.6-flash-low`; it must appear in `agy models`.
3. **Is the service's working directory in `trustedWorkspaces`?** See above. This
   was the last blocker in a real setup where the first two were already fine.

Success in the log looks like:

```
Antigravity: {'ok': True, 'pctPro': 3, 'labelPro': '3.1 Pro', 'rPro': 238, ...}
```

Note the retry path currently swallows the second attempt's error text, so a
persistent failure logs only the "retrying once" line and then nothing. If you are
debugging this, expect no further clue from the log.

### Antigravity on an Ubuntu desktop: the daemon read the status too early (2026-09-09 — SOLVED)

Symptom on a desktop Linux with gnome-keyring — every daemon poll fails while
everything looks healthy:

```
Antigravity: keyring read timed out headlessly (transient) - retrying once, ...
Antigravity: no quota data - failed to get load code assist response: error
getting token source: You are not logged into Antigravity. (this was the retry)
```

`agy` run by hand succeeds; the same `agy` under the service fails — but its own
`cli-*.log` for the "failed" run shows `ChainedAuth: authenticated via keyring`
and even `silent auth succeeded`. Root cause is in **clawdmeter, not agy**:
`agy` starts its embedded language server before its silent auth has restored
the OAuth token, so the FIRST `GetUserStatus` RPC snapshot legitimately contains
a transient cached "not logged into Antigravity" error. Interactive print mode
waits for silent auth and succeeds. `_antigravity_wait_for_status()` returned
any response containing `userStatus` — including that pre-auth snapshot — while
its docstring claimed it waited for populated `clientModelConfigs`. Fixed by
holding out for `userStatus.cascadeModelConfigData.clientModelConfigs` and
keeping the last (error-bearing) snapshot only for error reporting.

**How to authenticate properly on an Ubuntu desktop (GUI machine):**

1. Sign in once interactively: open a terminal in the desktop session and run
   `agy models`. Should list models. State lands in
   `~/.gemini/antigravity-cli/` (keyring + plaintext fallback file).
2. `agy -p "reply with just the word pong, no tools"` must print `pong` from a
   plain shell. If not, nothing below matters.
3. The service unit needs `~/.local/bin` on PATH (claude/codex/agy all live
   there; the systemd user manager's default PATH lacks it — see the PATH
   warning earlier in this file). Without it the daemon's log says
   "not logged into Antigravity" too, for a much more boring reason.
4. Copying `~/.gemini/antigravity-cli/antigravity-oauth-token` from a working
   host is a valid bootstrap (a headless host has no keyring, so that file is
   its only store). On a desktop it is neither necessary nor sufficient — the
   keyring works there.
5. Daemon at/after this commit polls until the model configs are actually
   populated; on older daemon versions the transient pre-auth error is taken as
   final and every poll fails. **This is a daemon-code fix, not config** — pull
   it.

Two debugging traps cost hours; both are general:

- **The failure is a race, so it looks flaky.** The same command passes 3/3 in
  one configuration and fails 3/3 in another minutes apart, and pairwise
  env-bisects pass every pair while the triple fails. Single runs mislead;
  test several per cell. (A false DBus correlation came exactly this way:
  blanking `DBUS_SESSION_BUS_ADDRESS` seemed to fix isolated runs, but `strace`
  showed `agy` re-resolving the empty variable back to the real bus — the
  "fix" was the race landing the other way.)
- **Judge the daemon by its log, the CLI by its own log.** The decisive
  evidence was `agy`'s cli-*.log showing full authentication inside a run the
  daemon had already reported as failed. When the daemon says
  "not logged into Antigravity", check whether agy itself finished
  authenticating during that same second before touching any config.

Unchanged from before: `trustedWorkspaces` must include the daemon's working
directory, the prompt model is hardcoded `gemini-3.6-flash-low`, and the
retry-once heuristic logs "keyring read timed out" on error TEXT, not evidence
of a timeout — on a desktop it fires on this race every time.

## z.ai A missing id is **not** a
failure cause — observed behaviour is `Model ID gemini-3.6-flash-low not in local
config, defaulting to CCPA`, after which the poll still succeeds. Do not chase this
as a suspect; the cost of that default is simply unknown.

## z.ai

Static API key from your z.ai account (profile → **API Keys**). Pass `--zai-key`, or
set `CLAWDMETER_ZAI_KEY` in the environment / `.env` / `EnvironmentFile=`. Copyable
between machines — but prefer the env file over the flag, which is visible in `ps`.

## OpenRouter

Resolved in this order:

1. `--openrouter-key`
2. `CLAWDMETER_OPENROUTER_KEY`
3. the file **`~/.openrouter_dot_ai_key`**

The file is what the existing deployments actually use. Copyable between machines.

## Google Calendar

Two paths. **Prefer the service account.**

### Service account (no expiry)

**New to this? Follow the click-by-click walkthrough instead** — every console page,
button and URL, plus the share step and a troubleshooting table:
<https://kittipitch.github.io/smalltv-mod/getting-started/google-calendar/>
(source: `smalltv-mod/docs/src/content/docs/getting-started/google-calendar.md`).
What follows here is the terse version for people who already know the console.

Needs `google-auth` installed in the same environment as the daemon — it is in
`requirements.txt`, but an existing venv may need `pip install google-auth`. Without
it the daemon logs `Calendar: google-auth not installed - `pip install
google-auth`` and simply polls nothing.

1. [Create a project](https://console.cloud.google.com/projectcreate)
2. [Enable the Calendar API](https://console.cloud.google.com/apis/library/calendar-json.googleapis.com)
3. [Service accounts](https://console.cloud.google.com/iam-admin/serviceaccounts) →
   **Create Service Account** (any name, **no roles needed**) → open it → **Keys** →
   **Add Key** → **Create new key** → **JSON**
4. Save as `~/.clawdmeter-google-service-account.json` (`chmod 600`), or point
   `GOOGLE_APPLICATION_CREDENTIALS` at it. That variable can live in `.env` or in
   systemd's `EnvironmentFile=`; `~` is expanded.
5. **Share your calendar with the service account.** Copy `client_email` from the
   JSON, then in [Google Calendar](https://calendar.google.com) → calendar
   **Settings and sharing** → **Share with specific people** → add that email with
   **"See event details"**. (There is no "See all event details" tier — the
   options are "See only free/busy", "See event details", then two edit tiers.) Skipping this is the most common failure: everything
   looks configured and no events ever appear.
6. Give the calendar id — either `--calendar-id you@gmail.com`, **or** fill in
   **Calendar ID(s)** in the device's own Agenda tab, which the daemon reads each
   poll. One of the two is required; auto-detect relies on Google's "selected
   calendars" state, which a service account does not have.

A downloaded `client_secret_*.apps.googleusercontent.com.json` is an **OAuth client
secret**, not a service-account key. Wrong file.

#### Calendar ids live in TWO places, and the device wins

The daemon reads `--calendar-id` (persisted to `~/.clawdmeter-daemon.json`) and the
**Calendar ID(s) field in the device's own Agenda & weather tab**, which it re-reads
every poll. A non-empty device list REPLACES the daemon's list (it is not a union), so
the device's value takes precedence, so a stale id set there **survives every
daemon-side fix** — clear it in both places, then restart.

**Never list `addressbook#contacts@group.v.calendar.google.com` (Birthdays) with a
service account.** It is auto-included only in OAuth auto-detect mode; given
explicitly, the daemon tries to add it to the service account's calendarList and
Google rejects it, logging on **every poll**:

```
Calendar list insert HTTP 400 (addressbook#contacts@group.v.calendar.google.com)
```

There is no failure memo, so it repeats forever. Calendar still works — the noise is
the only symptom.

### OAuth (`--calendar-auth`)

Works, but the refresh token expires after **7 days** while the GCP project is in
Testing. Publishing it to Production removes that — which was impossible for one
project here because an unrelated client in the same project used a non-HTTPS
redirect URI. Use a fresh project, or the service account.

**Headless?** `--calendar-auth` binds a **random** loopback port, so the forward
cannot be set up in advance. Two sessions:

1. `ssh host`, run `--calendar-auth`, and read the port out of the printed URL
2. in a second terminal, `ssh -L <port>:127.0.0.1:<port> host`
3. open that URL in your local browser

`~/.clawdmeter-google-client.json` must already be on the target machine. `--calendar-sync-color` needs this same OAuth login, so a
service-account-only box cannot set calendar colours.

---

## PATH and environment: the quiet failure

Service managers start with a **minimal environment** and do **not** read
`~/.zshrc`, `~/.zprofile` or `~/.bashrc`. Two consequences:

- **Secrets exported in a shell profile are invisible to the daemon.** Use `.env` or
  `EnvironmentFile=`.
- **Tools in `~/.local/bin`, `~/.npm-global/bin` or `/opt/homebrew/bin` are not on
  `PATH`.** The daemon shells out to `claude`, `codex`, `agy`, `lsof` (Antigravity port
  discovery, at **`/usr/sbin/lsof`** on macOS) and `trans` (calendar title
  translation).

This applies to **launchd as well as systemd** — launchd's default PATH is
`/usr/bin:/bin:/usr/sbin:/sbin`, with no Homebrew.

A working headless unit, as deployed on a Raspberry Pi (flags trimmed to the ones
that matter here):

```ini
# ~/.config/systemd/user/clawdmeter.service
[Service]
ExecStart=%h/clawdmeter-daemon/.venv/bin/python %h/clawdmeter-daemon/clawdmeter_daemon.py \
    --push-to <device>.local --no-discover --no-tray --push-interval 30 \
    --weather --calendar --calendar-id you@gmail.com \
    --codex --zai --openrouter --antigravity
EnvironmentFile=%h/.config/clawdmeter/token.env
Environment=PATH=%h/.local/bin:%h/.npm-global/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
```

with `token.env` holding `CLAUDE_CODE_OAUTH_TOKEN`, `CLAWDMETER_ZAI_KEY` and
`GOOGLE_APPLICATION_CREDENTIALS`. Run **`loginctl enable-linger $USER`** or the
service dies at logout. On that box `agy` is symlinked into `/usr/local/bin` and
`codex` comes from `npm -g`, which is an alternative to the `Environment=PATH=`
line.

```ini
# generic form
[Service]
Environment=PATH=%h/.local/bin:%h/.npm-global/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
EnvironmentFile=%h/.config/clawdmeter/token.env
```

```xml
<!-- launchd (macOS): ~/Library/LaunchAgents/<label>.plist -->
<key>EnvironmentVariables</key>
<dict>
    <key>PATH</key><string>/Users/YOU/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
</dict>
```

Symlinking the tools into `/usr/local/bin` works too, and is what one deployment does
for `agy`.

⚠ **The plist `--install` writes cannot work for Antigravity, and re-running it
overwrites a hand-built one.** It sets no `WorkingDirectory` (so the daemon runs
from **`/`**, which `agy` will not trust, and a `cwd`-relative `.env` resolves to
`/.env`), no `EnvironmentVariables` (so no PATH — no `codex`, `agy`, `lsof` or
`trans`), no `KeepAlive`, and passes only `--tray`. For anything beyond Claude
usage, write the plist by hand: `--no-tray`, explicit flags, `WorkingDirectory`,
an `EnvironmentVariables` PATH, and `KeepAlive` with `SuccessfulExit=false`.

Do **not** put the token in the plist's `EnvironmentVariables` — it leaks to anyone
running `launchctl print`. The same applies to `--zai-key` / `--openrouter-key` in
`ProgramArguments` or `ExecStart`, which are visible in `ps`; prefer `.env` or
`EnvironmentFile=`. `launchctl setenv` is also wrong: it does not survive a
reboot.

When a tool is missing, the log says so explicitly:

```
Codex: `codex` not found on PATH - check this daemon's actual runtime PATH
```

## Verifying

Checking your interactive shell proves nothing — it has neither the `.env` contents
nor `EnvironmentFile=`. Ask the service manager, then read the log:

```bash
# systemd
systemctl --user show clawdmeter -p Environment
journalctl --user -u clawdmeter -n 50

# launchd
launchctl print gui/$(id -u)/<label> | grep -A5 environment
tail -50 ~/Library/Logs/clawdmeter.out.log
```

macOS has no `timeout(1)` — use `gtimeout` from coreutils, or leave it off — which
matters only if you hand-test a command.

Success looks like real numbers, not absence of errors: `5h=..% 7d=..%` for Claude,
`Codex: {...}` for Codex, and `Pushing to http://<device>/api/usage OK`.
