# Getting a token for each harness

The daemon reads quota from several tools, and **each authenticates differently**.
This is the per-harness reference: where the credential comes from, where it is
stored, whether it survives being copied to another machine, and how to tell it
worked.

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

The Codex case is mechanically sound as well as observed: two machines sharing one
refresh token produce `refresh_token_reused`.

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
refresh path of its own, and the Linux fallback may spawn `claude` to refresh, which
has been seen to hang with stale credentials.

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
   that looks exactly like an auth failure and is not. `agy` spawns a cascade
   session in whatever directory it is launched from, and refuses in an untrusted
   one — returning an empty result with no distinct error, so the daemon just logs
   `{"ok": False}`.

   The directory to trust is the **service's working directory**, not your shell's:
   `WorkingDirectory` in a launchd plist, or `$HOME` by default under
   `systemd --user`. Check and fix:

   ```bash
   cat ~/.gemini/antigravity-cli/settings.json     # look at trustedWorkspaces
   ```

   ```json
   {
     "trustedWorkspaces": [
       "/Users/you",
       "/Users/you/git_projects/clawdmeter-daemon"
     ]
   }
   ```

   Trusting only `$HOME` is **not** enough if the service runs from a repo
   directory. Restart the service after editing.
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

⚠ **That message lies when run headlessly.** With no Aqua session — over SSH, or
under launchd — `agy`'s own Keychain read times out after 10 s and it reports "not
logged into Antigravity" **even though the account is authenticated**. So a bare
`ssh host 'agy models'` is not a valid auth check and will send you chasing a
sign-in you already did.

What actually happens: a background goroutine in that same process still completes
an OAuth refresh over the network, and because its Keychain *write* also times out,
it falls back to a plaintext token file at
`~/.gemini/antigravity-cli/antigravity-oauth-token`. A second invocation, started
after the first has fully exited, picks that healed file up. The daemon retries once
on exactly this error for that reason.

To check auth for real, run `agy models` **in a GUI terminal on the machine
itself**, or look for that token file.

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

The prompt model is hardcoded to `gemini-3.6-flash-low`. It must appear in
`agy models` for your plan, or every poll fails.

## z.ai

Static API key. Pass `--zai-key`, or set `CLAWDMETER_ZAI_KEY` in the environment /
`.env` / `EnvironmentFile=`. Copyable between machines.

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
it, the key is silently ignored.

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

### OAuth (`--calendar-auth`)

Works, but the refresh token expires after **7 days** while the GCP project is in
Testing. Publishing it to Production removes that — which was impossible for one
project here because an unrelated client in the same project used a non-HTTPS
redirect URI. Use a fresh project, or the service account.

**Headless?** `--calendar-auth` binds a random loopback port and prints a URL. Over
SSH, forward that port (`ssh -L <port>:127.0.0.1:<port> host`) and open the URL in a
local browser. `--calendar-sync-color` needs this same OAuth login, so a
service-account-only box cannot set calendar colours.

---

## PATH and environment: the quiet failure

Service managers start with a **minimal environment** and do **not** read
`~/.zshrc`, `~/.zprofile` or `~/.bashrc`. Two consequences:

- **Secrets exported in a shell profile are invisible to the daemon.** Use `.env` or
  `EnvironmentFile=`.
- **Tools in `~/.local/bin`, `~/.npm-global/bin` or `/opt/homebrew/bin` are not on
  `PATH`.** The daemon shells out to `claude`, `codex` and `agy`.

This applies to **launchd as well as systemd** — launchd's default PATH is
`/usr/bin:/bin:/usr/sbin:/sbin`, with no Homebrew.

```ini
# systemd (Linux): ~/.config/systemd/user/clawdmeter.service
[Service]
Environment=PATH=%h/.local/bin:%h/.npm-global/bin:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=%h/.config/clawdmeter/token.env
```

```xml
<!-- launchd (macOS): ~/Library/LaunchAgents/<label>.plist -->
<key>EnvironmentVariables</key>
<dict>
    <key>PATH</key><string>/Users/YOU/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
</dict>
```

Symlinking the tools into `/usr/local/bin` works too, and is what one deployment does
for `agy`.

⚠ Re-running `--install` **rewrites the plist** and drops any PATH block you added by
hand.

Do **not** put the token in the plist's `EnvironmentVariables` — it leaks to anyone
running `launchctl print`. `launchctl setenv` is also wrong: it does not survive a
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

Success looks like real numbers, not absence of errors: `5h=..% 7d=..%` for Claude,
`Codex: {...}` for Codex, and `Pushing to http://<device>/api/usage OK`.
