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

```bash
python3 --version          # must be 3.10+ — 3.9 dies at import on PEP 604 annotations
git --version
```

macOS ships Python 3.9 as `/usr/bin/python3`. Install a newer one (python.org or
Homebrew) and build the venv from *that* explicit path, not from `python3`.

```bash
git clone https://github.com/kittipitch/clawdmeter-daemon.git
cd clawdmeter-daemon
python3.13 -m venv .venv          # or whatever 3.10+ you have
.venv/bin/pip install -r requirements.txt
.venv/bin/python clawdmeter_daemon.py --help    # check: prints usage, no traceback
```

### 1. Claude — do this one first

It is the only feature that works with nothing else configured, so it proves the
whole path (poll → push → device) before other variables are added.

**Headless box (Pi, server): use an environment token.**

```bash
claude setup-token                 # on ANY machine with a browser; prints sk-ant-oat...
```

That token is long-lived and **copyable**, so mint it on your laptop and move it.

```bash
umask 077
printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\n' 'sk-ant-oat...' > .env
chmod 600 .env
```

One `KEY=VALUE` per line, **no `export`**, and **no line break inside the token** —
a wrapped paste is skipped silently and you get a daemon with no token and no error.

**Laptop you actually use:** you can skip the token entirely and let it use Claude
Code's own login (macOS Keychain / `~/.claude/.credentials.json` on Linux). Proven
in practice. But if the machine is unattended, use the token.

```bash
.venv/bin/python clawdmeter_daemon.py --no-tray --push \
    --push-to <device>.local --no-discover
```

**Check:** the log shows `5h=..% 7d=..%` and `Pushing to http://<device>/api/usage OK`.
Numbers, not absence of errors — a missing token logs *nothing at all*.

Stop here until that works. Everything below is additive.

### 2. Codex (free, no key)

```bash
codex login                  # or: codex login --device-auth   (headless / SSH)
```

**Check:** add `--codex`; log shows `Codex: {'ok': True, ...}`.

⚠ Never run bare `codex login` "just to look" on a machine with a working session —
it wipes the session before completing the new one.

### 3. Weather (no credential at all)

Set the location **on the device**, in its Agenda/weather tab (lat/lon). The daemon
reads it from the device, so there is nothing to configure locally.

**Check:** add `--weather`; log shows `Weather: {'ok': True, 'tempC': ...}`.

### 4. Google Calendar (service account)

Longest step. Full detail below in [Google Calendar](#google-calendar) — the summary:
create a project, enable the Calendar API, create a service account with **no
roles**, download a **JSON key**, then **share your calendar with the service
account's `client_email`** using **"See event details"**. That share step is the one
everybody misses.

**Check:** add `--calendar --calendar-id you@gmail.com`; log shows
`Calendar: N upcoming, next = '...'`.

### 5. Antigravity — optional, and it costs money

⚠ Every poll fires a **real billable prompt**. Skip this unless you want the page.

```bash
cd <the directory the service will run from>
agy                          # bare, in a GUI terminal; accept "trust this folder"
```

**Check:** `agy models` lists models, then add `--antigravity
--antigravity-interval 3600` and look for `Antigravity: {'ok': True, ...}`.

### 6. Only now, install it as a service

Get everything working in the foreground first. A service adds a minimal
environment, a different working directory, and no shell profile — three new
failure modes at once, all silent.

See [PATH and environment](#path-and-environment-the-quiet-failure) for the unit and
plist. The three things that break a service which worked fine by hand:

- **PATH** — `claude`, `codex`, `agy`, `lsof`, `trans` are not found. Include
  `/usr/sbin` (macOS `lsof`).
- **Working directory** — `agy` must trust it; a `--install` plist has none and runs
  from `/`.
- **Secrets** — a shell profile is never read. Use `.env` or `EnvironmentFile=`.

On Linux also run `loginctl enable-linger $USER`, or the service dies at logout.

**Check:** restart the machine, wait a minute, and confirm the device still updates.
That is the only test that proves the service survives a reboot.

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

The daemon's own `keyring read timed out headlessly (transient) - retrying once`
line is a **heuristic, and is frequently misattributed** — it fires on the error
text, not on evidence of a timeout. Treat "retrying once… then a failure" as
**probably an untrusted working directory**, not a keyring problem.

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

The prompt model is hardcoded to `gemini-3.6-flash-low`. A missing id is **not** a
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

The daemon merges `--calendar-id` (persisted to `~/.clawdmeter-daemon.json`) with
the **Calendar ID(s) field in the device's own Agenda tab**, which it re-reads every
poll. The device's value takes precedence, so a stale id set there **survives every
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
