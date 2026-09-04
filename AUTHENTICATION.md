# Getting a token for each harness

The daemon reads quota from several different tools, and **each one authenticates
differently**. This page is the per-harness reference: where the credential comes
from, where it is stored, and whether it can be copied to another machine.

Read the copyability table first — it is the thing that wastes the most time.

## The rule that matters: what can be copied between machines

Setting up a second machine, the instinct is to copy credentials across. That
works for **static** credentials and fails for **short-lived OAuth** ones, which
refresh per-device and reject a copied refresh token.

| Credential | Copyable to another machine? |
|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) | **Yes** — long-lived, ~1 year |
| `CLAWDMETER_ZAI_KEY` (z.ai API key) | **Yes** — static API key |
| OpenRouter key | **Yes** — static API key |
| Google **service-account** JSON | **Yes** — no expiry at all |
| `~/.codex/auth.json` (Codex) | **No** — fails with `401 token expired` |
| `~/.clawdmeter-google-token.json` (Calendar **OAuth**) | **No** — fails with `invalid_grant` |
| `agy` / Antigravity session | **No** — sign in per machine |

Both "No" cases were confirmed the hard way: a copied `~/.codex/auth.json` worked
on the source machine and 401'd instantly on the target, and a copied Google OAuth
token failed to refresh with `invalid_grant` while the source machine refreshed the
identical file seconds later. **Fresh login on each machine is the only fix.**

---

## Claude usage

The daemon looks for a token in this order:

1. **`CLAUDE_CODE_OAUTH_TOKEN` env var** — the robust choice for an always-on
   daemon. Also loaded from a **`.env` file next to `clawdmeter_daemon.py`**
   (`KEY=VALUE`, `#` comments allowed), which matters because service managers do
   **not** read your shell profile (see [PATH and environment](#path-and-environment-the-silent-killer)).
2. **macOS Keychain** (`Claude Code-credentials`) or **`~/.claude/.credentials.json`**
   — whatever Claude Code itself wrote when you logged in.

Get a long-lived token:

```bash
claude setup-token          # subscription required; prints sk-ant-oat...
```

Then store it where the daemon will actually see it:

```bash
# next to the script — simplest, works under launchd/systemd
umask 077
printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\n' 'sk-ant-oat...' > /path/to/clawdmeter-daemon/.env
chmod 600 /path/to/clawdmeter-daemon/.env
```

**Prefer this over relying on the Keychain path.** The Keychain fallback works only
while a GUI session is unlocked, and it has **no refresh path** — once that access
token expires the daemon 401s silently until you next use Claude Code interactively.

> Signing in to Claude Code (`/login`) is not the same as having a token the daemon
> can use. If `claude` says `Not logged in · Please run /login`, nothing else will
> work either.

## Codex

Uses your existing Codex CLI login — no key to paste. The daemon briefly spawns
`codex app-server` and reads the quota over RPC, which is **free**.

```bash
codex login                 # normal browser flow
codex login --device-auth   # headless / over SSH — Codex has a real device-code flow
```

Stored in `~/.codex/auth.json`. **Do not copy that file to another machine** — see
the table above.

## Antigravity (`agy`)

⚠ **Every poll costs real money.** Unlike Codex's free RPC read, `agy` only reports
quota once a cascade session has actually run, so the daemon fires a small real
prompt on each poll. That is why the default interval is 1800 s. Consider
`--antigravity-interval 3600` or leaving the feature off.

Two traps:

1. **The `agy` CLI is a separate install from the Antigravity IDE.** Having
   `/Applications/Antigravity.app` does *not* give you `agy`, and the app bundles no
   CLI (unlike VS Code's `code`).
2. **Signing in to the IDE does not sign in the CLI.** They keep separate state.

```bash
agy            # bare, no arguments — this is what triggers sign-in
agy models     # free auth check: lists models if signed in
```

CLI state lives in `~/.gemini/antigravity-cli/`. Not signed in looks like:

```
Error: Please sign in to view available models. Launch the CLI without arguments to sign in.
```

and in `~/.gemini/antigravity-cli/log/cli-*.log`:

```
error getting token source: You are not logged into Antigravity.
```

## z.ai

A static API key from your z.ai account. Pass `--zai-key`, or set
`CLAWDMETER_ZAI_KEY` in the env / `.env` file. Copyable between machines.

## OpenRouter

A static API key. Copyable between machines.

## Google Calendar

Two paths. **Use the service account.**

### Service account (recommended — no expiry)

1. [Create a project](https://console.cloud.google.com/projectcreate)
2. [Enable the Calendar API](https://console.cloud.google.com/apis/library/calendar-json.googleapis.com)
3. [Service accounts](https://console.cloud.google.com/iam-admin/serviceaccounts) →
   **Create Service Account** (any name, **no roles needed**) → open it → **Keys** →
   **Add Key** → **Create new key** → **JSON**
4. Save it as `~/.clawdmeter-google-service-account.json` (`chmod 600`), or point at
   it with `GOOGLE_APPLICATION_CREDENTIALS` in the `.env` file
5. **Share your calendar with the service account.** Copy `client_email` from the
   JSON (`...@....iam.gserviceaccount.com`), then in
   [Google Calendar](https://calendar.google.com) → calendar **Settings and
   sharing** → **Share with specific people** → add that email with **"See all event
   details"**. Skipping this is the single most common failure: everything looks
   configured and no events ever appear.
6. Run with an **explicit `--calendar-id`** (your Gmail address, for a primary
   calendar). It is required with a service account — the auto-detect relies on
   Google's "selected calendars" sidebar state, which a service account does not have.

The JSON key is a long-lived credential with **no expiry**. Treat it like a
password.

### OAuth (`--calendar-auth`) — has a 7-day cliff

Works, but the refresh token expires after 7 days unless the GCP project is
published to Production, and Google will not publish a project whose client uses a
non-HTTPS redirect URI. Fine for a quick test, wrong for an always-on daemon.

Note that a downloaded `client_secret_*.apps.googleusercontent.com.json` is an
**OAuth client secret**, not a service-account key. Different thing, wrong file.

---

## PATH and environment: the silent killer

Service managers start with a **minimal environment**. They do **not** read
`~/.zshrc`, `~/.zprofile`, or `~/.bashrc`. Two consequences, both of which fail
quietly:

- **Secrets exported in a shell profile are invisible to the daemon.** Use the
  `.env` file next to the script (or `EnvironmentFile=` on systemd).
- **Tools installed in `~/.local/bin`, `~/.npm-global/bin` or `/opt/homebrew/bin`
  are not on `PATH`.** The daemon shells out to `claude`, `codex` and `agy`, so a
  missing PATH means **Codex silently reports no rate limits** rather than erroring.

Fix per platform:

```ini
# systemd (Linux):  ~/.config/systemd/user/clawdmeter.service
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

Do **not** put the token itself in the plist's `EnvironmentVariables` — it leaks to
anyone who runs `launchctl print`. Use the `.env` file. `launchctl setenv` is also
wrong: it does not survive a reboot.

## Quick verification

```bash
# what the daemon actually sees
python3 -c "import os;print('token set:', bool(os.environ.get('CLAUDE_CODE_OAUTH_TOKEN')))"
codex login status 2>/dev/null || echo 'codex: not logged in'
agy models        # free; lists models if signed in
```

Then watch the log for real numbers — `5h=..% 7d=..%` for Claude, `Codex: {...}`
for Codex — and for `Pushing to http://<device>/api/usage OK`.
