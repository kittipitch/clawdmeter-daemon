#!/usr/bin/env python3
"""clawdmeter-daemon — one daemon, three ways to reach the device.

Polls the Claude API rate-limit headers (using the OAuth token Claude Code already
stores on this machine) and delivers your 5h / 7d usage to a desk device by any of:

  * serial  — write JSON lines over USB CDC (the original Clawdmeter ESP32-S3)
  * push    — HTTP POST to the device   (SmallTV behind Wi-Fi client isolation)
  * serve   — HTTP server the device polls (SmallTV pull mode / anything)

Pick one or several; they all share the same poller, token handling and tray icon.
Cross-platform: Windows, macOS and Linux (tray + login autostart on each).

    pip install -r requirements.txt
    python clawdmeter_daemon.py --serial                 # auto-detect the COM port
    python clawdmeter_daemon.py --push                    # push to every SmallTV it finds (mDNS)
    python clawdmeter_daemon.py --push-to 192.168.1.50    # push to a specific SmallTV
    python clawdmeter_daemon.py --serve --port 8787       # serve for the device to pull
    python clawdmeter_daemon.py --serial --serve          # several at once
    python clawdmeter_daemon.py --install                 # start at login (per-user, this OS)

Firmware that speaks the contract:
  * Clawdmeter (ESP32-S3): https://github.com/giovi321/clawdmeter-win
  * SmallTV (ESP8266):     https://github.com/giovi321/smalltv-mod

Payload contract: {"s":29,"sr":142,"w":4,"wr":9876,"st":"allowed","ok":true}
  s/w  = 5h / 7d utilization %     sr/wr = minutes until each window resets
  st   = rate-limit status         ok    = false => no data (e.g. not logged in)
"""

import argparse
from datetime import datetime, timezone
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from decimal import InvalidOperation

import httpx
import aqi as pyaqi

# ---- .env ------------------------------------------------------------------
# No python-dotenv dependency -- this only ever needs to set a handful of flat
# KEY=VALUE env vars, so a tiny parser avoids adding a package for it. Values
# already present in the real environment always win (setdefault), so a real
# `export FOO=bar` or a systemd unit's Environment= still overrides the file.


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key:
            os.environ.setdefault(key, val)


# Script-directory .env first (works regardless of cwd, e.g. under systemd),
# then a .env in the current working directory (covers `python3 -m` / dev use).
_load_dotenv(Path(__file__).resolve().parent / ".env")
_load_dotenv(Path.cwd() / ".env")

# ---- Config ---------------------------------------------------------------

DEFAULT_POLL_INTERVAL = 60     # seconds between Claude API refreshes
DEFAULT_PUSH_INTERVAL = 20     # seconds between HTTP pushes to the device
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8787

# Internal argv flag: re-invoke ourselves to show the push-targets input dialog in
# its own process (so Tk runs on a real main thread and accepts keyboard input).
DIALOG_FLAG = "--_targets_dialog"

# Serial auto-detect (the Clawdmeter ESP32-S3 enumerates as Espressif CDC).
ESPRESSIF_VID = 0x303A
DEVICE_PID = 0x1001
BAUD_RATE = 115200
SERIAL_TIMEOUT = 1            # s, non-blocking readline
PORT_CHECK_INTERVAL = 5      # s, re-verify the COM port still exists

CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
TOKEN_ENDPOINT = "https://platform.claude.com/v1/oauth/token"
TOKEN_REFRESH_MARGIN = 300    # refresh 5 min before expiry

API_URL = "https://api.anthropic.com/v1/messages"
API_HEADERS_TEMPLATE = {
    "anthropic-version": "2023-06-01",
    "anthropic-beta": "oauth-2025-04-20",
    "Content-Type": "application/json",
    "User-Agent": "claude-code/2.1.146",
}
API_BODY = {
    "model": "claude-haiku-4-5-20251001",
    "max_tokens": 1,
    "messages": [{"role": "user", "content": "hi"}],
}

# ---- Google Calendar (optional feature, off by default) -------------------
# Same shape as the Claude token handling above: a refresh token cached on disk,
# refreshed silently as needed, no interactive re-auth once set up. Unlike the
# Claude token (issued for you by `claude setup-token`), Google requires YOU to
# create an OAuth client first (Google Cloud Console has no equivalent of
# setup-token) — see --calendar-auth's printed instructions.
#
# Two auth methods, both flat in $HOME to match this daemon's existing convention
# (~/.clawdmeter-daemon.json, ~/.clawdmeter-daemon.log). Service account is
# preferred (see _google_service_account_path() below and clawdmeter-daemon/README.md)
# — the OAuth Desktop-app flow works but its refresh token expires after 7 days
# unless the GCP project is published to Production, which isn't always possible
# (e.g. another OAuth client in the same project has a non-HTTPS redirect URI).
#   ~/.clawdmeter-google-client.json  - {"client_id":..,"client_secret":..}, YOU provide
#                                        this (Google Cloud Console OAuth client,
#                                        type "Desktop app"). Never written by this code.
#   ~/.clawdmeter-google-token.json   - {"refresh_token":..,"access_token":..,"expires_at":..},
#                                        written once by --calendar-auth, refreshed silently
#                                        after that.
GOOGLE_CLIENT_PATH = Path.home() / ".clawdmeter-google-client.json"
GOOGLE_TOKEN_PATH = Path.home() / ".clawdmeter-google-token.json"
# Service-account key JSON, downloaded whole from Google Cloud Console (Create
# Credentials -> Service account -> Keys -> Add key -> JSON). No consent
# screen, no refresh token, never expires on its own. YOU must also share your
# calendar with this key's client_email (Calendar settings -> "Share with
# specific people") -- a service account only sees calendars explicitly shared
# with it, and (personal Gmail, no Workspace admin console) domain-wide
# delegation isn't available here, so sharing is the only path. See
# clawdmeter-daemon/README.md for the full walkthrough.
#
# Path is configurable via GOOGLE_APPLICATION_CREDENTIALS (Google's own
# standard env var for exactly this, recognized by their other client
# libraries too -- not invented for this project), settable in .env like
# every other secret here (see "---- .env ----" near the top of this file).
# Falls back to the fixed path below if unset, so a plain drop-the-file setup
# still works with zero configuration.
GOOGLE_SERVICE_ACCOUNT_PATH_DEFAULT = Path.home() / ".clawdmeter-google-service-account.json"


def _google_service_account_path() -> Path:
    env = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    return Path(env).expanduser() if env else GOOGLE_SERVICE_ACCOUNT_PATH_DEFAULT
GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
# Service-account credentials additionally need calendarlist write access --
# not for reading events (calendar.readonly + the ACL share already cover
# that), but _ensure_calendar_list_entry() calls calendarList.insert() to
# subscribe the service account to a shared calendar it can't otherwise see
# in its own (empty) calendarList, purely to get a real per-calendar color
# back instead of falling back to the device's single default accent for
# every event. This only touches the service account's OWN calendarList
# (a self-contained action -- it can only add calendars it's already been
# granted event access to via sharing), not the calendar owner's data.
GOOGLE_SERVICE_ACCOUNT_SCOPES = [GOOGLE_CALENDAR_SCOPE, "https://www.googleapis.com/auth/calendar.calendarlist"]
GOOGLE_TOKEN_REFRESH_MARGIN = 300     # refresh 5 min before expiry, same margin as Claude's
DEFAULT_CALENDAR_INTERVAL = 300       # s; events don't change fast, spare the API quota
DEFAULT_WEATHER_INTERVAL = 600        # s; matches Open-Meteo's own forecast update cadence
DEFAULT_ZAI_INTERVAL = 300            # s; same cadence as calendar -- this endpoint looks like
                                       # a free dashboard read (no completion call riding under
                                       # it, unlike Claude's usage ping), but it's undocumented
                                       # by z.ai, so kept conservative rather than assumed free
                                       # under heavy polling. See CLAUDE.md's z.ai research notes.

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_AQ_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
ZAI_QUOTA_URL = "https://api.z.ai/api/monitor/usage/quota/limit"
OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"

DEFAULT_CODEX_INTERVAL = 300           # s; each poll spins up `codex app-server` briefly
                                        # (real subprocess, ~1-2s) to read cached rate-limit
                                        # state via RPC -- no model call, kept conservative
                                        # anyway, same reasoning as z.ai.

DEFAULT_ANTIGRAVITY_INTERVAL = 1800    # s; UNLIKE Codex, each poll fires a real `agy -p`
                                        # prompt (genuine cost, see poll_antigravity()) --
                                        # 30 min default specifically because of that cost,
                                        # not just convention.
DEFAULT_OPENROUTER_INTERVAL = 300       # s; OpenRouter is one cheap authenticated GET,
                                        # no model/token cost, so a short interval is fine.


# A daemon launched windowless (pythonw) or headless has no visible console, so
# every message also goes to this file — the one place to look when "it didn't
# start". Kept small by a crude size cap (rotate to .1 past ~1 MB).
LOG_PATH = Path.home() / ".clawdmeter-daemon.log"
_LOG_MAX_BYTES = 1_000_000
_log_lock = threading.Lock()


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    with _log_lock:
        try:
            print(line, flush=True)
        except (OSError, ValueError):
            pass    # no usable stdout (pythonw with a detached console)
        try:
            if LOG_PATH.exists() and LOG_PATH.stat().st_size > _LOG_MAX_BYTES:
                LOG_PATH.replace(LOG_PATH.with_name(LOG_PATH.name + ".1"))
            with LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass    # never let logging take the daemon down


# Run a child process without flashing a console window on Windows (important when
# launched via pythonw — otherwise spawning `claude` pops a visible window).
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW


def _run(cmd, **kw):
    if _NO_WINDOW:
        kw["creationflags"] = kw.get("creationflags", 0) | _NO_WINDOW
    return subprocess.run(cmd, **kw)


# Spawning Claude Code is a last-resort token refresh; never do it more than once
# per this many seconds, so a failing direct refresh can't pop it every poll.
_CLAUDE_REFRESH_COOLDOWN = 900
_last_claude_refresh = 0.0


# ---- Config persistence ---------------------------------------------------
# Remembers the transport you pick in the tray, so it survives restarts.

CONFIG_PATH = Path.home() / ".clawdmeter-daemon.json"


def load_config() -> dict:
    cfg = {
        "transport": None,        # "serial" | "push" | "serve"
        "push_url": "",
        "serve_host": DEFAULT_HOST,
        "serve_port": DEFAULT_PORT,
        "serial_port": None,      # None => auto-detect
        "push_interval": DEFAULT_PUSH_INTERVAL,
        "calendar_enabled": False,             # off by default, matches every other opt-in feature here
        "calendar_id": "",   # empty = auto-detect active/checked calendars; comma-separated ids to override
        "calendar_interval": DEFAULT_CALENDAR_INTERVAL,
        "weather_enabled": False,              # off by default, matches every other opt-in feature here
        "weather_interval": DEFAULT_WEATHER_INTERVAL,
    }
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text()))
    except (OSError, json.JSONDecodeError):
        pass
    # Defensive denylist, not an expected path: every real credential in
    # this daemon (Claude token, Google OAuth, --zai-key) is deliberately
    # kept OUT of this file already -- see the module-level comment on
    # _zai_api_key. This only guards against a stale key surviving from a
    # manual edit or an old
    # daemon version that once wrote one here; load_config() blindly merges
    # whatever's on disk otherwise, and save_config() would silently persist
    # it forever.
    _secret_like = [k for k in cfg if any(s in k.lower() for s in ("key", "token", "secret", "password"))]
    for k in _secret_like:
        del cfg[k]
    if _secret_like:
        log(f"Config: dropped secret-like key(s) found in {CONFIG_PATH}: {', '.join(_secret_like)}")
    return cfg


def save_config(cfg: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    except OSError as e:
        log(f"Could not save config: {e}")


# ---- Shared state ---------------------------------------------------------

def _push_hosts(target: str) -> list:
    """Turn the stored push target string into bare hosts for display, e.g.
    "http://192.168.1.44/api/usage, http://smalltv.local/api/usage"
    -> ["192.168.1.44", "smalltv.local"]. Keeps the tray text short."""
    hosts = []
    for url in (target or "").split(", "):
        url = url.strip()
        if not url:
            continue
        m = re.match(r"https?://([^/]+)", url)
        hosts.append(m.group(1) if m else url)
    return hosts


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.status = "Starting..."
        self.payload: dict = {"ok": False}
        self.version = 0           # bumped on every set_payload (serial change-detect)
        self.last_update = 0.0
        self.port = None           # serial COM port, if serial transport active
        self.push_target = ""      # device URL, if push transport active
        self.endpoint = ""         # serve URL, if serve transport active
        self.hid_enabled = True
        self.stop_event = threading.Event()
        self.refresh_event = threading.Event()
        # Set by the usage push loop (30s cadence, always running) when a device
        # transitions from unreachable -> reachable. Lets calendar/weather's own
        # push loop (which can run at a much longer interval, e.g. 300s) wake up
        # and push immediately after a reboot/reconnect instead of waiting out
        # its full interval -- bounded by the usage loop's 30s cadence either way,
        # so it never fires faster than that even if the device is flapping.
        self.push_kick_event = threading.Event()

    def set_status(self, status: str, port: str | None = ...) -> None:
        with self.lock:
            self.status = status
            if port is not ...:
                self.port = port

    def set_payload(self, payload: dict, keep_last_good: bool = True) -> None:
        with self.lock:
            if payload.get("ok") or not (keep_last_good and self.payload.get("ok")):
                self.payload = payload
                self.version += 1
            if payload.get("ok"):
                self.last_update = time.time()

    def get_payload(self) -> dict:
        with self.lock:
            return dict(self.payload)

    def get_payload_versioned(self):
        with self.lock:
            return dict(self.payload), self.version

    def get_tooltip(self) -> str:
        """Full detail for the icon's hover tooltip (multi-line). Push targets are
        trimmed to hosts and capped so even many devices stay readable."""
        with self.lock:
            lines = [f"clawdmeter — {self.status}"]
            p = self.payload
            if p.get("ok"):
                lines.append(f"5h {p['s']}%   7d {p['w']}%")
            if self.port:
                lines.append("serial " + self.port)
            if self.push_target:
                hosts = _push_hosts(self.push_target)
                shown = hosts[:6]
                line = "push -> " + ", ".join(shown)
                if len(hosts) > len(shown):
                    line += f" (+{len(hosts) - len(shown)} more)"
                lines.append(line)
            elif self.endpoint:
                lines.append(self.endpoint)
            return "\n".join(lines)

    def get_menu_header(self) -> str:
        """Compact one-line summary for the tray menu header. Kept narrow on purpose:
        a long push-target list would otherwise stretch the whole popover, so we show
        the first host plus a count and leave the full list to the hover tooltip."""
        with self.lock:
            parts = [f"clawdmeter — {self.status}"]
            p = self.payload
            if p.get("ok"):
                parts.append(f"5h {p['s']}%  7d {p['w']}%")
            if self.port:
                parts.append("serial " + self.port)
            if self.push_target:
                hosts = _push_hosts(self.push_target)
                if len(hosts) == 1:
                    parts.append("push -> " + hosts[0])
                elif hosts:
                    parts.append(f"push -> {hosts[0]} (+{len(hosts) - 1} more)")
            elif self.endpoint:
                parts.append(self.endpoint)
            return "   ".join(parts)

    def get_status_key(self) -> str:
        with self.lock:
            s = self.status.lower()
            if "token" in s or "login" in s or "error" in s:
                return "error"
            if self.payload.get("ok"):
                return "ok"
            return "searching"


state = State()
# Last reason read_token() returned None — shown in the tray/console.
_auth_hint = ""

# z.ai's own API key (for the quota endpoint). Set once from --zai-key/
# CLAWDMETER_ZAI_KEY in main(); intentionally NOT persisted to
# ~/.clawdmeter-daemon.json (same treatment as CLAUDE_CODE_OAUTH_TOKEN -- a
# credential, not a UI preference).
_zai_api_key = ""

# OpenRouter's own API key (for the key/usage endpoint). Set once from
# --openrouter-key / CLAWDMETER_OPENROUTER_KEY / ~/.openrouter_dot_ai_key in
# main(); intentionally NOT persisted to ~/.clawdmeter-daemon.json.
_openrouter_api_key = ""


# The devices no longer support a write-auth key (removed -- plaintext HTTP
# gave it no real security value, only accidental-write protection, which a
# per-device "daemon source IP" setting on the device itself now covers
# instead). Kept as a no-op so the 6 call sites below don't need touching.
def _push_headers() -> dict:
    return {}

# Reuses State's generic set_payload/get_payload machinery for the calendar
# feature (get_tooltip/get_menu_header are usage-payload-shaped and unused here).
calendar_state = State()
_calendar_auth_hint = ""

# Weather/AQ feature: same generic State reuse as calendar_state.
weather_state = State()

# z.ai quota feature: same generic State reuse.
zai_state = State()

# OpenRouter spend feature: same generic State reuse.
openrouter_state = State()

# Codex CLI quota feature: same generic State reuse. No credential of its
# own here -- relies on `codex login` already being done on this machine.
codex_state = State()

# Antigravity CLI (`agy`) quota feature: same generic State reuse. No
# credential of its own -- relies on `agy` already being authenticated on
# this machine.
antigravity_state = State()


# ---- Credential / token management ----------------------------------------

def _read_credentials_file() -> dict | None:
    try:
        return json.loads(CREDENTIALS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _write_credentials_file(data: dict) -> None:
    try:
        tmp = CREDENTIALS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(CREDENTIALS_PATH)
        try:
            os.chmod(CREDENTIALS_PATH, 0o600)   # holds an OAuth token; readable by us only
        except OSError:
            pass
    except OSError as e:
        log(f"Error writing credentials: {e}")


def _get_oauth_block(creds: dict) -> dict | None:
    return creds.get("claudeAiOauth") if isinstance(creds, dict) else None


def _extract_access_token(blob: str) -> str | None:
    blob = blob.strip()
    if not blob:
        return None
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        if isinstance(data.get("accessToken"), str):
            return data["accessToken"]
        for v in data.values():
            if isinstance(v, dict) and isinstance(v.get("accessToken"), str):
                return v["accessToken"]
    m = re.search(r'"accessToken"\s*:\s*"([^"]+)"', blob)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_\-.~+/=]{20,}", blob):
        return blob
    return None


def _is_token_expired(oauth: dict) -> bool:
    expires_at = oauth.get("expiresAt")
    if not isinstance(expires_at, (int, float)):
        return False
    return time.time() >= (expires_at / 1000.0 - TOKEN_REFRESH_MARGIN)


def _refresh_token(oauth: dict, creds: dict) -> str | None:
    refresh_tok = oauth.get("refreshToken")
    if not refresh_tok:
        log("No refresh token available")
        return None
    log("Refreshing OAuth token...")
    try:
        resp = httpx.post(
            TOKEN_ENDPOINT,
            data={"grant_type": "refresh_token", "refresh_token": refresh_tok},
            headers={"User-Agent": API_HEADERS_TEMPLATE["User-Agent"]},
            timeout=20.0,
        )
    except httpx.HTTPError as e:
        log(f"Token refresh request failed: {e}")
        return None
    if resp.status_code >= 400:
        log(f"Token refresh HTTP {resp.status_code}: {resp.text[:200]}")
        return None
    try:
        body = resp.json()
    except (json.JSONDecodeError, ValueError):
        log("Token refresh returned invalid JSON")
        return None
    new_access = body.get("access_token")
    if not new_access:
        log("Token refresh response missing access_token")
        return None
    oauth["accessToken"] = new_access
    if "refresh_token" in body:
        oauth["refreshToken"] = body["refresh_token"]
    if "expires_in" in body:
        oauth["expiresAt"] = int((time.time() + body["expires_in"]) * 1000)
    elif "expires_at" in body:
        oauth["expiresAt"] = int(body["expires_at"] * 1000)
    _write_credentials_file(creds)
    log("Token refreshed successfully")
    return new_access


def _refresh_via_claude_code() -> str | None:
    """Spawn Claude Code (windowless, rate-limited) so it refreshes its own token."""
    global _last_claude_refresh
    now = time.time()
    if now - _last_claude_refresh < _CLAUDE_REFRESH_COOLDOWN:
        return None
    _last_claude_refresh = now
    log("Spawning Claude Code to refresh token...")
    try:
        _run(["claude", "-p", "hi", "--max-turns", "1"], capture_output=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        log(f"Could not refresh via Claude Code: {e}")
        return None
    creds = _read_credentials_file()
    oauth = _get_oauth_block(creds) if creds else None
    if oauth and not _is_token_expired(oauth):
        log("Token refreshed via Claude Code")
        return oauth.get("accessToken")
    log("Claude Code did not refresh the token - re-login may be required")
    return None


def _read_token_keychain() -> str | None:
    import getpass
    try:
        out = _run(
            ["security", "find-generic-password", "-s",
             "Claude Code-credentials", "-a", getpass.getuser(), "-w"],
            check=True, capture_output=True, text=True, timeout=10,
        )
    except (subprocess.CalledProcessError, FileNotFoundError,
            subprocess.TimeoutExpired) as e:
        log(f"Keychain read failed: {e}")
        return None
    return _extract_access_token(out.stdout)


def read_token() -> str | None:
    """Return a Claude OAuth token.

    Prefers CLAUDE_CODE_OAUTH_TOKEN — a long-lived token from `claude setup-token`,
    the robust choice for an always-on daemon. Otherwise falls back to the
    credentials Claude Code stores on disk (which expire and, for some subscription
    logins, carry no refresh token, so they can't be renewed headlessly).
    """
    global _auth_hint
    env_tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if env_tok:
        _auth_hint = ""
        return env_tok

    if sys.platform == "darwin":
        tok = _read_token_keychain()
        _auth_hint = "" if tok else "Not logged in - run 'claude' to log in"
        return tok

    creds = _read_credentials_file()
    if not creds:
        _auth_hint = "No Claude credentials - run 'claude setup-token' or 'claude'"
        return None
    oauth = _get_oauth_block(creds)
    if not oauth or not isinstance(oauth.get("accessToken"), str):
        _auth_hint = "Not logged in - run 'claude setup-token' or 'claude'"
        return None

    if _is_token_expired(oauth):
        # The OAuth refresh grant + a spawned `claude` are the same autonomous
        # mechanisms the original daemon used; both need a usable refresh path. If
        # there's no refresh token and a fresh `claude` 401s, only a long-lived
        # token fixes it: `claude setup-token` -> set CLAUDE_CODE_OAUTH_TOKEN.
        tok = _refresh_token(oauth, creds) or _refresh_via_claude_code()
        _auth_hint = "" if tok else "Token expired - run: claude setup-token (long-lived)"
        return tok

    _auth_hint = ""
    return oauth.get("accessToken")


# ---- API polling ----------------------------------------------------------

def poll_api(token: str) -> tuple[dict | None, bool]:
    """Minimal API call; extract usage headers. Returns (payload, auth_failed)."""
    headers = dict(API_HEADERS_TEMPLATE)
    headers["Authorization"] = f"Bearer {token}"
    try:
        resp = httpx.post(API_URL, headers=headers, json=API_BODY, timeout=20.0)
    except httpx.HTTPError as e:
        log(f"API call failed: {e}")
        return None, False
    if resp.status_code in (401, 403):
        return None, True
    if resp.status_code >= 400:
        # A 429 rate_limit_error means the usage window itself is exhausted --
        # and it will KEEP 429ing every poll (the quota check is an API call),
        # so freezing on the last good payload would show e.g. 97% forever
        # while real usage is full. Anthropic still sends the full
        # anthropic-ratelimit-unified-* header set on the 429 (verified live:
        # real utilization AND reset epochs for both windows), so fall through
        # and parse those like a success -- real numbers, nothing inferred.
        if resp.status_code == 429 and "rate_limit_error" in resp.text:
            log("429 rate_limit_error - parsing real utilization from response headers")
        else:
            log(f"API HTTP {resp.status_code}: {resp.text[:200]}")
            return None, False

    now = time.time()

    def hdr(name, default="0"):
        return resp.headers.get(name, default)

    def reset_minutes(ts):
        try:
            mins = (float(ts) - now) / 60.0
        except ValueError:
            return 0
        return int(round(mins)) if mins > 0 else 0

    def pct(util):
        try:
            # 429 responses can report >1.0 utilization (e.g. 1.05); the
            # screen is a 0..100 meter, so clamp here rather than send >100.
            return max(0, min(100, int(round(float(util) * 100))))
        except ValueError:
            return 0

    payload = {
        "s":  pct(hdr("anthropic-ratelimit-unified-5h-utilization")),
        "sr": reset_minutes(hdr("anthropic-ratelimit-unified-5h-reset")),
        "w":  pct(hdr("anthropic-ratelimit-unified-7d-utilization")),
        "wr": reset_minutes(hdr("anthropic-ratelimit-unified-7d-reset")),
        "st": hdr("anthropic-ratelimit-unified-5h-status", "unknown"),
        "ok": True,
    }
    return payload, False


def do_poll() -> None:
    """One poll cycle: token -> API -> update shared state."""
    token = read_token()
    if not token:
        state.set_status(_auth_hint or "No token - run 'claude setup-token'")
        state.set_payload({"ok": False})
        return
    payload, auth_failed = poll_api(token)
    if auth_failed:
        state.set_status("Refreshing token...")
        creds = _read_credentials_file()
        oauth = _get_oauth_block(creds) if creds else None
        if oauth and creds:
            new_token = _refresh_token(oauth, creds)
            if new_token:
                payload, _ = poll_api(new_token)
    if payload is not None:
        state.set_payload(payload)
        state.set_status("Connected")
        log(f"5h={payload['s']}% 7d={payload['w']}% st={payload['st']}")
    elif "token" not in state.status.lower():
        state.set_status("API error - retrying")


def poller_loop(interval: float) -> None:
    log(f"Polling Claude every {interval:.0f}s")
    while not state.stop_event.is_set():
        try:
            do_poll()
        except Exception as e:
            # Each poll_* function already catches its own expected failure
            # modes (HTTP errors, bad JSON) -- this is the backstop against
            # anything unexpected (a response shape change, etc.). Without
            # it, this thread is daemon=True and just dies silently on any
            # uncaught exception, permanently killing this one feature until
            # the whole daemon is restarted, with only a traceback on stderr
            # (invisible in a launchd/systemd deployment) as evidence.
            log(f"Claude poll: unexpected error, will retry next interval: {e}")
        state.refresh_event.wait(interval)
        state.refresh_event.clear()


# ---- Google Calendar: credential management --------------------------------

def _read_google_client() -> dict | None:
    try:
        data = json.loads(GOOGLE_CLIENT_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict) and data.get("client_id") and data.get("client_secret"):
        return data
    return None


def _read_google_token() -> dict | None:
    try:
        return json.loads(GOOGLE_TOKEN_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _write_google_token(data: dict) -> None:
    try:
        tmp = GOOGLE_TOKEN_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(GOOGLE_TOKEN_PATH)
        try:
            os.chmod(GOOGLE_TOKEN_PATH, 0o600)   # holds a refresh token; readable by us only
        except OSError:
            pass
    except OSError as e:
        log(f"Error writing Google token: {e}")


def _google_token_expired(tok: dict) -> bool:
    expires_at = tok.get("expires_at")
    if not isinstance(expires_at, (int, float)):
        return True   # no expiry recorded => treat as stale, force a refresh
    return time.time() >= (expires_at - GOOGLE_TOKEN_REFRESH_MARGIN)


def _refresh_google_token(tok: dict, client: dict) -> str | None:
    refresh_tok = tok.get("refresh_token")
    if not refresh_tok:
        log("No Google refresh token available - run --calendar-auth")
        return None
    log("Refreshing Google Calendar token...")
    try:
        resp = httpx.post(
            GOOGLE_TOKEN_ENDPOINT,
            data={
                "client_id": client["client_id"],
                "client_secret": client["client_secret"],
                "refresh_token": refresh_tok,
                "grant_type": "refresh_token",
            },
            timeout=20.0,
        )
    except httpx.HTTPError as e:
        log(f"Google token refresh request failed: {e}")
        return None
    if resp.status_code >= 400:
        log(f"Google token refresh HTTP {resp.status_code}: {resp.text[:200]}")
        return None
    try:
        body = resp.json()
    except (json.JSONDecodeError, ValueError):
        log("Google token refresh returned invalid JSON")
        return None
    new_access = body.get("access_token")
    if not new_access:
        log("Google token refresh response missing access_token")
        return None
    tok["access_token"] = new_access
    if "refresh_token" in body:   # Google sometimes rotates it; rare but must be kept if sent
        tok["refresh_token"] = body["refresh_token"]
    tok["expires_at"] = time.time() + body.get("expires_in", 3600)
    _write_google_token(tok)
    log("Google token refreshed successfully")
    return new_access


def _read_google_service_account() -> dict | None:
    try:
        data = json.loads(_google_service_account_path().read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict) and data.get("type") == "service_account" and data.get("client_email"):
        return data
    return None


# Cached google.oauth2.service_account.Credentials (built once) and the last
# access token + expiry (re-exchanged only once it's stale) -- same shape as
# the OAuth refresh-token caching above, so a poll doesn't pay a fresh
# JWT-sign + token-exchange round trip every 300s for no reason.
_service_account_creds = None
_service_account_token: dict = {}   # {"access_token":.., "expires_at":..}


def _service_account_access_token(sa: dict) -> str | None:
    # Only google-auth's RSA-signed JWT assertion is used here (the one part
    # worth a real library instead of hand-rolling) -- the actual token
    # exchange goes through httpx like every other HTTP call in this file,
    # deliberately avoiding a dependency on the `requests` package that
    # google.auth.transport.requests would otherwise pull in.
    global _service_account_creds, _service_account_token
    if _service_account_token and time.time() < _service_account_token["expires_at"] - GOOGLE_TOKEN_REFRESH_MARGIN:
        return _service_account_token["access_token"]
    try:
        from google.oauth2 import service_account
    except ImportError:
        log("Calendar: google-auth not installed - `pip install google-auth` "
            "(see clawdmeter-daemon/requirements.txt)")
        return None
    try:
        if _service_account_creds is None:
            _service_account_creds = service_account.Credentials.from_service_account_info(
                sa, scopes=GOOGLE_SERVICE_ACCOUNT_SCOPES)
        # _make_authorization_grant_assertion() returns bytes -- httpx's form
        # encoder doesn't decode a bytes dict value, it stringifies it (the
        # literal "b'...'" repr, quotes included), so undecoded this becomes
        # a garbage assertion string and Google returns a content-free
        # "400 invalid_request" that looks identical to a bad key. Confirmed
        # live: this exact bug, first real-account test, fixed by .decode().
        assertion = _service_account_creds._make_authorization_grant_assertion().decode("ascii")
        resp = httpx.post(
            GOOGLE_TOKEN_ENDPOINT,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
            timeout=20.0,
        )
    except httpx.HTTPError as e:
        log(f"Calendar: service-account token request failed: {e}")
        return None
    except Exception as e:
        log(f"Calendar: service-account token fetch failed: {e}")
        return None
    if resp.status_code >= 400:
        log(f"Calendar: service-account token exchange HTTP {resp.status_code}: {resp.text[:200]}")
        return None
    try:
        body = resp.json()
    except (json.JSONDecodeError, ValueError):
        log("Calendar: service-account token exchange returned invalid JSON")
        return None
    access_token = body.get("access_token")
    if not access_token:
        log("Calendar: service-account token exchange response missing access_token")
        return None
    _service_account_token = {
        "access_token": access_token,
        "expires_at": time.time() + body.get("expires_in", 3600),
    }
    return access_token


def read_google_calendar_token() -> str | None:
    """Return a Google access token. Tries the service account first (no
    consent screen, no refresh-token expiry -- see _google_service_account_path()),
    then falls back to the interactive-OAuth refresh token, refreshing it as
    needed. Mirrors read_token()'s shape for the Claude token above — the whole
    point is this never needs interactive re-auth once set up once, same as
    claude setup-token being a one-time thing for the Claude side.
    """
    global _calendar_auth_hint
    sa = _read_google_service_account()
    if sa:
        token = _service_account_access_token(sa)
        _calendar_auth_hint = "" if token else (
            "Service-account token fetch failed - check "
            f"{_google_service_account_path()} and that your calendar is "
            "shared with its client_email")
        return token
    client = _read_google_client()
    if not client:
        _calendar_auth_hint = (f"No {_google_service_account_path().name} or {GOOGLE_CLIENT_PATH.name} "
                                "- see clawdmeter-daemon/README.md (service account, recommended) "
                                "or --calendar-auth --help (OAuth)")
        return None
    tok = _read_google_token()
    if not tok or not tok.get("refresh_token"):
        _calendar_auth_hint = "Not authorized - run: python clawdmeter_daemon.py --calendar-auth"
        return None
    if _google_token_expired(tok):
        new_access = _refresh_google_token(tok, client)
        _calendar_auth_hint = "" if new_access else "Google token refresh failed - re-run --calendar-auth"
        return new_access
    _calendar_auth_hint = ""
    return tok.get("access_token")


# ---- Google Calendar: one-time interactive setup (PKCE loopback flow) ------
# Google's installed-app OAuth flow: the browser (anywhere) completes consent
# and redirects to http://127.0.0.1:<port>/ on THIS machine, which must be the
# same machine running this command — so run --calendar-auth on a machine with
# a browser (e.g. your Mac), not over a headless SSH session on the Pi. Deploy
# the resulting ~/.clawdmeter-google-token.json to the Pi afterwards (scp), same
# as how the Claude Code long-lived token was deployed there in this project.

def _pkce_pair() -> tuple[str, str]:
    import base64
    import hashlib
    import secrets
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def calendar_auth_flow() -> None:
    client = _read_google_client()
    if not client:
        print(f"""
No Google OAuth client found at {GOOGLE_CLIENT_PATH}.

One-time setup (you do this once, in a browser, on any machine with a Google
account — takes a few minutes):

  1. https://console.cloud.google.com/projectcreate - create a project
     (any name).
  2. https://console.cloud.google.com/apis/library/calendar-json.googleapis.com
     - enable the Google Calendar API for that project.
  3. https://console.cloud.google.com/apis/credentials/consent
     - configure the OAuth consent screen. User type: External. Add your own
       Google account as a test user, OR (recommended so the refresh token
       doesn't expire after 7 days) publish the app to Production — for a
       personal app with only the Calendar readonly scope, Google does not
       require the full verification review, you'll just see one "unverified
       app" warning to click through during step 5 below.
  4. https://console.cloud.google.com/apis/credentials - Create Credentials
     -> OAuth client ID -> Application type: "Desktop app". Download the
     client ID + secret.
  5. Save them here as JSON:
       {{"client_id": "...", "client_secret": "..."}}
     -> {GOOGLE_CLIENT_PATH}
  6. Re-run: python clawdmeter_daemon.py --calendar-auth
""")
        return

    import webbrowser
    from urllib.parse import urlencode, urlparse, parse_qs

    verifier, challenge = _pkce_pair()
    result: dict = {}
    done = threading.Event()

    class _CB(BaseHTTPRequestHandler):
        def do_GET(self):
            qs = parse_qs(urlparse(self.path).query)
            if "code" in qs:
                result["code"] = qs["code"][0]
                body = b"<html><body>Authorized. You can close this tab.</body></html>"
            else:
                result["error"] = qs.get("error", ["unknown"])[0]
                body = b"<html><body>Authorization failed - check the terminal.</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)
            done.set()

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _CB)
    port = srv.server_address[1]
    threading.Thread(target=srv.handle_request, daemon=True).start()

    redirect_uri = f"http://127.0.0.1:{port}/"
    auth_url = GOOGLE_AUTH_ENDPOINT + "?" + urlencode({
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_CALENDAR_SCOPE,
        "access_type": "offline",     # request a refresh token
        "prompt": "consent",          # force one even on a re-auth (Google only issues it once)
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })

    print(f"\nOpen this URL to authorize (attempting to open your browser too):\n\n{auth_url}\n")
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    if not done.wait(300):
        print("Timed out waiting for authorization.")
        return
    if "error" in result:
        print(f"Authorization failed: {result['error']}")
        return

    try:
        resp = httpx.post(GOOGLE_TOKEN_ENDPOINT, data={
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "code": result["code"],
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }, timeout=20.0)
    except httpx.HTTPError as e:
        print(f"Token exchange failed: {e}")
        return
    if resp.status_code >= 400:
        print(f"Token exchange HTTP {resp.status_code}: {resp.text[:300]}")
        return
    body = resp.json()
    if "refresh_token" not in body:
        print("Google did not return a refresh token. This usually means you've already "
              "authorized this client before - go to https://myaccount.google.com/permissions, "
              "remove access for this app, and run --calendar-auth again.")
        return
    _write_google_token({
        "refresh_token": body["refresh_token"],
        "access_token": body.get("access_token"),
        "expires_at": time.time() + body.get("expires_in", 3600),
    })
    print(f"Saved {GOOGLE_TOKEN_PATH}. Calendar polling can now run headless, no more browser needed.")
    print("If the daemon runs elsewhere (e.g. a Pi), copy this file there now:")
    print(f"  scp {GOOGLE_TOKEN_PATH} <host>:~/")


# ---- Google Calendar: one-time color sync (OAuth read -> service-account write) --
# The service-account auth path (see read_google_calendar_token() above) can't
# see a calendar's color on its own -- backgroundColor/colorId only exist on a
# CalendarListEntry, which is per-viewer, and a service account starts with an
# empty calendarList even for calendars explicitly shared with it (sharing
# grants event access, not a list entry). Confirmed live: reading the color
# via the service account returns Google's own arbitrary auto-assigned pick,
# not the color the calendar's real owner actually sees. This bridges that
# gap with the ONE credential that legitimately has the answer -- the human
# owner's own OAuth token -- read once, applied once, not an ongoing
# dependency (the OAuth token from --calendar-auth can expire again on its
# usual 7-day/Testing-status timer afterward and this still keeps working,
# since the applied color is stored server-side on the calendarList entry).
#
# Also confirmed live: Google's calendarList API only accepts colorId from
# its fixed 24-color palette -- a calendar using a custom color beyond that
# palette (real example: owner's actual color was #0088ff, a custom hex;
# Calendar API could only ever report/accept the nearest palette entry,
# colorId 16 / #4986e7 "Blueberry") cannot be reproduced exactly through the
# API at all. This copies colorId (a lossless copy of whatever Google itself
# already reports for that entry), not backgroundColor -- attempting to PATCH
# a custom backgroundColor directly was tried and confirmed rejected (Google
# silently re-derives it from colorId, or returns "400 Invalid color" if
# colorId is cleared).
def _get_working_oauth_token() -> str | None:
    """Shared by the --calendar-sync-* one-time commands below -- the OAuth
    token is only ever used as a one-time read of what the human owner's
    account can see, never for ongoing polling (that's the service account's
    job), so a stale/expired one just means re-running --calendar-auth, not
    a recurring maintenance burden.
    """
    client = _read_google_client()
    oauth_tok_data = _read_google_token()
    if not client or not oauth_tok_data or not oauth_tok_data.get("refresh_token"):
        print("No working OAuth credentials found - run --calendar-auth once first "
              "(this is a one-time read of your own account's data, not an ongoing "
              "dependency; see clawdmeter-daemon/README.md).")
        return None
    oauth_token = (oauth_tok_data.get("access_token")
                   if not _google_token_expired(oauth_tok_data)
                   else _refresh_google_token(oauth_tok_data, client))
    if not oauth_token:
        print("OAuth token refresh failed - re-run --calendar-auth.")
    return oauth_token


def _get_working_sa_token() -> str | None:
    sa = _read_google_service_account()
    if not sa:
        print(f"No service-account key found at {_google_service_account_path()} - "
              "see clawdmeter-daemon/README.md.")
        return None
    sa_token = _service_account_access_token(sa)
    if not sa_token:
        print("Service-account token fetch failed - see the log line above for why.")
    return sa_token


def _sync_one_calendar_color(oauth_token: str, sa_token: str, calendar_id: str) -> bool:
    """Read calendar_id's real colorId via the OAuth token (the only credential
    that can see it -- see the module comment above) and apply it to the
    service account's calendarList entry, subscribing it first if needed.
    Returns True on success. Prints its own progress/error lines.
    """
    encoded_id = urllib.parse.quote(calendar_id, safe="")
    try:
        owner_resp = httpx.get(
            f"https://www.googleapis.com/calendar/v3/users/me/calendarList/{encoded_id}",
            headers={"Authorization": f"Bearer {oauth_token}"}, timeout=20.0)
    except httpx.HTTPError as e:
        print(f"  {calendar_id}: reading your own calendarList entry failed: {e}")
        return False
    if owner_resp.status_code >= 400:
        print(f"  {calendar_id}: reading your own calendarList entry HTTP "
              f"{owner_resp.status_code}: {owner_resp.text[:200]}")
        return False
    owner_entry = owner_resp.json()
    color_id = owner_entry.get("colorId")
    if not color_id:
        print(f"  {calendar_id}: no colorId on your own entry (unexpected) - skipped.")
        return False

    if not _ensure_calendar_list_entry(sa_token, calendar_id):
        # Not necessarily fatal -- it may already be subscribed (see
        # _ensure_calendar_list_entry's 409-is-fine handling); the patch
        # below will fail loudly if the service account genuinely can't see it.
        pass
    try:
        patch_resp = httpx.patch(
            f"https://www.googleapis.com/calendar/v3/users/me/calendarList/{encoded_id}",
            headers={"Authorization": f"Bearer {sa_token}"},
            json={"colorId": color_id}, timeout=20.0)
    except httpx.HTTPError as e:
        print(f"  {calendar_id}: applying colorId to the service account failed: {e}")
        return False
    if patch_resp.status_code >= 400:
        print(f"  {calendar_id}: applying colorId HTTP {patch_resp.status_code}: "
              f"{patch_resp.text[:200]}")
        return False
    sa_entry = patch_resp.json()
    print(f"  {calendar_id}: colorId {color_id!r} synced "
          f"(backgroundColor {sa_entry.get('backgroundColor')!r}).")
    return True


def calendar_sync_color_flow(calendar_id: str) -> None:
    oauth_token = _get_working_oauth_token()
    if not oauth_token:
        return
    sa_token = _get_working_sa_token()
    if not sa_token:
        return
    if _sync_one_calendar_color(oauth_token, sa_token, calendar_id):
        print("Done.")


# ---- Google Calendar: one-time bulk sync (every calendar checked in your
# own Google Calendar sidebar) --------------------------------------------
# A service account has no "My calendars" sidebar of its own -- see
# --calendar-id's own help text and calendar_poller_loop()'s startup
# warning -- so there is no way for it to auto-detect "whatever I have
# checked" the way OAuth polling could. This is the one-time bridge: use
# the OAuth token (same one-time-read pattern as calendar_sync_color_flow
# above) to ask YOUR OWN account which calendars are checked, subscribe the
# service account + sync color for each, and print the exact --calendar-id
# value to use. Only a snapshot at the moment this runs -- if you check/
# uncheck a calendar in Google's UI afterward, re-run this command and
# update --calendar-id again; there's no way to keep it live without an
# ongoing OAuth dependency, which is exactly what switching to a service
# account was for avoiding in the first place.
def calendar_sync_selected_flow() -> None:
    oauth_token = _get_working_oauth_token()
    if not oauth_token:
        return
    sa_token = _get_working_sa_token()
    if not sa_token:
        return

    try:
        resp = httpx.get("https://www.googleapis.com/calendar/v3/users/me/calendarList",
                          headers={"Authorization": f"Bearer {oauth_token}"}, timeout=20.0)
    except httpx.HTTPError as e:
        print(f"Reading your own calendarList failed: {e}")
        return
    if resp.status_code >= 400:
        print(f"Reading your own calendarList HTTP {resp.status_code}: {resp.text[:200]}")
        return
    selected = [it["id"] for it in resp.json().get("items", [])
                if it.get("selected") and it.get("id")]
    if not selected:
        print("No calendars are checked in your Google Calendar sidebar - nothing to sync.")
        return

    print(f"{len(selected)} calendar(s) checked in your sidebar:")
    synced = [cid for cid in selected if _sync_one_calendar_color(oauth_token, sa_token, cid)]
    if not synced:
        print("None synced successfully -- see the errors above.")
        return
    print(f"\nSynced {len(synced)}/{len(selected)}. Set this as --calendar-id "
          "(or update the calendar_id in your saved config / systemd unit):")
    print("  --calendar-id " + ",".join(synced))


# ---- Google Calendar: polling -----------------------------------------------

CALENDAR_MAX_EVENTS = 6   # matches the device's CAL_MAX_EVENTS (two 3-event pages, cycled)

# Google's special auto-generated "Birthdays" calendar (from Contacts) never
# appears in calendarList.list() -- confirmed live, even with showHidden=true.
# It has no "selected" field to read, so the normal auto-detect logic can't
# see it at all, regardless of whether it's checked in the Calendar UI. Its
# events are real and fetchable by hitting this fixed ID directly (also
# confirmed live). Always included in the auto-detect (non-override) path --
# there's no per-account way to know if the user unchecked it, since Google
# doesn't expose that state anywhere queryable. Color is colorId 1 ("Cocoa")
# from /colors' "calendar" section, matching the brown swatch Google's own
# UI shows for this calendar -- birthday events carry no colorId/color of
# their own, so this is the only way to get the real brown, not a guess.
BIRTHDAY_CALENDAR_ID = "addressbook#contacts@group.v.calendar.google.com"
BIRTHDAY_CALENDAR_COLOR = "ac725e"


def _ensure_calendar_list_entry(token: str, calendar_id: str) -> dict | None:
    """POST calendarList.insert for a calendar_id this token can't already
    see in its own calendarList. The insert call's response is itself a
    CalendarListEntry, complete with a real backgroundColor Google assigns on
    subscribe -- not necessarily the same color the calendar's owner sees in
    their own sidebar (that's a per-viewer setting with no API to read it for
    a different account), but stable and distinct per calendar, which is what
    fetch_active_calendars() needs. None on failure -- callers already treat
    a missing color as "use the device's default", so this degrades safely.
    """
    try:
        resp = httpx.post(
            "https://www.googleapis.com/calendar/v3/users/me/calendarList",
            headers={"Authorization": f"Bearer {token}"},
            json={"id": calendar_id},
            timeout=20.0,
        )
    except httpx.HTTPError as e:
        log(f"Calendar list insert failed ({calendar_id}): {e}")
        return None
    if resp.status_code >= 400:
        if resp.status_code != 409:   # 409 = already subscribed, not an error
            log(f"Calendar list insert HTTP {resp.status_code} ({calendar_id}): {resp.text[:200]}")
        return None
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError):
        return None


def fetch_active_calendars(token: str, override_ids: list[str] | None) -> list[tuple[str, str | None]]:
    """Return [(calendar_id, background_color_hex_no_hash_or_None), ...].

    Default (override_ids is None/empty): auto-detect via calendarList's
    "selected" field, which is exactly the checkbox state in Google
    Calendar's own "My calendars" sidebar -- per explicit request ("get
    just the active one"), so toggling a calendar's checkbox in Google's
    UI changes what the device shows with zero daemon reconfiguration.
    Also always includes the special Birthdays calendar (see
    BIRTHDAY_CALENDAR_ID above) since it's invisible to this API entirely.
    If override_ids is set, use exactly those ids instead (still looked
    up in calendarList for color, in case the user manually listed a
    calendar that happens to be unchecked).
    """
    try:
        resp = httpx.get(
            "https://www.googleapis.com/calendar/v3/users/me/calendarList",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20.0,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as e:
        log(f"Calendar list fetch failed: {e}")
        items = []

    by_id = {it.get("id"): it for it in items if it.get("id")}

    if override_ids:
        out = []
        for cid in override_ids:
            entry = by_id.get(cid)
            if entry is None:
                # Not in this token's own calendarList -- normal for a
                # service account on its first run against a calendar that
                # was just shared with it: sharing grants event access but
                # doesn't add an entry to the grantee's own list, and
                # backgroundColor only exists on list entries, not on the
                # raw Calendar resource (it's a per-viewer setting, not a
                # calendar property). Without this, every event from this
                # calendar_id would silently fall back to the device's one
                # default accent color instead of a real per-calendar color.
                entry = _ensure_calendar_list_entry(token, cid)
            color = (entry or {}).get("backgroundColor")
            out.append((cid, color.lstrip("#") if color else None))
        return out

    return [(BIRTHDAY_CALENDAR_ID, BIRTHDAY_CALENDAR_COLOR)] + [
        (it["id"], (it.get("backgroundColor") or "").lstrip("#") or None)
        for it in items
        if it.get("selected") and it.get("id")
    ]


def fetch_event_color_map(token: str) -> dict[str, str]:
    """Return {colorId: hex_no_hash} for Google Calendar's 11 fixed
    per-event override colors (Tomato, Flamingo, Banana, ... -- the same
    swatch picker shown when you right-click a single event and choose
    "Change color"). Distinct from calendarList's per-calendar colors.
    Empty dict on failure -- callers must fall back to the calendar's
    own color, not treat this as fatal.
    """
    try:
        resp = httpx.get(
            "https://www.googleapis.com/calendar/v3/colors",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20.0,
        )
        resp.raise_for_status()
        event_colors = resp.json().get("event", {})
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as e:
        log(f"Calendar color palette fetch failed: {e}")
        return {}
    return {
        cid: (c.get("background") or "").lstrip("#")
        for cid, c in event_colors.items()
        if c.get("background")
    }


_translate_cache: dict[str, str] = {}
_translate_warned_missing = False
_translate_fail_until: dict[str, float] = {}   # text -> monotonic time to retry after
TRANSLATE_CACHE_PATH = Path.home() / ".clawdmeter-translate-cache.json"
TRANSLATE_FAIL_BACKOFF_SEC = 300.0   # don't re-pay a 15s timeout every poll for the same title
TRANSLATE_CALL_SPACING_SEC = 1.5     # pace real translate-shell calls -- a cold cache with
                                      # several new non-ASCII titles in one poll pass (up to
                                      # CALENDAR_MAX_EVENTS per calendar_id, times however many
                                      # calendar_id's are configured) would otherwise fire that
                                      # many requests back-to-back, which looks like abuse to the
                                      # backend even though total daily volume is low -- caching
                                      # already kills repeat calls, this smooths out bursts of
                                      # genuinely new titles instead.


def _load_translate_cache() -> dict[str, str]:
    try:
        return json.loads(TRANSLATE_CACHE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_translate_cache(cache: dict[str, str]) -> None:
    try:
        tmp = TRANSLATE_CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, indent=2, ensure_ascii=False))
        tmp.replace(TRANSLATE_CACHE_PATH)
    except OSError as e:
        log(f"Calendar: could not persist translate cache: {e}")


_translate_cache: dict[str, str] = _load_translate_cache()


def _translate_to_english(text: str) -> str:
    """Best-effort romanize/translate a non-ASCII event title via
    translate-shell (`trans`) so the device's own stripNonAscii() doesn't
    have to silently drop it. Confirmed live: ~5.6s/call (network
    round-trip, not CPU) and correctly romanizes rather than mistranslates
    proper nouns (e.g. a Thai market name "กาดโก้งโค้ง" -> "Kad Kong
    Khong", not a literal-meaning translation). Cached per exact title
    string -- the same title repeats every poll until the event itself
    changes, so an uncached call would re-pay that cost for no reason.
    Only successful translations are cached (mirrors the city-name cache's
    "never cache a failure" rule) so a transient failure retries next poll
    instead of getting stuck. Falls back to the original text on any
    failure or if `trans` isn't installed -- the device-side
    stripNonAscii() safety net still applies either way, so this can only
    improve on the pre-existing behavior, never break it."""
    global _translate_warned_missing
    if text in _translate_cache:
        return _translate_cache[text]
    now = time.monotonic()
    retry_after = _translate_fail_until.get(text)
    if retry_after is not None and now < retry_after:
        return text   # recent failure for this exact title -- don't re-pay the timeout yet
    trans_bin = shutil.which("trans")
    if not trans_bin:
        if not _translate_warned_missing:
            log("Calendar: `trans` (translate-shell) not found on PATH -- "
                "non-English event titles will be stripped device-side "
                "instead of translated")
            _translate_warned_missing = True
        return text
    try:
        # -e bing: translate-shell's default engine is Google's unofficial
        # web endpoint, which rate-limits by source network (confirmed live
        # -- two machines on the same home connection both got
        # "[ERROR] Rate limiting" simultaneously, so switching which host
        # runs this doesn't help). Bing's engine isn't rate-limited on the
        # same network and produces equivalent romanization.
        result = subprocess.run(
            [trans_bin, "-e", "bing", "-b", ":en", text],
            capture_output=True, text=True, timeout=15.0,
        )
        translated = result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError) as e:
        log(f"Calendar: translate-shell failed for {text!r}: {e}")
        translated = ""
    time.sleep(TRANSLATE_CALL_SPACING_SEC)   # pace real calls, see constant's comment above
    if not translated:
        _translate_fail_until[text] = now + TRANSLATE_FAIL_BACKOFF_SEC
        return text
    _translate_cache[text] = translated
    _save_translate_cache(_translate_cache)
    return translated


def poll_calendar(token: str, override_ids: list[str] | None) -> tuple[dict | None, bool]:
    """Fetch the next few upcoming events across every active (checked)
    calendar, merged and sorted by start time. Returns (payload, auth_failed).

    Each event carries a "color" (hex, no '#') -- its own per-event
    colorId override if the user set one in Google Calendar (right-click
    an event -> "Change color"), else its source calendar's real
    backgroundColor, else absent, so the device falls back to its own
    default accent color rather than showing nothing.

    Payload keys are spelled out (not terse like the usage contract's s/w/sr/wr)
    to match what features/calendar/ actually consumes on the firmware side.

    Each event also carries "calendarId" (the source calendar's own id) --
    added so the device can apply a per-calendar color override the user
    picked in its own web UI, independent of whatever real Google color (or
    lack of one) this daemon resolved above. Without it, a device-side
    override has no way to know which calendar an event came from.

    An "end" is carried too (when Google provides one) -- without it the
    device cannot tell a multi-day event from a single-day one. Google's
    all-day end.date is exclusive, so it's shipped raw and the device does
    any -1 day span rendering; see the comment at the extraction site.
    """
    import datetime
    calendars = fetch_active_calendars(token, override_ids)
    if not calendars:
        return {"ok": True, "events": []}, False

    event_color_map = fetch_event_color_map(token)

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    all_events = []
    auth_failed = False
    for calendar_id, color in calendars:
        try:
            # calendar_id can contain '#' (e.g. "en.th#holiday@group.v.calendar.google.com",
            # "addressbook#contacts@group.v.calendar.google.com") -- an unescaped '#' in an
            # f-string URL is parsed as the fragment delimiter, silently truncating the path
            # and 404ing. quote() with safe="" escapes it (and '@', ':', etc.) into the path.
            encoded_id = urllib.parse.quote(calendar_id, safe="")
            resp = httpx.get(
                f"https://www.googleapis.com/calendar/v3/calendars/{encoded_id}/events",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "timeMin": now,
                    "maxResults": CALENDAR_MAX_EVENTS,
                    "singleEvents": "true",
                    "orderBy": "startTime",
                },
                timeout=20.0,
            )
        except httpx.HTTPError as e:
            log(f"Calendar API call failed ({calendar_id}): {e}")
            continue
        if resp.status_code in (401, 403):
            auth_failed = True
            continue
        if resp.status_code >= 400:
            log(f"Calendar API HTTP {resp.status_code} ({calendar_id}): {resp.text[:200]}")
            continue
        try:
            items = resp.json().get("items", [])
        except (json.JSONDecodeError, ValueError):
            log(f"Calendar API returned invalid JSON ({calendar_id})")
            continue
        for ev in items[:CALENDAR_MAX_EVENTS]:
            start = ev.get("start", {})
            all_day = "date" in start and "dateTime" not in start
            start_val = start.get("dateTime") or start.get("date")
            # Mirror start for end. NOTE: Google's all-day end.date is
            # EXCLUSIVE -- a single-day all-day event on Aug 6 has
            # start.date="2026-08-06" and end.date="2026-08-07", not the
            # 6th twice. Ship the raw value here; the -1 day adjustment for
            # rendering a span is the device side's job (C++, not this
            # daemon). Timed events' end.dateTime is inclusive as expected.
            end = ev.get("end", {})
            end_val = end.get("dateTime") or end.get("date")
            if not start_val:
                continue
            summary = ev.get("summary") or "(no title)"
            if any(ord(c) > 127 for c in summary):
                summary = _translate_to_english(summary)
            event = {
                "summary": summary,
                "start": start_val,
                "allDay": all_day,
                "calendarId": calendar_id,
            }
            event_color = event_color_map.get(str(ev.get("colorId") or "")) or color
            if event_color:
                event["color"] = event_color
            if end_val:
                event["end"] = end_val
            all_events.append(event)

    if not all_events:
        return ({"ok": True, "events": []}, False) if not auth_failed else (None, True)

    # The same real-world event can appear on more than one calendar --
    # confirmed live: a contact's birthday shows up both on the special
    # Birthdays calendar and (independently synced) on the primary
    # calendar. Dedup by (summary, start), keeping the first occurrence.
    # `calendars` puts the Birthdays calendar first (see
    # fetch_active_calendars), so its brown-tagged copy is always seen
    # before any duplicate from another calendar and wins the dedup.
    seen = set()
    deduped = []
    for event in all_events:
        key = (event["summary"], event["start"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)

    deduped.sort(key=lambda e: e["start"])
    return {"ok": True, "events": deduped[:CALENDAR_MAX_EVENTS]}, False


def do_calendar_poll(static_ids: list[str] | None, config_url: str | None) -> None:
    # Device-supplied ids win over the static --calendar-id list when present
    # -- editing the device's Agenda tab is how a newly-shared calendar gets
    # picked up without a daemon restart. Falls back to static_ids (or
    # auto-detect) if the device field is empty/unreachable, so existing
    # CLI-only setups keep working unchanged.
    override_ids = read_device_calendar_ids(config_url) if config_url else None
    if not override_ids:
        override_ids = static_ids
    token = read_google_calendar_token()
    if not token:
        calendar_state.set_payload({"ok": False})
        if _calendar_auth_hint:
            log(f"Calendar: {_calendar_auth_hint}")
        return
    payload, auth_failed = poll_calendar(token, override_ids)
    if auth_failed:
        global _service_account_token
        if _service_account_token:
            # Google 401'd a token our own expiry check still thought was
            # valid (revoked key, clock skew, key rotation) -- without this,
            # every poll until expires_at naturally lapses would keep
            # handing out the same stale cached token and 401 again, a
            # silent stall rather than a retry.
            _service_account_token = {}
            new_token = read_google_calendar_token()
            if new_token:
                payload, _ = poll_calendar(new_token, override_ids)
        else:
            client = _read_google_client()
            tok = _read_google_token()
            if client and tok:
                new_token = _refresh_google_token(tok, client)
                if new_token:
                    payload, _ = poll_calendar(new_token, override_ids)
    if payload is not None:
        calendar_state.set_payload(payload)
        if payload.get("events"):
            first = payload["events"][0]
            log(f"Calendar: {len(payload['events'])} upcoming, next = {first['summary']!r} at {first['start']}")
        # Wake the push loop immediately instead of leaving it to find this
        # on its own clock -- without this, a daemon restart races the
        # push loop's first iteration (payload not ready yet, skips, then
        # waits out the full calendar_push_interval) against the poller's
        # first successful fetch a few seconds later. Real symptom hit
        # live: real data landed, but the device kept showing a stale
        # synthetic test push for the better part of 5 minutes because
        # nothing told the push loop new data had arrived.
        calendar_state.push_kick_event.set()


def calendar_poller_loop(interval: float, static_ids: list[str] | None,
                          config_url: str | None) -> None:
    if config_url:
        seed_device_calendar_ids(config_url, static_ids)
    if _read_google_service_account() and not static_ids and not config_url:
        # A service account has no "My calendars" sidebar, so the selected=true
        # auto-detect in fetch_active_calendars() finds nothing for it -- this
        # would otherwise silently poll to {"ok": true, "events": []} forever,
        # looking like success. Explicit ids are required for this auth method
        # (a calendar's address, e.g. a Gmail address), after sharing that
        # calendar with the service account's client_email -- either via
        # --calendar-id, or via the device's own Agenda tab (config_url reads
        # that live each poll, see read_device_calendar_ids()).
        log("Calendar: service account in use with no --calendar-id and no "
            "push target to read device-configured ids from - auto-detect "
            "needs Google's 'selected calendars' list, which service "
            "accounts don't have. Set --calendar-id, or set the Calendar "
            "ID(s) field in the device's Agenda & weather tab; see "
            "clawdmeter-daemon/README.md.")
    log(f"Polling Google Calendar every {interval:.0f}s "
        f"(static ids={','.join(static_ids) if static_ids else 'none'}, "
        f"{'device ids from ' + config_url if config_url else 'no device config source'})")
    while not calendar_state.stop_event.is_set():
        try:
            do_calendar_poll(static_ids, config_url)
        except Exception as e:
            # Backstop against an unexpected exception killing this daemon
            # thread permanently -- see poller_loop()'s comment for why.
            log(f"Calendar poll: unexpected error, will retry next interval: {e}")
        calendar_state.refresh_event.wait(interval)
        calendar_state.refresh_event.clear()


def calendar_push_loop(stop: threading.Event, targets_fn, interval: float) -> None:
    """Same shape as push_loop() but for the calendar payload, pushed to
    /api/calendar on each device instead of /api/usage. Independent thread —
    calendar push stays on regardless of which primary Transport (serial/push/
    serve) is selected for the usage payload, since it's a separate on/off
    feature, not a transport choice."""
    log(f"HTTP calendar push every {interval:.0f}s")
    while not stop.is_set():
        payload = calendar_state.get_payload()
        urls = targets_fn()
        if payload.get("ok") and urls:
            for url in urls:
                cal_url = _resolve_url_for_push(url.replace("/api/usage", "/api/calendar"))
                try:
                    r = httpx.post(cal_url, json=payload, timeout=10.0, headers=_push_headers())
                    if r.status_code >= 400:
                        log(f"Calendar push {cal_url} HTTP {r.status_code}")
                except httpx.HTTPError as e:
                    log(f"Calendar push {cal_url} failed: {e}")
        calendar_state.push_kick_event.wait(interval)
        calendar_state.push_kick_event.clear()


# ---- Weather + air quality: polling ----------------------------------------
# Moved here from a device-direct Open-Meteo fetch -- that path was hard to
# debug on the ESP8266 (no serial console in this project's workflow, opaque
# TLS/HTTP failures with no logging). The daemon has real logging and can be
# iterated on quickly, so it fetches Open-Meteo and pushes the result instead.

def _device_config_url(push_url: str) -> str:
    """Turn a device's /api/usage or /api/calendar push URL into its /api/config URL."""
    for suffix in ("/api/usage", "/api/calendar", "/api/weather"):
        if push_url.endswith(suffix):
            return push_url[: -len(suffix)] + "/api/config"
    return push_url.rstrip("/") + "/api/config"


def read_device_location(config_url: str) -> tuple[float, float] | None:
    """Read the lat/lon the user set in the device's own Calendar tab, so
    weather location is configured in exactly one place (the device's web UI),
    not duplicated into daemon flags/config."""
    try:
        resp = httpx.get(config_url, timeout=10.0)
        resp.raise_for_status()
        cal = resp.json().get("calendar", {})
        lat, lon = float(cal.get("lat", 0)), float(cal.get("lon", 0))
    except (httpx.HTTPError, ValueError, TypeError) as e:
        log(f"Weather: couldn't read location from {config_url}: {e}")
        return None
    if lat == 0.0 and lon == 0.0:
        return None
    return lat, lon


def read_device_calendar_ids(config_url: str) -> list[str] | None:
    """Read the calendar ID(s) the user set in the device's own Agenda tab, so
    a newly-shared calendar gets picked up on the next poll with no daemon
    restart. Same reasoning as read_device_location() above (one place to
    configure, not duplicated into daemon flags), but calendar IDs specifically
    exist here because Google's Calendar API gives a service account no way to
    discover which calendars have been shared with it -- CalendarList only
    reflects entries a user has explicitly added, never automatically reflects
    ACL grants (confirmed against Google's own API docs, and there's a filed
    Google issue about exactly this gap: issuetracker.google.com/issues/148804709).
    The human has to supply the ID somewhere; this lets that "somewhere" be
    edited on the device without touching the daemon at all."""
    try:
        resp = httpx.get(config_url, timeout=10.0)
        resp.raise_for_status()
        cal = resp.json().get("calendar", {})
        raw = str(cal.get("ids", "") or "")
    except (httpx.HTTPError, ValueError, TypeError) as e:
        log(f"Calendar: couldn't read ids from {config_url}: {e}")
        return None
    ids = [c.strip() for c in raw.split(",") if c.strip()]
    return ids or None


def seed_device_calendar_ids(config_url: str, static_ids: list[str] | None) -> None:
    """One-time, at poller startup only (never per-poll -- see the call site):
    if the device's own Calendar ID(s) field is empty and this daemon has a
    real static --calendar-id list, push that list onto the device once, so
    the web UI shows what's actually active instead of a blank field that
    read as broken ("it there but it's not showing the current ones").
    Deliberately one-way and one-shot: once seeded (or if the field already
    had something in it), this never runs again for the life of this
    process, so it can't fight a user who later edits or clears the field --
    read_device_calendar_ids() reading the device fresh every poll is what
    makes edits "take" without a restart; this function only ever primes an
    empty box, never overwrites a non-empty one."""
    if not static_ids:
        return
    try:
        current = read_device_calendar_ids(config_url)
    except Exception:
        current = None
    if current:
        return   # device already has something -- device wins, don't touch it
    try:
        body = json.dumps({"calendar": {"ids": ",".join(static_ids)}})
        resp = httpx.post(config_url, content=body,
                           headers={"Content-Type": "application/json"}, timeout=10.0)
        resp.raise_for_status()
        log(f"Calendar: seeded device's empty Calendar ID(s) field with "
            f"{len(static_ids)} static id(s) from --calendar-id ({config_url})")
    except httpx.HTTPError as e:
        log(f"Calendar: couldn't seed device ids at {config_url}: {e}")


_city_cache: dict[tuple[float, float], str | None] = {}


def reverse_geocode_city(lat: float, lon: float) -> str | None:
    """City name for the weather page's location line. Cached per rounded
    lat/lon (2 decimals, ~1km) since the device's location rarely changes --
    no point re-geocoding every poll. Same free, no-key API the web UI's
    "Use my location" button already calls from the browser."""
    key = (round(lat, 2), round(lon, 2))
    if key in _city_cache:
        return _city_cache[key]
    try:
        r = httpx.get("https://api.bigdatacloud.net/data/reverse-geocode-client",
                       params={"latitude": lat, "longitude": lon, "localityLanguage": "en"},
                       timeout=10.0, follow_redirects=True)
        r.raise_for_status()
        d = r.json()
        # "locality" is the plain city name ("Chiang Mai"); "city" from this
        # API is often the containing district/amphoe ("Amphoe Mueang Chiang
        # Mai") -- verified against a real response before picking the order.
        city = d.get("locality") or d.get("city") or None
    except (httpx.HTTPError, ValueError) as e:
        log(f"Weather: reverse-geocode failed: {e}")
        return None   # don't cache a transient failure -- retry next poll
    if city:
        _city_cache[key] = city
    return city


def poll_weather(lat: float, lon: float) -> dict:
    """Fetch forecast + air-quality from Open-Meteo (two independent hosts,
    no API key). Each half is independently optional -- a temp-only result
    with no AQI (or vice versa) is a normal degraded state, not an error."""
    payload: dict = {"ok": True}
    # ISO dates for each payload["forecast"] entry, same index alignment,
    # kept out of the payload itself (device only gets the "day" label) --
    # used below to key into the AQI aggregation without re-deriving or
    # string-matching a date from the human-readable label.
    forecast_dates: list = []
    try:
        r = httpx.get(OPEN_METEO_FORECAST_URL, params={
            "latitude": lat, "longitude": lon,
            "current": "temperature_2m,precipitation_probability,weather_code,uv_index",
            # 3-day forecast, added to this same request (separate response
            # key "daily", doesn't touch the "current" parsing above at
            # all). timezone=auto so "daily" buckets are local calendar
            # days, not UTC ones -- confirmed live this matters (Chiang
            # Mai is UTC+7, a UTC-day bucket would span the wrong window).
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "auto",
            "forecast_days": 4,
        }, timeout=15.0)
        r.raise_for_status()
        body = r.json()
        cur = body.get("current", {})
        if "temperature_2m" in cur: payload["tempC"] = cur["temperature_2m"]
        if "precipitation_probability" in cur: payload["precipPct"] = cur["precipitation_probability"]
        if "weather_code" in cur: payload["weatherCode"] = cur["weather_code"]
        if "uv_index" in cur: payload["uvIndex"] = cur["uv_index"]

        # daily["time"][0] is today (same local day as `current`, confirmed
        # live with forecast_days=4 + timezone=auto) -- start at index 1 so
        # the forecast page doesn't just repeat what the rest of this page
        # already shows for today.
        daily = body.get("daily", {})
        dtimes = daily.get("time", [])
        dcode = daily.get("weather_code", [])
        dhi = daily.get("temperature_2m_max", [])
        dlo = daily.get("temperature_2m_min", [])
        dprecip = daily.get("precipitation_probability_max", [])
        # "Tmr" for the first day, else a fixed-English 3-letter weekday
        # abbreviation from date.weekday() (0=Monday), not strftime("%a")
        # -- that's locale-dependent on whatever locale the daemon's host
        # happens to have configured, and this string ends up rendered
        # as-is on the device with no i18n path there. Kept to a uniform
        # 3 chars (not "Tomorrow") so it fits either device-side layout
        # (wide row or narrow column) without a device-side truncation
        # path of its own.
        WEEKDAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        forecast = []
        for i in range(1, min(4, len(dtimes))):
            if i == 1:
                label = "Tmr"
            else:
                label = WEEKDAY_ABBR[datetime.strptime(dtimes[i], "%Y-%m-%d").weekday()]
            day = {"day": label}
            if i < len(dcode): day["code"] = dcode[i]
            if i < len(dhi): day["hi"] = round(dhi[i])
            if i < len(dlo): day["lo"] = round(dlo[i])
            if i < len(dprecip): day["precip"] = dprecip[i]
            forecast.append(day)
            forecast_dates.append(dtimes[i])
        if forecast:
            payload["forecast"] = forecast
    except (httpx.HTTPError, ValueError) as e:
        log(f"Weather: forecast fetch failed: {e}")

    try:
        # us_aqi comes from `current` (reliable there), but pm2_5 is
        # fetched via `hourly` and matched to the current hour ourselves --
        # confirmed live that `current.pm2_5` and the matching
        # `hourly.pm2_5[idx]` return the identical value, but the hourly
        # array is the more robust path (current-block field coverage can
        # vary). No `timezone` param -- confirmed live it then defaults to
        # plain UTC/GMT for both `current.time` and every `hourly.time`
        # entry, so scaling our own real time (`datetime.now(timezone.utc)`)
        # to that same UTC hour finds the right index without trusting the
        # API's own `current.time` field at all -- one less thing that
        # could silently mismatch if Open-Meteo ever changes what that
        # field reports.
        r = httpx.get(OPEN_METEO_AQ_URL, params={
            "latitude": lat, "longitude": lon,
            "hourly": "pm10,pm2_5", "current": "us_aqi",
        }, timeout=15.0)
        r.raise_for_status()
        data = r.json()
        cur = data.get("current", {})
        if "us_aqi" in cur: payload["aqi"] = cur["us_aqi"]

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        pm25s = hourly.get("pm2_5", [])
        now_hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00")
        if now_hour in times:
            idx = times.index(now_hour)
            if idx < len(pm25s) and pm25s[idx] is not None:
                pm25_val = pm25s[idx]
                payload["pm25"] = pm25_val
                # Real-time AQI computed from THIS hour's PM2.5 reading, not
                # Open-Meteo's own us_aqi (a 24h rolling average -- confirmed
                # live it stays elevated for hours after a real spike has
                # already passed, e.g. 6.5 ug/m3 instant reading still
                # showing us_aqi=50 from an afternoon spike 12+ hours
                # earlier). This is meant to warn immediately on a fresh
                # spike, not wait a day. python-aqi's EPA algorithm uses the
                # classic pre-2024 breakpoint table (12.0 ug/m3 -> AQI 50,
                # confirmed live), not the 2024-revised one -- disclosed,
                # not silently assumed current. Clamp to the library's own
                # valid PM2.5 range (0-500.4, confirmed by reading its
                # source) since to_iaqi() raises IndexError outside it, and
                # pm25_val is an unvalidated upstream value like everything
                # else in this function.
                try:
                    clamped = max(0.0, min(500.4, float(pm25_val)))
                    payload["aqiNow"] = int(pyaqi.to_iaqi(pyaqi.POLLUTANT_PM25, str(clamped), algo=pyaqi.ALGO_EPA))
                except (IndexError, InvalidOperation, ValueError, TypeError) as e:
                    log(f"Weather: aqiNow computation failed: {e}")
    except (httpx.HTTPError, ValueError) as e:
        log(f"Weather: air-quality fetch failed: {e}")

    try:
        # Separate request from the instant-AQI one above, deliberately not
        # merged into it: that call's now_hour index matching depends on
        # its hourly array staying plain UTC (no timezone param, see the
        # comment above) -- adding timezone=auto there to get local day
        # buckets would shift every hourly timestamp and break that
        # already-verified matching logic. Cheap to just ask twice.
        # No `daily=` support on this endpoint (confirmed live -- it 400s
        # on daily=pm2_5_max), so daily max is aggregated from `hourly`
        # here instead.
        r = httpx.get(OPEN_METEO_AQ_URL, params={
            "latitude": lat, "longitude": lon,
            "hourly": "pm2_5", "timezone": "auto", "forecast_days": 4,
        }, timeout=15.0)
        r.raise_for_status()
        hourly = r.json().get("hourly", {})
        times = hourly.get("time", [])
        pm25s = hourly.get("pm2_5", [])
        daily_max = {}
        for t, v in zip(times, pm25s):
            if v is None:
                continue
            d = t[:10]
            if d not in daily_max or v > daily_max[d]:
                daily_max[d] = v

        # Merge into the "forecast" entries built above, matched by index
        # via forecast_dates (payload["forecast"][i] <-> forecast_dates[i],
        # same construction loop) rather than by string-matching a date out
        # of "day" -- that field is now a human label ("Tmr"/"Mon"), not a
        # date, so it can't be matched back to daily_max's real ISO keys
        # at all. If the forecast block above didn't run (its own fetch
        # failed), forecast_dates is empty and this loop is a no-op.
        if "forecast" in payload:
            for day, iso_date in zip(payload["forecast"], forecast_dates):
                if iso_date not in daily_max:
                    continue
                try:
                    clamped = max(0.0, min(500.4, float(daily_max[iso_date])))
                    day["aqi"] = int(pyaqi.to_iaqi(pyaqi.POLLUTANT_PM25, str(clamped), algo=pyaqi.ALGO_EPA))
                except (IndexError, InvalidOperation, ValueError, TypeError) as e:
                    log(f"Weather: forecast aqi computation failed for {iso_date}: {e}")
    except (httpx.HTTPError, ValueError) as e:
        log(f"Weather: air-quality forecast fetch failed: {e}")

    # ok stays True only if at least one field actually landed -- State's
    # keep-last-good logic only preserves the previous payload when the new
    # one's "ok" is falsy, so a total-failure poll must not claim ok:True.
    payload["ok"] = len(payload) > 1
    return payload


def do_weather_poll(config_url: str) -> None:
    loc = read_device_location(config_url)
    if loc is None:
        weather_state.set_payload({"ok": False})
        return
    lat, lon = loc
    payload = poll_weather(lat, lon)
    city = reverse_geocode_city(lat, lon)
    if city:
        payload["city"] = city
    weather_state.set_payload(payload)
    if len(payload) > 1:   # more than just "ok"
        log(f"Weather: {payload}")


def weather_poller_loop(interval: float, config_url: str) -> None:
    log(f"Polling weather/AQI every {interval:.0f}s (location from {config_url})")
    while not weather_state.stop_event.is_set():
        try:
            do_weather_poll(config_url)
        except Exception as e:
            log(f"Weather poll: unexpected error, will retry next interval: {e}")
        weather_state.refresh_event.wait(interval)
        weather_state.refresh_event.clear()


def weather_push_loop(stop: threading.Event, targets_fn, interval: float) -> None:
    """Same shape as calendar_push_loop() but for the weather payload, pushed
    to /api/weather on each device."""
    log(f"HTTP weather push every {interval:.0f}s")
    while not stop.is_set():
        payload = weather_state.get_payload()
        urls = targets_fn()
        if payload.get("ok") and len(payload) > 1 and urls:
            for url in urls:
                wx_url = _resolve_url_for_push(url.replace("/api/usage", "/api/weather"))
                try:
                    r = httpx.post(wx_url, json=payload, timeout=10.0, headers=_push_headers())
                    if r.status_code >= 400:
                        log(f"Weather push {wx_url} HTTP {r.status_code}")
                except httpx.HTTPError as e:
                    log(f"Weather push {wx_url} failed: {e}")
        weather_state.push_kick_event.wait(interval)
        weather_state.push_kick_event.clear()


def poll_zai(api_key: str) -> dict:
    """Fetch z.ai's quota endpoint. UNDOCUMENTED by z.ai (found via a
    community reverse-engineered plugin, confirmed live against a real
    account -- see CLAUDE.md's z.ai research notes) -- parsed defensively,
    since the response shape could change with no notice. Auth header has
    no "Bearer" prefix; confirmed working as-is, don't "fix" it.

    IMPORTANT, corrected TWICE after wrong assumptions -- read this before
    touching the type->field mapping again:
    1st wrong assumption: TIME_LIMIT was assumed to be the rolling 5-hour
    quota (matching Claude's "5h" card) just because it's named "TIME_LIMIT"
    and appeared first/percentage=0. Wrong -- z.ai's own field names don't
    match what they sound like.
    2nd (corrected) mapping: a reverse-engineered plugin's docs describe
    "5-hour token cycle, weekly quota, monthly MCP usage". Live data showed
    TOKENS_LIMIT resets in ~3.5h (fits a 5h cycle) and TIME_LIMIT resets in
    ~7d (looked like it might fit weekly) -- so TOKENS_LIMIT was mapped to
    the 5h card and TIME_LIMIT to a "weekly" card. Still wrong, caught by
    an advisor review before flashing: TIME_LIMIT's own "usage" field (its
    cap) was 100, which exactly matches z.ai Lite's published MONTHLY MCP
    tools allowance (100 web-search+web-reader calls/month), not its
    weekly prompt allowance (~400/week for Lite). TIME_LIMIT's
    usageDetails also literally lists the three MCP tool names
    (search-prime/web-reader/zread) -- it's the monthly MCP bucket, not a
    time-window-duration field at all. The ~7-day reading was just where
    this account happened to be in its monthly cycle, not evidence of a
    weekly reset.
    Current (believed correct) mapping: pct5h/r5h <- TOKENS_LIMIT (the real
    5h cycle), pctMcp/rMcp <- TIME_LIMIT (the monthly MCP-tools quota).
    Neither matches z.ai's own type-string naming -- don't "simplify" this
    back to a 1:1 mapping without re-deriving the evidence above."""
    payload: dict = {"ok": True}
    try:
        r = httpx.get(ZAI_QUOTA_URL, headers={
            "Authorization": api_key,
            "Accept-Language": "en-US,en",
            "Content-Type": "application/json",
        }, timeout=15.0)
        r.raise_for_status()
        limits = r.json().get("data", {}).get("limits", [])
        if not isinstance(limits, list):
            return {"ok": False}
        now_ms = time.time() * 1000.0

        def reset_minutes(next_reset_ms):
            # nextResetTime is epoch MILLISECONDS (confirmed live against
            # the real endpoint), unlike Claude's header-based reset which
            # is epoch seconds -- don't reuse that conversion here.
            if not isinstance(next_reset_ms, (int, float)):
                return None
            mins = (next_reset_ms - now_ms) / 60000.0
            return int(round(mins)) if mins > 0 else 0

        for entry in limits:
            if not isinstance(entry, dict):
                continue
            pct = entry.get("percentage")
            if not isinstance(pct, (int, float)):
                continue
            rmins = reset_minutes(entry.get("nextResetTime"))
            if entry.get("type") == "TOKENS_LIMIT":   # the real 5-hour cycle
                payload["pct5h"] = round(pct)
                if rmins is not None:
                    payload["r5h"] = rmins
            elif entry.get("type") == "TIME_LIMIT":   # the real monthly MCP-tools quota
                payload["pctMcp"] = round(pct)
                if rmins is not None:
                    payload["rMcp"] = rmins
    except (httpx.HTTPError, ValueError) as e:
        log(f"Z.AI: quota fetch failed: {e}")

    # Same "ok only if something actually landed" rule as poll_weather() --
    # State's keep-last-good logic only preserves the previous payload when
    # the new one's ok is falsy.
    payload["ok"] = len(payload) > 1
    return payload


def do_zai_poll(api_key: str) -> None:
    payload = poll_zai(api_key)
    zai_state.set_payload(payload)
    if len(payload) > 1:
        log(f"Z.AI: {payload}")


def zai_poller_loop(interval: float, api_key: str) -> None:
    log(f"Polling z.ai quota every {interval:.0f}s")
    while not zai_state.stop_event.is_set():
        try:
            do_zai_poll(api_key)
        except Exception as e:
            log(f"Z.AI poll: unexpected error, will retry next interval: {e}")
        zai_state.refresh_event.wait(interval)
        zai_state.refresh_event.clear()


def zai_push_loop(stop: threading.Event, targets_fn, interval: float) -> None:
    """Same shape as weather_push_loop() but for the z.ai payload, pushed to
    /api/zai on each device."""
    log(f"HTTP z.ai push every {interval:.0f}s")
    while not stop.is_set():
        payload = zai_state.get_payload()
        urls = targets_fn()
        if payload.get("ok") and len(payload) > 1 and urls:
            for url in urls:
                zai_url = _resolve_url_for_push(url.replace("/api/usage", "/api/zai"))
                try:
                    r = httpx.post(zai_url, json=payload, timeout=10.0, headers=_push_headers())
                    if r.status_code >= 400:
                        log(f"Z.AI push {zai_url} HTTP {r.status_code}")
                except httpx.HTTPError as e:
                    log(f"Z.AI push {zai_url} failed: {e}")
        zai_state.push_kick_event.wait(interval)
        zai_state.push_kick_event.clear()


def poll_openrouter(api_key: str) -> dict:
    """Fetch OpenRouter key usage/spend. Plain authenticated GET, no OAuth
    dance and no model call; parsed defensively because only the few
    device-facing spend fields are needed here."""
    payload: dict = {"ok": True}
    try:
        r = httpx.get(OPENROUTER_KEY_URL, headers={
            "Authorization": f"Bearer {api_key}",
        }, timeout=15.0)
        r.raise_for_status()
        data = r.json().get("data", {})
        if not isinstance(data, dict):
            return {"ok": False}

        usd_daily = data.get("usage_daily")
        if isinstance(usd_daily, (int, float)):
            payload["usd_daily"] = usd_daily
        usd_weekly = data.get("usage_weekly")
        if isinstance(usd_weekly, (int, float)):
            payload["usd_weekly"] = usd_weekly
        usd_total = data.get("usage")
        if isinstance(usd_total, (int, float)):
            payload["usd_total"] = usd_total
        free_tier = data.get("is_free_tier")
        if isinstance(free_tier, bool):
            payload["free_tier"] = free_tier
    except (httpx.HTTPError, ValueError) as e:
        log(f"OpenRouter: key fetch failed: {e}")

    payload["ok"] = len(payload) > 1
    return payload


def do_openrouter_poll(api_key: str) -> None:
    payload = poll_openrouter(api_key)
    openrouter_state.set_payload(payload)
    if len(payload) > 1:
        log(f"OpenRouter: {payload}")


def openrouter_poller_loop(interval: float, api_key: str) -> None:
    log(f"Polling OpenRouter spend every {interval:.0f}s")
    while not openrouter_state.stop_event.is_set():
        try:
            do_openrouter_poll(api_key)
        except Exception as e:
            log(f"OpenRouter poll: unexpected error, will retry next interval: {e}")
        openrouter_state.refresh_event.wait(interval)
        openrouter_state.refresh_event.clear()


def openrouter_push_loop(stop: threading.Event, targets_fn, interval: float) -> None:
    """Same shape as antigravity_push_loop() but for the OpenRouter payload,
    pushed to /api/openrouter on each device."""
    log(f"HTTP OpenRouter push every {interval:.0f}s")
    while not stop.is_set():
        payload = openrouter_state.get_payload()
        urls = targets_fn()
        if payload.get("ok") and len(payload) > 1 and urls:
            for url in urls:
                or_url = _resolve_url_for_push(url.replace("/api/usage", "/api/openrouter"))
                try:
                    r = httpx.post(or_url, json=payload, timeout=10.0, headers=_push_headers())
                    if r.status_code >= 400:
                        log(f"OpenRouter push {or_url} HTTP {r.status_code}")
                except httpx.HTTPError as e:
                    log(f"OpenRouter push {or_url} failed: {e}")
        openrouter_state.push_kick_event.wait(interval)
        openrouter_state.push_kick_event.clear()


def _codex_rpc_call() -> dict | None:
    """Query Codex CLI's real ChatGPT-plan rate-limit quota via `codex
    app-server`'s JSON-RPC `account/rateLimits/read` method over stdio
    (newline-delimited JSON, no Content-Length framing). Confirmed live
    against a real account: this spins up a local subprocess, which makes
    a genuine live `GET https://chatgpt.com/backend-api/wham/usage` call
    (seen directly in a 401 error's own text during troubleshooting) --
    NOT a read of some local cache, so this is never stale the way a
    passive local-file read would be. It does NOT send a prompt/fire a
    completion (that's what makes it free), round-trips in ~1-2s either
    way. This replaced an earlier design that grepped the most recently
    modified ~/.codex/sessions/*/*/*/*.jsonl rollout file for a
    `rate_limits` block written by real usage -- that passive design only
    updated when the daemon's own machine happened to run `codex`
    interactively (useless on a headless/server deployment that never
    does); the RPC's live call fixes that, working the same everywhere
    regardless of local usage. Old passive/active-ping split
    (--codex-ping) no longer applies -- there's only one mode now, and
    it's the always-fresh one. `codex login` must already be done on
    whichever machine actually runs this daemon; the quota itself is
    account-wide, so it doesn't matter whether Codex is ever actually used
    on that machine, only that it's authenticated there.

    Returns the RPC's full result dict: {"rateLimits": {...},
    "rateLimitsByLimitId": {...}, "rateLimitResetCredits": {...}|null}.
    Confirmed live this account's `rateLimitsByLimitId` has exactly one
    bucket ("codex") mirroring the top-level `rateLimits` -- not a second
    window, so poll_codex() only reads `rateLimits` for the percentages.
    `rateLimitResetCredits` is separate: occasional free "full rate-limit
    reset" credits, found by reading the raw response (undocumented)."""
    codex_bin = shutil.which("codex")
    if not codex_bin:
        # A systemd --user service (pi5's actual deployment) runs with a
        # minimal PATH -- confirmed this daemon's own service environment
        # may not include wherever `codex` actually lives (e.g. a Node
        # global bin or ~/.local/bin), even though an interactive SSH login
        # shell resolves it fine. Fail loud here rather than a bare OSError,
        # since that failure mode looks like "feature doesn't work" instead
        # of "PATH" otherwise.
        log("Codex: `codex` not found on PATH - check this daemon's actual "
            "runtime PATH (a systemd --user service's PATH can differ from "
            "an interactive login shell's)")
        return None
    try:
        p = subprocess.Popen(
            [codex_bin, "-s", "read-only", "-a", "never", "app-server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
        )
    except OSError as e:
        log(f"Codex: failed to start app-server: {e}")
        return None

    # Watchdog, not a blocking-read timeout -- `for line in p.stdout` has no
    # native timeout and select() on a pipe isn't portable to Windows (this
    # daemon runs there too, per README). A kill() after N seconds forces
    # stdout to EOF, which unblocks the read loop below either way.
    killer = threading.Timer(10.0, p.kill)
    killer.start()
    # Returns the FULL RPC result dict now, not just result["rateLimits"] --
    # confirmed live the response also carries `rateLimitResetCredits`
    # (free "full reset" credits, undocumented, found by reading the raw
    # response rather than any spec) alongside the rate limits themselves.
    # poll_codex() picks apart both from this one dict.
    result = None
    try:
        p.stdin.write(json.dumps({
            "id": 1, "method": "initialize",
            "params": {"clientInfo": {"name": "clawdmeter", "version": "1.0.0"}},
        }) + "\n")
        p.stdin.write(json.dumps({"method": "initialized", "params": {}}) + "\n")
        p.stdin.write(json.dumps({"id": 2, "method": "account/rateLimits/read", "params": {}}) + "\n")
        p.stdin.flush()

        for line in p.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == 2:
                r = msg.get("result")
                if isinstance(r, dict):
                    result = r
                break
    except (OSError, BrokenPipeError) as e:
        log(f"Codex: RPC I/O failed: {e}")
    finally:
        killer.cancel()
        p.terminate()
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()

    return result


def poll_codex() -> dict:
    """Codex CLI's real ChatGPT-plan rate-limit quota -- NOT an OpenAI API
    key, no billing involved. See _codex_rpc_call() for the mechanism.

    Confirmed live against a real account: `primary` carries a weekly
    window (windowDurationMins=10080) at this account's plan tier ("plus");
    `secondary` (a shorter window, e.g. 5h, on some plans/tiers) was null
    on this account -- both parsed defensively since neither is
    documented.

    Also surfaces `rateLimitResetCredits` -- free "full reset" credits
    found live in the same RPC response, undocumented. The point of
    showing this on the device is to use a credit before it expires
    unused, so resetCreditExpireMins is a countdown to the SOONEST
    expiring *available* credit, not just a raw count."""
    result = _codex_rpc_call()
    if not result:
        log("Codex: no rate limits returned - is `codex login` done on this machine?")
        return {"ok": False}
    rate_limits = result.get("rateLimits")
    if not rate_limits:
        log("Codex: RPC returned no rateLimits block")
        return {"ok": False}

    now = time.time()

    def window_pct(entry):
        if not isinstance(entry, dict):
            return None
        pct = entry.get("usedPercent")
        if not isinstance(pct, (int, float)):
            return None
        return int(round(max(0, min(100, pct))))

    def window_reset_minutes(entry):
        if not isinstance(entry, dict):
            return None
        ts = entry.get("resetsAt")
        if not isinstance(ts, (int, float)):
            return None
        mins = (ts - now) / 60.0
        return int(round(mins)) if mins > 0 else 0

    def window_minutes(entry):
        if not isinstance(entry, dict):
            return None
        wm = entry.get("windowDurationMins")
        return wm if isinstance(wm, (int, float)) else None

    primary, secondary = rate_limits.get("primary"), rate_limits.get("secondary")
    payload: dict = {"ok": True}

    # Classify each entry independently by its own windowDurationMins (< 1
    # day = the "5h" card, else the "Week" card) rather than by sort
    # position -- confirmed live this matters: with only `primary` present
    # (secondary null on this account/tier) and windowDurationMins=10080 (a
    # real 7-day window), a positional "shorter of the two -> 5h slot" rule
    # would wrongly force a weekly quota into the 5h card just because it's
    # the only entry. Falls back to the primary=week/secondary=5h
    # convention (matching every real response seen so far) only when
    # windowDurationMins itself is absent.
    DAY_MINUTES = 1440
    short_entry = long_entry = None
    for entry, is_primary in ((primary, True), (secondary, False)):
        if entry is None:
            continue
        wm = window_minutes(entry)
        if wm is not None:
            is_short = wm < DAY_MINUTES
        else:
            is_short = not is_primary
        if is_short:
            short_entry = entry
        else:
            long_entry = entry

    pct5h = window_pct(short_entry)
    if pct5h is not None:
        payload["pct5h"] = pct5h
        r5h = window_reset_minutes(short_entry)
        if r5h is not None:
            payload["r5h"] = r5h
    pctWeek = window_pct(long_entry)
    if pctWeek is not None:
        payload["pctWeek"] = pctWeek
        rWeek = window_reset_minutes(long_entry)
        if rWeek is not None:
            payload["rWeek"] = rWeek

    # Free rate-limit reset credits -- separate from the primary/secondary
    # windows above, found in `rateLimitResetCredits`. Only count credits
    # with status "available" (a used/expired one shouldn't count toward
    # "you have N left"), and track the soonest `expiresAt` among those --
    # that's the number worth watching, not an arbitrary one.
    reset_credits = result.get("rateLimitResetCredits")
    if isinstance(reset_credits, dict):
        available = [
            c for c in (reset_credits.get("credits") or [])
            if isinstance(c, dict) and c.get("status") == "available"
        ]
        # Prefer the API's own availableCount over len(available) -- they
        # agree on this account today, but if the backend ever changes
        # what "available" means, its own count is authoritative. The
        # filtered list is still needed below for the expiry.
        count = reset_credits.get("availableCount")
        payload["resetCredits"] = count if isinstance(count, int) else len(available)

        # Soonest-expiring available credit's expiresAt -- the device
        # computes its own urgency bar from this (against a fixed 7-day
        # window, per live feedback: "the bottom bar for the reset it
        # shud be devided in 7 days... green is good, red is almost
        # expire"), not from the credit's actual granted-to-expiry
        # lifespan (an earlier version computed % elapsed of that here,
        # which could span ~30 days on this account -- not the intuitive
        # "7 days" scale the user described). No grantedAt needed here
        # anymore.
        expiries = [c["expiresAt"] for c in available if isinstance(c.get("expiresAt"), (int, float))]
        if expiries:
            mins = (min(expiries) - now) / 60.0
            payload["resetCreditExpireMins"] = int(round(mins)) if mins > 0 else 0

    payload["ok"] = len(payload) > 1
    return payload


def do_codex_poll() -> None:
    payload = poll_codex()
    codex_state.set_payload(payload)
    if len(payload) > 1:
        log(f"Codex: {payload}")


def codex_poller_loop(interval: float) -> None:
    log(f"Polling Codex quota every {interval:.0f}s (app-server RPC, zero cost)")
    while not codex_state.stop_event.is_set():
        try:
            do_codex_poll()
        except Exception as e:
            log(f"Codex poll: unexpected error, will retry next interval: {e}")
        codex_state.refresh_event.wait(interval)
        codex_state.refresh_event.clear()


def codex_push_loop(stop: threading.Event, targets_fn, interval: float) -> None:
    """Same shape as zai_push_loop() but for the Codex payload, pushed to
    /api/codex on each device."""
    log(f"HTTP Codex push every {interval:.0f}s")
    while not stop.is_set():
        payload = codex_state.get_payload()
        urls = targets_fn()
        if payload.get("ok") and len(payload) > 1 and urls:
            for url in urls:
                codex_url = _resolve_url_for_push(url.replace("/api/usage", "/api/codex"))
                try:
                    r = httpx.post(codex_url, json=payload, timeout=10.0, headers=_push_headers())
                    if r.status_code >= 400:
                        log(f"Codex push {codex_url} HTTP {r.status_code}")
                except httpx.HTTPError as e:
                    log(f"Codex push {codex_url} failed: {e}")
        codex_state.push_kick_event.wait(interval)
        codex_state.push_kick_event.clear()


ANTIGRAVITY_PROMPT_MODEL = "gemini-3.6-flash-low"  # cheapest model on this plan's list


def _antigravity_find_ports(pid: int, deadline: float) -> list[int]:
    """Poll `lsof` for listening TCP ports on the given PID until at least
    one shows up or the deadline passes. `agy` binds TWO ports (confirmed
    live: one HTTPS/gRPC, one plain HTTP) -- returns all of them, not just
    the first `lsof` line, since lsof's line order isn't guaranteed and a
    caller that only tries the first one can silently hit the wrong port
    every time."""
    lsof_bin = shutil.which("lsof")
    if not lsof_bin:
        log("Antigravity: `lsof` not found on PATH - required for local port discovery")
        return []
    while time.time() < deadline:
        try:
            r = subprocess.run(
                [lsof_bin, "-nP", "-iTCP", "-sTCP:LISTEN", "-a", "-p", str(pid)],
                capture_output=True, text=True, timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        ports = [int(p) for p in re.findall(r":(\d+)\s+\(LISTEN\)", r.stdout)]
        if ports:
            return ports
        time.sleep(0.3)
    return []


def _antigravity_rpc_call_once(port: int) -> dict | None:
    """One GetUserStatus attempt, HTTPS then HTTP fallback. Returns the
    parsed body even on a same-process RPC-level error (e.g. cascade state
    not ready yet) so the caller can distinguish "not ready" from
    "unreachable" -- only a transport-level failure returns None here."""
    body = {"metadata": {"ideName": "antigravity", "extensionName": "antigravity",
                          "ideVersion": "unknown", "locale": "en"}}
    for scheme in ("https", "http"):
        try:
            r = httpx.post(
                f"{scheme}://127.0.0.1:{port}/exa.language_server_pb.LanguageServerService/GetUserStatus",
                json=body, timeout=5.0, verify=False,
            )
        except httpx.HTTPError:
            continue
        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                return None
        # A wrong-scheme guess (e.g. the plain-HTTP port answering the HTTPS
        # try) 400s immediately -- worth trying the other scheme/port, not
        # worth logging as a real failure.
    return None


def _antigravity_wait_for_status(pid: int, deadline: float) -> dict | None:
    """Find `agy`'s local ports and poll GetUserStatus against each until
    one returns real account data or the deadline passes. Confirmed live
    this needs retrying, not just port discovery: the ports start accepting
    connections (~1s) before the cascade session inside the process has
    finished initializing model config data -- querying too early gets a
    real same-process RPC error ("GetCascadeModelConfigData() is nil"), not
    a connection failure, so a single query right after a port appears is
    NOT reliable. Confirmed live one of the two ports is plain HTTP-only and
    answers an HTTPS probe with an immediate 400 / SSL error -- harmless,
    _antigravity_rpc_call_once() already falls back to the http:// scheme
    on the same port, but only the correct port (of the two) ever returns
    real data, hence trying all of them each round rather than caching the
    first one found."""
    ports: list[int] = []
    while time.time() < deadline:
        if not ports:
            ports = _antigravity_find_ports(pid, deadline)
            if not ports:
                return None
        for port in ports:
            result = _antigravity_rpc_call_once(port)
            if isinstance(result, dict) and "userStatus" in result:
                return result
        time.sleep(1.0)
    return None


def poll_antigravity(_retry: bool = True) -> dict:
    """Antigravity CLI (`agy`) quota. UNLIKE Codex, `agy`'s local server
    only reports real quota once a cascade/agent session has actually run
    -- confirmed live that a cheap no-op (`agy models`) leaves
    cascadeModelConfigData nil (`GetUserStatus` errors with
    "GetCascadeModelConfigData() is nil"), and the fallback
    `GetCommandModelConfigs` RPC is flatly unimplemented on this CLI
    version). So this fires a real, tiny prompt every poll
    (--model gemini-3.6-flash-low, the cheapest model on this plan's list)
    -- a genuine, real cost every poll, NOT free like Codex's RPC read.
    Kept to a long default interval (--antigravity-interval, default 1800s)
    specifically because of that cost.

    The daemon finds the spawned `agy` process's listening port via `lsof`
    (~1s in, confirmed live), then retries GetUserStatus against it every
    1s until real data comes back -- confirmed live the port accepts
    connections before the cascade session inside it finishes initializing
    model config data, so a single query right after the port appears can
    get a real same-process RPC error ("GetCascadeModelConfigData() is
    nil") rather than the account data, and needs a retry, not just a
    longer wait. The prompt is left to finish naturally afterward rather
    than killed early -- it's real usage either way, no reason to also
    throw away the answer.

    Quota metric: `userStatus.planStatus.availablePromptCredits`/
    `availableFlowCredits` were the FIRST thing tried and are WRONG --
    confirmed live these stay flat (500/100) across 5+ real prompts fired
    this session while testing, so they're some kind of wallet/purchasable
    credit balance (siblings of `canBuyMoreCredits`/
    `monthlyFlexCreditPurchaseAmount` in the response), not a consumption
    counter; dividing them by the unrelated static `monthlyPromptCredits`/
    `monthlyFlowCredits` plan-description fields produced a ~99%-used
    reading that was actually meaningless. The real, moving metric is each
    entry's own `quotaInfo.remainingFraction` under
    `cascadeModelConfigData.clientModelConfigs[]` -- confirmed live it
    dropped from 1 to 0.9826585 after real use.

    Two real numbers, not one: `agy models` on this account lists 11
    configs (confirmed live) -- gemini-3.6-flash-{high,medium,low},
    gemini-3.5-flash-{high,medium,low}, gemini-3.1-pro-{high,low}, plus
    non-Gemini models (claude-sonnet-4-6, claude-opus-4-6-thinking,
    gpt-oss-120b-medium) that aren't "Pro" or "Flash" tier at all. Per
    live feedback ("we will show pro and flash % and re[set] time"), this
    reports one entry per family (substring match on the response's own
    friendly `label`, e.g. "Gemini 3.6 Flash (Medium)", case-insensitive
    on "flash"/"pro" -- naturally excludes the three non-Gemini models,
    which contain neither substring).

    Selection within a family is version-first, not quota-first (per
    later live feedback, "let's show the latest model of pro and
    flash"): find the highest version number present in that family
    (this account's Flash family spans two generations, 3.5 and 3.6),
    then break ties among that version's own reasoning-tier variants by
    lowest `remainingFraction` -- see `latest_in_family()` below. A
    numerically tighter but OLDER-generation entry (e.g. 3.5 Flash at
    10% remaining) is deliberately NOT picked over a newer one (e.g. 3.6
    Flash at 80%) -- don't revert this to a pure "lowest fraction wins"
    global pick, that was tried first and explicitly walked back.

    The device-facing label ("3.6 Flash"/"3.1 Pro") is composed from a
    `\\d+\\.\\d+` version-number regex match plus the full family word,
    dropping the "(High/Medium/Low)" reasoning suffix entirely. This is
    wider than the device's shared header row has room for at a readable
    size next to the % value -- the device compensates by shrinking that
    page's value text, not by shortening the label (an earlier 2-char
    "Pr"/"Fl" abbreviation was tried and explicitly reverted).

    One automatic retry on a specific transient race, confirmed live: `agy`'s
    own Keychain read for its stored token times out after 10s when run
    headlessly (no Aqua session -- classic launchd-vs-Keychain problem), which
    surfaces as cascadeModelConfigData.errorMessage containing "not logged
    into Antigravity" even though the account IS authenticated. A background
    goroutine inside that same `agy` process still completes an OAuth refresh
    over the network and -- since its own Keychain *write* also times out --
    falls back to a plaintext token file (~/.gemini/antigravity-cli/
    antigravity-oauth-token) before the process exits. So a second `agy`
    invocation, started only after the first one has fully exited, reliably
    picks up that healed file token (confirmed live). Retrying is bounded to
    exactly once (`_retry` guards this) specifically for this error text --
    NOT a blanket retry on any {"ok": False}, since every real invocation is a
    paid prompt and most other failure causes (missing binary, never
    authenticated at all) would just pay twice for nothing."""
    agy_bin = shutil.which("agy")
    if not agy_bin:
        log("Antigravity: `agy` not found on PATH")
        return {"ok": False}
    try:
        p = subprocess.Popen(
            [agy_bin, "-p", "reply with just the word pong, no tools",
             "--model", ANTIGRAVITY_PROMPT_MODEL],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
    except OSError as e:
        log(f"Antigravity: failed to start agy: {e}")
        return {"ok": False}

    result = _antigravity_wait_for_status(p.pid, time.time() + 20)

    try:
        p.wait(timeout=60)
    except subprocess.TimeoutExpired:
        log("Antigravity: agy prompt still running after 60s, killing")
        p.kill()

    if not result:
        # stderr was discarded here before -- a real startup failure (e.g.
        # `bubbletea: could not open TTY` on a headless launchd/systemd
        # deployment, confirmed live) was indistinguishable from "just not
        # authenticated yet" in the log. p has already exited/been killed by
        # this point, so reading its stderr here doesn't block.
        stderr = ""
        try:
            stderr = (p.stderr.read() or "").strip() if p.stderr else ""
        except (OSError, ValueError):
            pass
        if stderr:
            log(f"Antigravity: no quota data, agy stderr: {stderr[:300]}")
        else:
            log("Antigravity: no quota data - is `agy` authenticated on this machine?")
        return {"ok": False}

    us = result.get("userStatus") or {}
    cascade_data = us.get("cascadeModelConfigData") or {}
    error_message = cascade_data.get("errorMessage") or ""
    if error_message:
        if _retry and ("not logged into antigravity" in error_message.lower()
                        or "getting token source" in error_message.lower()):
            log("Antigravity: keyring read timed out headlessly (transient) - "
                "retrying once, the token file should be healed by now")
            return poll_antigravity(_retry=False)
        # Anything else, or the same error on the retry, is a real failure --
        # say why. Staying silent here cost a long debugging session: the
        # symptom of an untrusted working directory is byte-identical to a
        # failed login ({"ok": False}, no further output), and with the retry
        # swallowing this text there was nothing in the log to tell them apart.
        log(f"Antigravity: no quota data - {error_message.strip()}"
            + ("" if _retry else " (this was the retry)"))
    configs = cascade_data.get("clientModelConfigs") or []

    # Deterministic pick per family, NOT "first isRecommended" -- confirmed
    # live this account has THREE entries with isRecommended:true
    # simultaneously, and clientModelConfigs' own array order changes
    # between polls (different model led the list on two separate calls
    # this session). "First isRecommended" therefore silently flips which
    # model a card shows, with a different %/reset each time and no way
    # for a viewer to tell.
    #
    # Per live feedback ("let's show the latest model of pro and flash"),
    # selection is version-first, not tightest-quota-first: within a
    # family, find the highest version number present (this account's
    # Flash family spans two generations, 3.5 and 3.6, confirmed live via
    # `agy models`), then break ties among that version's own
    # reasoning-tier variants (High/Medium/Low) by lowest
    # remainingFraction. This keeps the card pinned to the newest model
    # generation even if an older, less-used generation's quota happens
    # to be numerically tighter at some poll -- version-first is what was
    # asked for, not quota-first.
    def latest_in_family(substr: str):
        best_version = None
        version_configs = []
        for c in configs:
            if not isinstance(c, dict):
                continue
            label = c.get("label")
            if not isinstance(label, str) or substr not in label.lower():
                continue
            m = re.search(r"\d+\.\d+", label)
            if not m:
                continue
            try:
                ver = tuple(int(x) for x in m.group(0).split("."))
            except ValueError:
                continue
            if best_version is None or ver > best_version:
                best_version, version_configs = ver, [c]
            elif ver == best_version:
                version_configs.append(c)

        best, best_frac = None, None
        for c in version_configs:
            quota = c.get("quotaInfo") or {}
            frac = quota.get("remainingFraction")
            if not isinstance(frac, (int, float)):
                continue
            if best_frac is None or frac < best_frac:
                best, best_frac = c, frac
        return best, best_frac

    # Device-facing label is "<version> <full family word>" (e.g.
    # "3.6 Flash", "3.1 Pro") -- per explicit request ("NO we want the
    # version number... change fl to flash"), reverting an intermediate
    # 2-char-abbreviation attempt. This is wider than the device's shared
    # header row can fit next to the % value at a readable size (9 chars
    # vs. ~6 chars of room) -- the device side compensates by shrinking
    # just this page's value text, not by shortening the label further.
    def short_label(label: str, family_word: str) -> str:
        m = re.search(r"\d+\.\d+", label)
        return f"{m.group(0)} {family_word}" if m else family_word

    def fill_entry(payload: dict, prefix: str, best, best_frac, family_word: str):
        if best is None:
            return
        payload[f"pct{prefix}"] = int(round(max(0, min(100, (1 - best_frac) * 100))))
        label = best.get("label")
        if isinstance(label, str) and label:
            payload[f"label{prefix}"] = short_label(label, family_word)
        quota = best.get("quotaInfo") or {}
        reset_str = quota.get("resetTime")
        if isinstance(reset_str, str):
            try:
                reset_dt = datetime.fromisoformat(reset_str.replace("Z", "+00:00"))
                mins = (reset_dt.timestamp() - time.time()) / 60.0
                payload[f"r{prefix}"] = int(round(mins)) if mins > 0 else 0
            except ValueError:
                pass

    payload: dict = {"ok": True}
    pro_best, pro_frac = latest_in_family("pro")
    flash_best, flash_frac = latest_in_family("flash")
    fill_entry(payload, "Pro", pro_best, pro_frac, "Pro")
    fill_entry(payload, "Flash", flash_best, flash_frac, "Flash")

    payload["ok"] = len(payload) > 1
    return payload


def do_antigravity_poll() -> None:
    payload = poll_antigravity()
    antigravity_state.set_payload(payload)
    if len(payload) > 1:
        log(f"Antigravity: {payload}")


def antigravity_poller_loop(interval: float) -> None:
    log(f"Polling Antigravity quota every {interval:.0f}s (real agy prompt each poll -- real cost)")
    while not antigravity_state.stop_event.is_set():
        try:
            do_antigravity_poll()
        except Exception as e:
            log(f"Antigravity poll: unexpected error, will retry next interval: {e}")
        antigravity_state.refresh_event.wait(interval)
        antigravity_state.refresh_event.clear()


def antigravity_push_loop(stop: threading.Event, targets_fn, interval: float) -> None:
    """Same shape as codex_push_loop() but for the Antigravity payload,
    pushed to /api/antigravity on each device."""
    log(f"HTTP Antigravity push every {interval:.0f}s")
    while not stop.is_set():
        payload = antigravity_state.get_payload()
        urls = targets_fn()
        if payload.get("ok") and len(payload) > 1 and urls:
            for url in urls:
                ag_url = _resolve_url_for_push(url.replace("/api/usage", "/api/antigravity"))
                try:
                    r = httpx.post(ag_url, json=payload, timeout=10.0, headers=_push_headers())
                    if r.status_code >= 400:
                        log(f"Antigravity push {ag_url} HTTP {r.status_code}")
                except httpx.HTTPError as e:
                    log(f"Antigravity push {ag_url} failed: {e}")
        antigravity_state.push_kick_event.wait(interval)
        antigravity_state.push_kick_event.clear()


# ---- Transport: serial (USB CDC) ------------------------------------------

def find_device_port(explicit: str | None) -> str | None:
    import serial.tools.list_ports
    if explicit:
        return explicit
    ports = serial.tools.list_ports.comports()
    for p in ports:
        if p.vid == ESPRESSIF_VID and p.pid == DEVICE_PID:
            return p.device
    for p in ports:
        desc = (p.description or "").lower() + (p.product or "").lower()
        if "claude controller" in desc or "clawdmeter" in desc:
            return p.device
    return None


def serial_loop(stop: threading.Event, explicit_port: str | None) -> None:
    """Maintain the USB serial link: write the latest payload when it changes, and
    let the device request refreshes. Polling is done by poller_loop; we just ship.
    `stop` is the per-transport event so the tray can switch transports at runtime."""
    import serial
    import serial.tools.list_ports

    backoff = 1
    while not stop.is_set():
        port = find_device_port(explicit_port)
        if not port:
            state.set_status("Searching for serial device...", port=None)
            stop.wait(backoff)
            backoff = min(backoff * 2, 30)
            continue
        try:
            ser = serial.Serial(port, BAUD_RATE, timeout=SERIAL_TIMEOUT)
        except serial.SerialException as e:
            log(f"Serial open failed: {e}")
            state.set_status(f"Serial error: {e}", port=None)
            stop.wait(backoff)
            backoff = min(backoff * 2, 30)
            continue

        log(f"Serial connected: {port}")
        state.set_status("Connected", port=port)
        backoff = 1
        last_sent_version = -1
        last_port_check = time.time()
        state.refresh_event.set()   # ask the poller for fresh data on connect

        if not state.hid_enabled:
            try:
                ser.write((json.dumps({"hid": False}, separators=(",", ":")) + "\n").encode())
                ser.flush()
                log("Sent HID-disabled config to device")
            except serial.SerialException:
                pass

        try:
            while not stop.is_set():
                now = time.time()
                if now - last_port_check >= PORT_CHECK_INTERVAL:
                    last_port_check = now
                    live = {p.device for p in serial.tools.list_ports.comports()}
                    if port not in live:
                        log(f"{port} disappeared - device unplugged")
                        break

                try:
                    line = ser.readline().decode("utf-8", errors="replace").strip()
                except serial.SerialException:
                    log("Serial read error - device disconnected")
                    break
                if line:
                    try:
                        msg = json.loads(line)
                        if msg.get("refresh") or msg.get("ready"):
                            state.refresh_event.set()   # device wants fresh data
                        elif not (msg.get("ack") or msg.get("err")):
                            log(f"Device: {line}")
                    except json.JSONDecodeError:
                        log(f"Device: {line}")

                payload, version = state.get_payload_versioned()
                if version != last_sent_version and payload.get("ok"):
                    try:
                        ser.write((json.dumps(payload, separators=(",", ":")) + "\n").encode())
                        ser.flush()
                        last_sent_version = version
                    except serial.SerialException:
                        log("Serial write error - device disconnected")
                        break
        finally:
            try:
                ser.close()
            except Exception:
                pass
            with state.lock:
                state.port = None
        if not stop.is_set():
            state.set_status("Serial disconnected - reconnecting...", port=None)
            stop.wait(2)


# ---- Transport: HTTP server (device pulls) --------------------------------

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body: dict):
        data = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path.rstrip("/") in ("/healthz", "/health"):
            self._send(200, {"ok": True})
            return
        self._send(200, state.get_payload())

    def log_message(self, *args):
        pass


# ---- Transport: HTTP push (daemon POSTs to device) ------------------------

def normalize_push_url(v: str) -> str:
    v = v.strip()
    if not v.startswith(("http://", "https://")):
        v = "http://" + v
    if "/api/usage" not in v:
        v = v.rstrip("/") + "/api/usage"
    return v


def _resolve_via_ping(host: str):
    """Resolve a .local hostname via `ping -c1`, which reaches macOS's
    mDNSResponder directly. Measured live this session: httpx/
    socket.getaddrinfo() on this Mac takes a consistent ~5s per call to
    resolve a .local name (not a one-time cold-cache warm-up -- 3
    consecutive calls all took ~5s), while ping's resolver answers in
    under 50ms. macOS ONLY -- an advisor review flagged that Linux (pi5,
    the actual production deployment) has no such slowdown (Avahi/
    nss-mdns already resolve fast through getaddrinfo, confirmed live),
    so gating this any wider risks a real regression: `ping -c 1` with no
    reply-timeout flag can block for ~10s against an offline host on
    Linux, which would stall every push loop right when a reconnect-kick
    needs to fire fast (see CLAUDE.md's push-kick sections). Only macOS
    was ever measured to need this, so only macOS gets it."""
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(["ping", "-c", "1", host],
                              capture_output=True, text=True, timeout=3)
        m = re.search(r"\((\d+\.\d+\.\d+\.\d+)\)", out.stdout)
        return m.group(1) if m else None
    except (subprocess.SubprocessError, OSError):
        return None


def _resolve_url_for_push(url: str) -> str:
    """Rewrite a .local hostname in `url` to its current IP via
    _resolve_via_ping(). No caching -- an advisor review flagged that a
    cached IP would defeat the reconnect-kick machinery (push_kick_event)
    on a real device reboot/IP-change, silently serving a stale IP for up
    to a cache window instead of failing fast and re-resolving. Ping
    itself is ~35ms, negligible against a 20-30s push interval, so paying
    it every call is cheaper than the staleness risk. Falls back to the
    original URL unchanged if ping fails/isn't run (not macOS) or the
    host isn't a .local name -- httpx's own resolver still works
    everywhere, just slower on some platforms."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname
    if not host or not host.endswith(".local"):
        return url
    ip = _resolve_via_ping(host)
    if not ip:
        return url
    netloc = f"{ip}:{parts.port}" if parts.port else ip
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _static_push_urls(cfg: dict) -> list:
    """Normalize the configured push target(s) into a list of URLs.

    cfg['push_url'] may be a single string or a list (from a repeated --push-to).
    A string entry can itself hold several hosts separated by commas/whitespace/
    semicolons, so one CLAWDMETER_PUSH_URL env var or one --push-to can target
    several devices (e.g. "192.168.1.44, 192.168.1.45"). Blank entries and
    duplicates are dropped.
    """
    raw = cfg.get("push_url") or ""
    items = raw if isinstance(raw, list) else [raw]
    out = []
    for entry in items:
        for it in re.split(r"[,\s;]+", (entry or "").strip()):
            it = it.strip()
            if it:
                u = normalize_push_url(it)
                if u not in out:
                    out.append(u)
    return out


# ---- mDNS discovery: find every SmallTV on the LAN ------------------------
# smalltv-mod firmware (2.8.0+) advertises a `_clawdmeter._tcp` service whose TXT
# record carries the push path. Browsing for it lets one daemon push to every
# device with no hardcoded address, so several SmallTVs stay in sync without a
# --push-to per device. zeroconf is optional: without it, push still works with
# explicit --push-to hosts.

class Discovery:
    SERVICE = "_clawdmeter._tcp.local."

    def __init__(self):
        self._lock = threading.Lock()
        self._urls: dict = {}     # mDNS service name -> push URL
        self._zc = None
        self._browser = None

    def start(self) -> bool:
        try:
            from zeroconf import Zeroconf, ServiceBrowser
        except ImportError:
            log("mDNS discovery needs zeroconf (pip install zeroconf) - discovery off")
            return False
        self._zc = Zeroconf()
        self._browser = ServiceBrowser(self._zc, self.SERVICE, handlers=[self._on_change])
        log("mDNS discovery on - browsing for SmallTV devices")
        return True

    def _on_change(self, zeroconf, service_type, name, state_change) -> None:
        from zeroconf import ServiceStateChange
        if state_change is ServiceStateChange.Removed:
            with self._lock:
                if self._urls.pop(name, None):
                    log(f"device left: {name}")
            return
        # Resolve off the browser thread so a slow lookup can't stall discovery.
        threading.Thread(target=self._resolve, args=(zeroconf, service_type, name),
                         daemon=True).start()

    def _resolve(self, zeroconf, service_type, name) -> None:
        try:
            info = zeroconf.get_service_info(service_type, name, timeout=2000)
        except Exception:
            return
        if not info:
            return
        addrs = info.parsed_addresses() if hasattr(info, "parsed_addresses") else []
        if not addrs:
            return
        path = (info.properties or {}).get(b"path") or b"/api/usage"
        if isinstance(path, (bytes, bytearray)):
            path = path.decode(errors="replace")
        url = f"http://{addrs[0]}:{info.port}{path}"
        with self._lock:
            if self._urls.get(name) != url:
                self._urls[name] = url
                log(f"device found: {name} -> {url}")

    def urls(self) -> list:
        with self._lock:
            return list(self._urls.values())

    def stop(self) -> None:
        try:
            if self._zc is not None:
                self._zc.close()
        except Exception:
            pass
        self._zc = None
        self._browser = None


def push_loop(stop: threading.Event, targets_fn, interval: float) -> None:
    """POST the latest payload to every current target each interval.

    targets_fn() returns the live list of (url, trusted) pairs -- static
    --push-to hosts (trusted) plus anything mDNS discovery has found
    (untrusted, see the "push" transport's targets() closure), so devices
    coming and going are handled without restarting the loop. A per-URL
    failure never blocks the others. `trusted`/`_push_headers()` are
    vestigial (devices no longer support a write-auth key) but kept as the
    seam for any future per-target header, e.g. so an mDNS-discovered device
    that hasn't been vetted as the user's own could be treated differently.
    """
    log(f"HTTP push every {interval:.0f}s (static targets + mDNS discovery)")
    ok_urls: set = set()
    while not stop.is_set():
        payload = state.get_payload()
        targets = targets_fn()
        urls = [url for url, _trusted in targets]
        state.push_target = ", ".join(urls)
        if payload.get("ok") and urls:
            for url, trusted in targets:
                # url itself stays the configured hostname (used below as the
                # ok_urls/state.push_target identity) -- only the actual
                # request target is resolved, so reachability tracking and
                # the UI's displayed target don't churn if the resolved IP
                # changes between pushes.
                push_url = _resolve_url_for_push(url)
                headers = _push_headers() if trusted else {}
                try:
                    r = httpx.post(push_url, json=payload, timeout=10.0, headers=headers)
                    if r.status_code >= 400:
                        log(f"Push {url} HTTP {r.status_code}")
                        ok_urls.discard(url)
                    elif url not in ok_urls:
                        log(f"Pushing to {url} OK")
                        ok_urls.add(url)
                        # Device just became reachable (first success, or a
                        # reconnect after failures) -- wake calendar/weather's
                        # push loops so a reboot doesn't leave them stale for
                        # up to their full (possibly long) interval. Also kick
                        # their poller loops (refresh_event): a poll attempt
                        # that raced a reconnect (e.g. mDNS not resolving yet
                        # right after restart) would otherwise sit on its own
                        # full interval -- weather's is 600s -- before trying
                        # again, even though the device is reachable *now*.
                        calendar_state.push_kick_event.set()
                        weather_state.push_kick_event.set()
                        zai_state.push_kick_event.set()
                        openrouter_state.push_kick_event.set()
                        codex_state.push_kick_event.set()
                        # Antigravity's push (re-sending its cached payload
                        # sooner) is free, so it gets kicked like the others.
                        # Its refresh_event is deliberately NOT kicked here --
                        # unlike every other feature's poll, an Antigravity
                        # poll fires a real, costed `agy -p` prompt (see
                        # poll_antigravity()), and a reconnect can happen
                        # often (device reboots, IP drift) -- kicking it here
                        # would silently multiply real cost on every
                        # reconnect blip instead of staying on its own
                        # deliberately-long interval.
                        antigravity_state.push_kick_event.set()
                        calendar_state.refresh_event.set()
                        weather_state.refresh_event.set()
                        zai_state.refresh_event.set()
                        openrouter_state.refresh_event.set()
                        codex_state.refresh_event.set()
                except httpx.HTTPError as e:
                    log(f"Push {url} failed: {e}")
                    ok_urls.discard(url)
        elif payload.get("ok") and not urls:
            state.set_status("Push: waiting for a device (mDNS)")
        stop.wait(interval)


# ---- Transport supervisor (runtime switch from the tray) ------------------

class Transports:
    NAMES = ("serial", "push", "serve")
    LABELS = {
        "serial": "Serial (USB)",
        "push":   "HTTP push to device",
        "serve":  "HTTP serve (device pulls)",
    }

    def __init__(self, cfg: dict):
        self.lock = threading.RLock()
        self.cfg = cfg
        self.active = None
        self._stop = None        # per-transport stop event for the running thread
        self._server = None      # ThreadingHTTPServer while serving
        self._discovery = None   # mDNS Discovery while pushing

    def select(self, name: str) -> None:
        if name not in self.NAMES:
            return
        with self.lock:
            if name == self.active:
                return
            self._teardown()
            self.active = name
            self.cfg["transport"] = name
            save_config(self.cfg)
            stop = threading.Event()
            self._stop = stop
            if name == "serial":
                threading.Thread(target=serial_loop,
                                 args=(stop, self.cfg.get("serial_port")), daemon=True).start()
            elif name == "push":
                static = _static_push_urls(self.cfg)
                disc = None
                if self.cfg.get("discover", True):
                    disc = Discovery()
                    if not disc.start():
                        disc = None
                self._discovery = disc
                if not static and disc is None:
                    state.set_status("HTTP push: no target and no discovery")
                    log("Push selected but no --push-to host and discovery unavailable")
                else:
                    def targets(_static=tuple(static), _disc=disc):
                        # (url, trusted) pairs, not a flat list -- mDNS
                        # discovery (on by default unless --no-discover) has
                        # no way to verify a responder is actually the
                        # user's own device. A rogue LAN host advertising
                        # the same service type would otherwise receive the
                        # X-Secret-Key header (see push_loop()) alongside
                        # every discovered URL, real device-key exposure on
                        # any untrusted network. Static --push-to hosts are
                        # explicitly configured by the user, so they stay
                        # trusted; discovered ones never get the header.
                        urls = [(u, True) for u in _static]
                        seen = set(_static)
                        if _disc is not None:
                            for u in _disc.urls():
                                if u not in seen:
                                    urls.append((u, False))
                                    seen.add(u)
                        return urls
                    threading.Thread(
                        target=push_loop,
                        args=(stop, targets,
                              float(self.cfg.get("push_interval", DEFAULT_PUSH_INTERVAL))),
                        daemon=True).start()
            elif name == "serve":
                host = self.cfg.get("serve_host", DEFAULT_HOST)
                port = int(self.cfg.get("serve_port", DEFAULT_PORT))
                try:
                    self._server = ThreadingHTTPServer((host, port), Handler)
                except OSError as e:
                    state.set_status(f"serve error: {e}")
                    log(f"serve bind failed: {e}")
                    return
                state.endpoint = f"http://{host}:{port}/"
                threading.Thread(target=self._server.serve_forever, daemon=True).start()
            log(f"Transport -> {name}")

    def _teardown(self) -> None:
        if self._stop is not None:
            self._stop.set()
            self._stop = None
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None
        if self._discovery is not None:
            self._discovery.stop()
            self._discovery = None
        state.endpoint = ""
        state.push_target = ""
        with state.lock:
            state.port = None
        self.active = None

    def reselect(self) -> None:
        """Re-apply the active transport so a config change (e.g. new push targets)
        takes effect live. select() no-ops on an unchanged name, so tear down first."""
        with self.lock:
            active = self.active
            if not active:
                return
            self._teardown()          # clears self.active so select() will proceed
            self.select(active)

    def shutdown(self) -> None:
        with self.lock:
            self._teardown()


# ---- System tray ----------------------------------------------------------

# Mascot pose for the tray icon (claudepix idle frame; digits index MASCOT_PALETTE).
MASCOT_ROWS = [
    "00000000000000000000",
    "00000000000000000000",
    "00000000000000000000",
    "00000000000000000000",
    "00000111111111110000",
    "00000111111111110000",
    "00000112111112110000",
    "00011112111112111100",
    "00011111111111111100",
    "00011111111111111100",
    "00010111111111110100",
    "00000111111111110000",
    "00000111111111110000",
    "00000111111111110000",
    "00000100100010010000",
    "00000100100010010000",
    "00000100100010010000",
    "00000000000000000000",
    "00000000000000000000",
    "00000000000000000000",
]
MASCOT_PALETTE = [0x0000, 0xCBED, 0x0861, 0, 0, 0, 0, 0, 0, 0]  # RGB565; 1=body, 2=eye


def _rgb565(c: int) -> tuple:
    r, g, b = (c >> 11) & 0x1F, (c >> 5) & 0x3F, c & 0x1F
    return (r * 255 // 31, g * 255 // 63, b * 255 // 31)


def _make_icon_image(status_key: str):
    """Render the mascot into a tray icon, tinted by status."""
    from PIL import Image
    scale = 4
    n = len(MASCOT_ROWS)
    img = Image.new("RGBA", (n * scale, n * scale), (0, 0, 0, 0))
    px = img.load()
    for y, row in enumerate(MASCOT_ROWS):
        for x, ch in enumerate(row):
            idx = int(ch)
            if idx == 0:
                continue
            r, g, b = _rgb565(MASCOT_PALETTE[idx])
            if status_key == "searching":             # dim grey while waiting
                lum = (r * 30 + g * 59 + b * 11) // 100
                r = g = b = lum
            elif status_key == "error" and idx == 1:   # redden the body on error
                r, g, b = 200, 70, 55
            for dy in range(scale):
                for dx in range(scale):
                    px[x * scale + dx, y * scale + dy] = (r, g, b, 255)
    return img


def _run_targets_dialog(initial: str) -> int:
    """Standalone push-targets input dialog (its own process/main thread, so the
    entry accepts typing). Prints the entered value as 'OK\\n<value>' to stdout;
    Cancel/close prints nothing. Run via the DIALOG_FLAG re-invocation."""
    try:
        import tkinter as tk
    except Exception as e:
        sys.stderr.write(f"tkinter unavailable: {e}\n")
        return 1

    result = {"val": None}
    root = tk.Tk()
    root.title("clawdmeter - push targets")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    tk.Label(
        root, justify="left",
        text=("SmallTV IPs or hostnames, comma-separated:\n"
              "e.g.   192.168.1.44, 192.168.1.45\n"
              "Leave blank to use mDNS auto-discovery only."),
    ).pack(padx=16, pady=(16, 8), anchor="w")

    var = tk.StringVar(value=initial)
    entry = tk.Entry(root, textvariable=var, width=46)
    entry.pack(padx=16, pady=(0, 10), fill="x")

    def _ok():
        result["val"] = var.get()
        root.destroy()

    def _cancel():
        result["val"] = None
        root.destroy()

    bar = tk.Frame(root)
    bar.pack(padx=16, pady=(0, 16), anchor="e")
    tk.Button(bar, text="Save", width=10, command=_ok).pack(side="right", padx=(8, 0))
    tk.Button(bar, text="Cancel", width=10, command=_cancel).pack(side="right")

    entry.bind("<Return>", lambda e: _ok())
    root.bind("<Escape>", lambda e: _cancel())
    root.protocol("WM_DELETE_WINDOW", _cancel)

    root.update_idletasks()
    try:
        root.eval("tk::PlaceWindow . center")
    except Exception:
        pass
    root.lift()
    entry.focus_force()
    entry.icursor("end")
    entry.select_range(0, "end")
    # Some window managers only grant focus a beat after the window maps.
    root.after(120, lambda: (root.focus_force(), entry.focus_force()))
    root.mainloop()

    if result["val"] is not None:
        sys.stdout.write("OK\n" + result["val"])
        sys.stdout.flush()
    return 0


def tray_backend_available() -> bool:
    """Whether a system-tray icon can plausibly render on this OS/session.

    Windows and macOS always can (pystray's win32/AppKit backends need no display
    server negotiation). Linux is the hard case: it needs a running display and a
    StatusNotifier (AppIndicator) or legacy Xorg backend — and even then GNOME on
    Wayland shows nothing without the user's AppIndicator extension. When this
    returns False we run headless instead of failing."""
    if sys.platform in ("win32", "darwin"):
        return True
    if os.environ.get("PYSTRAY_BACKEND") == "dummy":
        return False
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
        except ValueError:
            gi.require_version("AppIndicator3", "0.1")
        return True
    except (ImportError, ValueError):
        pass
    if os.environ.get("DISPLAY"):
        try:
            import Xlib  # noqa: F401
            return True
        except ImportError:
            pass
    return False


def run_with_tray(transports: "Transports") -> None:
    import pystray
    icons = {k: _make_icon_image(k) for k in ("ok", "searching", "error")}

    def on_refresh(icon, item):
        state.refresh_event.set()

    def on_quit(icon, item):
        state.stop_event.set()
        state.refresh_event.set()
        transports.shutdown()
        icon.stop()

    def on_edit_targets(icon, item):
        # Edit the comma-separated push target list (IPs/hostnames) from the tray.
        # The dialog runs in a SEPARATE process so Tk owns its own main thread and
        # event loop — a Tk window created on pystray's callback thread renders but
        # can't take keyboard input. The entered value comes back over stdout.
        cur = transports.cfg.get("push_url") or ""
        if isinstance(cur, list):
            cur = ", ".join(cur)
        try:
            proc = _run([sys.executable, os.path.abspath(__file__), DIALOG_FLAG, cur],
                        capture_output=True, text=True, timeout=600)
        except Exception as e:
            log(f"Could not open the push-targets dialog: {e}")
            return
        out = proc.stdout or ""
        if not out.startswith("OK\n"):
            # returncode 1 == the child had no tkinter (common on Linux: python3-tk).
            if proc.returncode == 1 and "tkinter" in (proc.stderr or "").lower():
                log("Can't open the dialog: tkinter is missing. Install it "
                    "(Linux: python3-tk / python3-tkinter) or set push targets via "
                    "--push-to / CLAWDMETER_PUSH_URL / the config file.")
            return                            # cancelled / closed / no input
        val = out[3:].strip()
        transports.cfg["push_url"] = val
        save_config(transports.cfg)
        log(f"Push targets set to: {val or '(discovery only)'}")
        if transports.active == "push":
            transports.reselect()             # re-arm push with the new list, live
        else:
            transports.select("push")         # switch to push using the new list

    def transport_item(name):
        # Radio item: pick how the daemon sends data; switches live + is remembered.
        # pystray rejects an action callable with >2 params, so bind `name` via a
        # closure (def), not a default arg, to keep the action at (icon, item).
        def _select(icon, item):
            transports.select(name)
        return pystray.MenuItem(
            Transports.LABELS[name],
            _select,
            checked=lambda item: transports.active == name,
            radio=True,
        )

    menu = pystray.Menu(
        pystray.MenuItem(lambda _: state.get_menu_header(), None, enabled=False),
        pystray.Menu.SEPARATOR,
        transport_item("serial"),
        transport_item("push"),
        transport_item("serve"),
        pystray.MenuItem("Configure push targets...", on_edit_targets),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Refresh now", on_refresh),
        pystray.MenuItem("Quit", on_quit),
    )
    icon = pystray.Icon("clawdmeter", icon=icons["searching"],
                        title="clawdmeter - starting...", menu=menu)

    def updater():
        last = None
        while not state.stop_event.is_set():
            key = state.get_status_key()
            if key != last:
                last = key
                icon.icon = icons.get(key, icons["searching"])
            icon.title = state.get_tooltip()
            state.stop_event.wait(2)

    if sys.platform == "darwin":
        # Menu-bar-only agent: drop the Dock icon / Python rocket an unbundled
        # process would otherwise show. Harmless if pystray already set this.
        try:
            from AppKit import (NSApplication,
                                NSApplicationActivationPolicyAccessory)
            NSApplication.sharedApplication().setActivationPolicy_(
                NSApplicationActivationPolicyAccessory)
        except Exception:
            pass

    threading.Thread(target=updater, daemon=True).start()
    icon.run()
    state.stop_event.set()


def run_console() -> None:
    def _stop(*_):
        log("Stopping")
        state.stop_event.set()
        state.refresh_event.set()
    signal.signal(signal.SIGINT, _stop)
    try:
        signal.signal(signal.SIGTERM, _stop)
    except (ValueError, AttributeError):
        pass
    state.stop_event.wait()


# ---- Autostart at login (cross-platform, pure stdlib) ---------------------
# One --install / --uninstall that registers the tray daemon to start at login,
# using per-OS mechanisms that need no admin and no extra packages:
#   Windows  HKCU\...\Run value           (winreg)
#   macOS    ~/Library/LaunchAgents plist  (launchctl)
#   Linux    ~/.config/autostart .desktop  (XDG autostart, runs in the GUI session)
# The command always uses the *current* interpreter (windowless on Windows), so
# there is never a hardcoded Python path to go stale.

AUTOSTART_NAME = "clawdmeter"               # Windows Run value / Task Manager label
LAUNCHD_LABEL = "com.giovi321.clawdmeter"   # macOS LaunchAgent Label
_WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _script_path() -> str:
    return os.path.abspath(__file__)


def _windowless_interpreter() -> str:
    """The interpreter to register for autostart. On Windows, map python.exe to the
    pythonw.exe beside it so login start never flashes a console; elsewhere use the
    running interpreter. Never a hardcoded version path."""
    exe = Path(sys.executable)
    if sys.platform != "win32":
        # Keep the path unresolved: a venv's bin/python is a symlink to the base
        # interpreter, and Python only activates the venv when launched via that
        # symlink. realpath() would strip it and autostart would run outside the
        # venv (missing deps). abspath just guarantees it's absolute.
        return os.path.abspath(str(exe))
    name = exe.name.lower()
    if name.startswith("pythonw"):
        return str(exe)
    if name.startswith("python"):
        cand = exe.with_name("pythonw" + exe.name[len("python"):])
        if cand.exists():
            return str(cand)
    return str(exe)   # no pythonw beside it (rare); accept a possible console flash


def _autostart_command_args() -> list:
    return [_windowless_interpreter(), _script_path(), "--tray"]


# --- Windows: HKCU Run key ---

def _win_autostart_install() -> str:
    import winreg
    interp, script, *rest = _autostart_command_args()
    cmd = f'"{interp}" "{script}" ' + " ".join(rest)
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, AUTOSTART_NAME, 0, winreg.REG_SZ, cmd.strip())
    msg = f"Autostart on: HKCU\\...\\Run\\{AUTOSTART_NAME} = {cmd.strip()}"
    if "windowsapps" in interp.lower():
        msg += ("\nNote: this uses the Microsoft Store Python; a Store update can change "
                "its path. If autostart later fails, re-run --install (or use a python.org install).")
    return msg


def _win_autostart_uninstall() -> str:
    import winreg
    try:
        with winreg.OpenKeyEx(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0,
                              winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, AUTOSTART_NAME)
        return f"Autostart off: removed HKCU\\...\\Run\\{AUTOSTART_NAME}"
    except FileNotFoundError:
        return "Autostart was not set (nothing to remove)"


def _win_autostart_status() -> str | None:
    import winreg
    try:
        with winreg.OpenKeyEx(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0,
                              winreg.KEY_QUERY_VALUE) as k:
            return winreg.QueryValueEx(k, AUTOSTART_NAME)[0]
    except FileNotFoundError:
        return None


# --- macOS: LaunchAgent plist ---

def _mac_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _mac_autostart_install() -> str:
    from xml.sax.saxutils import escape
    logdir = Path.home() / "Library" / "Logs"
    args = "".join(f"        <string>{escape(a)}</string>\n"
                   for a in _autostart_command_args())
    # No KeepAlive on purpose: RunAtLoad starts it at login, and picking Quit from
    # the menu bar must make it stay dead (KeepAlive would immediately respawn it).
    plist = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n<dict>\n'
        f'    <key>Label</key><string>{LAUNCHD_LABEL}</string>\n'
        '    <key>ProgramArguments</key>\n    <array>\n'
        f'{args}    </array>\n'
        '    <key>RunAtLoad</key><true/>\n'
        '    <key>ProcessType</key><string>Interactive</string>\n'
        f'    <key>StandardOutPath</key><string>{escape(str(logdir / "clawdmeter.out.log"))}</string>\n'
        f'    <key>StandardErrorPath</key><string>{escape(str(logdir / "clawdmeter.err.log"))}</string>\n'
        '</dict>\n</plist>\n'
    )
    p = _mac_plist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(plist)
    # Reload so it also starts now; unload first so re-install is idempotent.
    _run(["launchctl", "unload", "-w", str(p)], capture_output=True, text=True)
    _run(["launchctl", "load", "-w", str(p)], capture_output=True, text=True)
    return f"Autostart on: wrote and loaded LaunchAgent {p}"


def _mac_autostart_uninstall() -> str:
    p = _mac_plist_path()
    if not p.exists():
        return "Autostart was not set (nothing to remove)"
    _run(["launchctl", "unload", "-w", str(p)], capture_output=True, text=True)
    p.unlink()
    return f"Autostart off: removed LaunchAgent {p}"


def _mac_autostart_status() -> str | None:
    p = _mac_plist_path()
    return str(p) if p.exists() else None


# --- Linux: XDG autostart .desktop ---

def _linux_desktop_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / "clawdmeter-daemon.desktop"


def _desktop_exec(args: list) -> str:
    # .desktop Exec: quote any token containing whitespace; escape embedded quotes.
    out = []
    for a in args:
        if any(c.isspace() for c in a) or '"' in a:
            out.append('"' + a.replace("\\", "\\\\").replace('"', '\\"') + '"')
        else:
            out.append(a)
    return " ".join(out)


def _linux_autostart_install() -> str:
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Clawdmeter Daemon\n"
        "Comment=Claude usage meter tray daemon\n"
        f"Exec={_desktop_exec(_autostart_command_args())}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
        "Hidden=false\n"
        "StartupNotify=false\n"
    )
    p = _linux_desktop_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return (f"Autostart on: wrote {p}\n"
            "It starts at your next graphical login (or run start-daemon.sh now).")


def _linux_autostart_uninstall() -> str:
    p = _linux_desktop_path()
    if not p.exists():
        return "Autostart was not set (nothing to remove)"
    p.unlink()
    return f"Autostart off: removed {p}"


def _linux_autostart_status() -> str | None:
    p = _linux_desktop_path()
    return str(p) if p.exists() else None


def autostart_install() -> str:
    return {"win32": _win_autostart_install, "darwin": _mac_autostart_install}.get(
        sys.platform, _linux_autostart_install)()


def autostart_uninstall() -> str:
    return {"win32": _win_autostart_uninstall, "darwin": _mac_autostart_uninstall}.get(
        sys.platform, _linux_autostart_uninstall)()


def autostart_status() -> str | None:
    return {"win32": _win_autostart_status, "darwin": _mac_autostart_status}.get(
        sys.platform, _linux_autostart_status)()


def _resolve_openrouter_api_key(cli_key: str | None) -> str:
    key = (cli_key or "").strip()
    if key:
        return key
    key = os.environ.get("CLAWDMETER_OPENROUTER_KEY", "").strip()
    if key:
        return key
    openrouter_key_path = os.path.expanduser("~/.openrouter_dot_ai_key")
    if os.path.isfile(openrouter_key_path):
        try:
            key = Path(openrouter_key_path).read_text().strip()
        except OSError:
            return ""
        if key:
            return key
    return ""


# ---- Entry point ----------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Deliver Claude usage to a desk device (serial / push / serve).")
    # Transports (any combination; defaults to --serve if none chosen).
    ap.add_argument("--serial", nargs="?", const="auto", default=None,
                    metavar="PORT", help="use USB serial; optional COM port (else auto-detect)")
    ap.add_argument("--no-hid", action="store_true", help="tell the serial device to disable HID keys")
    ap.add_argument("--push", action="store_true",
                    help="use HTTP push with mDNS auto-discovery of SmallTV devices")
    ap.add_argument("--push-to", action="append", default=None, metavar="DEVICE",
                    help="HTTP-push to this device (repeatable); e.g. 192.168.1.50 or smalltv.local")
    ap.add_argument("--no-discover", action="store_true",
                    help="disable mDNS auto-discovery for push (only push to --push-to hosts)")
    ap.add_argument("--push-interval", type=float, default=None)
    ap.add_argument("--serve", action="store_true", help="use the HTTP server (device polls)")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--interval", type=float, default=DEFAULT_POLL_INTERVAL,
                    help="seconds between Claude API refreshes (default 60)")
    ap.add_argument("--tray", action="store_true", help="show the tray icon (default)")
    ap.add_argument("--no-tray", action="store_true", help="run headless in the console")
    # Autostart at login (per-user, no admin). Cross-platform.
    ap.add_argument("--install", action="store_true",
                    help="register autostart at login (per-user) and exit")
    ap.add_argument("--uninstall", action="store_true",
                    help="remove the autostart entry and exit")
    ap.add_argument("--autostart-status", action="store_true",
                    help="print whether autostart is registered and exit")
    # Google Calendar (optional feature, off by default).
    ap.add_argument("--calendar-auth", action="store_true",
                    help="one-time interactive Google Calendar authorization, then exit "
                         "(run this on a machine with a browser; see printed instructions "
                         "if no OAuth client is set up yet)")
    ap.add_argument("--calendar-sync-color", metavar="CALENDAR_ID", default=None,
                    help="one-time: read this calendar's real colorId using your own "
                         "OAuth login (needs --calendar-auth run at least once) and apply "
                         "it to the service account's calendarList entry, then exit. "
                         "Service accounts can't see per-calendar color on their own -- "
                         "this bridges that gap once; not needed again afterward")
    ap.add_argument("--calendar-sync-selected", action="store_true",
                    help="one-time: sync + color every calendar currently checked in your "
                         "own Google Calendar sidebar (via OAuth), then print the exact "
                         "--calendar-id value to use, then exit. A service account has no "
                         "sidebar of its own to auto-detect this from -- re-run any time "
                         "your checked calendars change")
    ap.add_argument("--calendar", action="store_true",
                    help="enable the Google Calendar feature (needs --calendar-auth run once first)")
    ap.add_argument("--no-calendar", action="store_true",
                    help="disable the Google Calendar feature (overrides a previously remembered --calendar)")
    ap.add_argument("--calendar-id", default=None,
                    help="comma-separated Google Calendar ID(s) to poll, overriding "
                         "auto-detection. Default: auto-detect every calendar checked "
                         "('selected') in your Google Calendar sidebar -- no manual list "
                         "needed, changes automatically when you toggle a checkbox there")
    ap.add_argument("--calendar-interval", type=float, default=None,
                    help=f"seconds between Calendar refreshes (default {DEFAULT_CALENDAR_INTERVAL:.0f})")
    ap.add_argument("--calendar-push-interval", type=float, default=None,
                    help="seconds between pushing the calendar payload to the device "
                         "(default: same as --calendar-interval -- no point pushing more "
                         "often than new data is actually polled)")
    # Weather + air quality (optional feature, off by default). Fetches
    # Open-Meteo (free, no key) using the lat/lon set in the device's own
    # Calendar tab -- no separate location config needed here.
    ap.add_argument("--weather", action="store_true",
                    help="enable weather/AQI push (reads location from the device's web UI)")
    ap.add_argument("--no-weather", action="store_true",
                    help="disable the weather/AQI feature (overrides a previously remembered --weather)")
    ap.add_argument("--weather-interval", type=float, default=None,
                    help=f"seconds between weather/AQI refreshes (default {DEFAULT_WEATHER_INTERVAL:.0f})")
    # z.ai quota (optional feature, off by default). UNDOCUMENTED endpoint --
    # see poll_zai()'s docstring and CLAUDE.md's research notes.
    ap.add_argument("--zai", action="store_true",
                    help="enable z.ai (GLM) quota push")
    ap.add_argument("--no-zai", action="store_true",
                    help="disable the z.ai feature (overrides a previously remembered --zai)")
    ap.add_argument("--zai-key", default=None,
                    help="z.ai API key (also settable via CLAWDMETER_ZAI_KEY / .env). "
                         "Required for --zai to do anything.")
    ap.add_argument("--zai-interval", type=float, default=None,
                    help=f"seconds between z.ai quota refreshes (default {DEFAULT_ZAI_INTERVAL:.0f})")
    # OpenRouter spend (optional feature, off by default). Lightweight
    # authenticated read, no per-call model/token cost.
    ap.add_argument("--openrouter", action="store_true",
                    help="enable OpenRouter dollar-spend push (lightweight key read, no per-call cost)")
    ap.add_argument("--no-openrouter", action="store_true",
                    help="disable the OpenRouter feature (overrides a previously remembered --openrouter)")
    ap.add_argument("--openrouter-key", default=None,
                    help="OpenRouter API key (also settable via CLAWDMETER_OPENROUTER_KEY / .env; "
                         "falls back to ~/.openrouter_dot_ai_key). Required for --openrouter to do anything.")
    ap.add_argument("--openrouter-interval", type=float, default=None,
                    help=f"seconds between OpenRouter spend refreshes (default {DEFAULT_OPENROUTER_INTERVAL:.0f})")
    # Codex CLI quota (optional feature, off by default). Real ChatGPT-plan
    # usage, no API key/billing involved -- needs `codex login` already done
    # on this machine. Each poll briefly spins up `codex app-server` and
    # reads cached rate-limit state over JSON-RPC -- no model call, no cost
    # (see poll_codex()/_codex_rpc_call() for the mechanism; supersedes both
    # the abandoned OpenAI-API-header idea and an earlier session-log-glob
    # design, see CLAUDE.md).
    ap.add_argument("--codex", action="store_true",
                    help="enable Codex CLI quota push (needs `codex login` already done "
                         "on this machine -- rides your ChatGPT plan's included usage, "
                         "no separate API key/cost)")
    ap.add_argument("--no-codex", action="store_true",
                    help="disable the Codex feature (overrides a previously remembered --codex)")
    ap.add_argument("--codex-interval", type=float, default=None,
                    help=f"seconds between Codex quota refreshes (default {DEFAULT_CODEX_INTERVAL:.0f})")
    # Antigravity CLI (`agy`) quota (optional feature, off by default).
    # UNLIKE Codex, this fires a real, tiny prompt every poll -- a genuine
    # cost, not free (see poll_antigravity() for why no free path exists).
    ap.add_argument("--antigravity", action="store_true",
                    help="enable Antigravity CLI quota push (needs `agy` authenticated "
                         "on this machine -- fires a real cheap-model prompt every poll, "
                         "a real cost, unlike --codex)")
    ap.add_argument("--no-antigravity", action="store_true",
                    help="disable the Antigravity feature (overrides a previously remembered --antigravity)")
    ap.add_argument("--antigravity-interval", type=float, default=None,
                    help=f"seconds between Antigravity quota refreshes (default {DEFAULT_ANTIGRAVITY_INTERVAL:.0f})")
    args = ap.parse_args()

    global _zai_api_key, _openrouter_api_key
    _zai_api_key = args.zai_key or os.environ.get("CLAWDMETER_ZAI_KEY", "")
    _openrouter_api_key = _resolve_openrouter_api_key(args.openrouter_key)

    # Autostart management runs and exits; it never starts the daemon.
    if args.install:
        print(autostart_install())
        return
    if args.uninstall:
        print(autostart_uninstall())
        return
    if args.autostart_status:
        where = autostart_status()
        print(f"autostart: {where}" if where else "autostart: not registered")
        return
    if args.calendar_auth:
        calendar_auth_flow()
        return
    if args.calendar_sync_color:
        calendar_sync_color_flow(args.calendar_sync_color)
        return
    if args.calendar_sync_selected:
        calendar_sync_selected_flow()
        return

    state.hid_enabled = not args.no_hid

    # Remembered config; a transport flag (or the tray menu) overrides it.
    cfg = load_config()
    if not cfg.get("push_url"):
        cfg["push_url"] = (os.environ.get("CLAWDMETER_PUSH_URL")
                           or os.environ.get("SMALLTV_PUSH_URL") or "")
    chosen = None
    if args.serial is not None:
        cfg["serial_port"] = None if args.serial == "auto" else args.serial
        chosen = "serial"
    if args.push:
        chosen = "push"
    if args.push_to:
        cfg["push_url"] = args.push_to      # list (repeatable)
        chosen = "push"
    if args.no_discover:
        cfg["discover"] = False
    if args.serve:
        chosen = "serve"
    if args.host is not None:
        cfg["serve_host"] = args.host
    if args.port is not None:
        cfg["serve_port"] = args.port
    if args.push_interval is not None:
        cfg["push_interval"] = args.push_interval
    if args.calendar:
        cfg["calendar_enabled"] = True
    if args.no_calendar:
        cfg["calendar_enabled"] = False
    if args.calendar_id is not None:
        cfg["calendar_id"] = args.calendar_id
    if args.calendar_interval is not None:
        cfg["calendar_interval"] = args.calendar_interval
    if args.calendar_push_interval is not None:
        cfg["calendar_push_interval"] = args.calendar_push_interval
    if args.weather:
        cfg["weather_enabled"] = True
    if args.no_weather:
        cfg["weather_enabled"] = False
    if args.weather_interval is not None:
        cfg["weather_interval"] = args.weather_interval
    if args.zai:
        cfg["zai_enabled"] = True
    if args.no_zai:
        cfg["zai_enabled"] = False
    if args.zai_interval is not None:
        cfg["zai_interval"] = args.zai_interval
    # _zai_api_key is NOT stored in cfg -- it's a credential, not a UI
    # preference.
    if args.openrouter:
        cfg["openrouter_enabled"] = True
    if args.no_openrouter:
        cfg["openrouter_enabled"] = False
    if args.openrouter_interval is not None:
        cfg["openrouter_interval"] = args.openrouter_interval
    # _openrouter_api_key is NOT stored in cfg -- it's a credential, not a UI
    # preference.
    if args.codex:
        cfg["codex_enabled"] = True
    if args.no_codex:
        cfg["codex_enabled"] = False
    if args.codex_interval is not None:
        cfg["codex_interval"] = args.codex_interval
    if args.antigravity:
        cfg["antigravity_enabled"] = True
    if args.no_antigravity:
        cfg["antigravity_enabled"] = False
    if args.antigravity_interval is not None:
        cfg["antigravity_interval"] = args.antigravity_interval

    # Initial transport: a CLI flag wins, else the remembered choice, else a sensible
    # default — push if a target is configured (env/config), otherwise serve.
    initial = chosen or cfg.get("transport")
    if not initial:
        initial = "push" if cfg.get("push_url") else "serve"
    save_config(cfg)

    threading.Thread(target=poller_loop, args=(args.interval,), daemon=True).start()
    transports = Transports(cfg)
    transports.select(initial)
    log(f"clawdmeter-daemon: transport = {initial}  (switch from the tray menu)")

    # Calendar: independent of the primary Transport above — it's an on/off
    # feature, not a transport choice, so it stays running (or off) regardless
    # of whether the tray later switches serial/push/serve for the usage side.
    if cfg.get("calendar_enabled"):
        raw_ids = (cfg.get("calendar_id") or "").strip()
        calendar_ids = [c.strip() for c in raw_ids.split(",") if c.strip()] or None
        cal_interval = float(cfg.get("calendar_interval", DEFAULT_CALENDAR_INTERVAL))
        cal_targets = tuple(_static_push_urls(cfg))
        # Read the live calendar-id override from the same device this
        # daemon is already pushing to -- first target, same convention
        # weather's config_url uses. Only meaningful with a push target.
        cal_config_url = _device_config_url(cal_targets[0]) if cal_targets else None
        threading.Thread(target=calendar_poller_loop,
                         args=(cal_interval, calendar_ids, cal_config_url), daemon=True).start()
        if cal_targets:
            cal_stop = threading.Event()
            cal_push_interval = float(cfg.get("calendar_push_interval", cal_interval))
            threading.Thread(target=calendar_push_loop,
                             args=(cal_stop, lambda: cal_targets, cal_push_interval),
                             daemon=True).start()
        else:
            log("Calendar enabled but no --push-to host configured - nothing to push to")

    # Weather: independent of the primary Transport too, same reasoning as Calendar.
    if cfg.get("weather_enabled"):
        wx_targets = tuple(_static_push_urls(cfg))
        if wx_targets:
            wx_interval = float(cfg.get("weather_interval", DEFAULT_WEATHER_INTERVAL))
            config_url = _device_config_url(wx_targets[0])
            threading.Thread(target=weather_poller_loop,
                             args=(wx_interval, config_url), daemon=True).start()
            wx_stop = threading.Event()
            threading.Thread(target=weather_push_loop,
                             args=(wx_stop, lambda: wx_targets,
                                   float(cfg.get("push_interval", DEFAULT_PUSH_INTERVAL))),
                             daemon=True).start()
        else:
            log("Weather enabled but no --push-to host configured - nothing to push to")

    # z.ai: independent of the primary Transport too, same reasoning as Calendar/Weather.
    if cfg.get("zai_enabled"):
        if not _zai_api_key:
            log("Z.AI enabled but no --zai-key/CLAWDMETER_ZAI_KEY configured - nothing to poll")
        else:
            zai_targets = tuple(_static_push_urls(cfg))
            if zai_targets:
                zai_interval = float(cfg.get("zai_interval", DEFAULT_ZAI_INTERVAL))
                threading.Thread(target=zai_poller_loop,
                                 args=(zai_interval, _zai_api_key), daemon=True).start()
                zai_stop = threading.Event()
                threading.Thread(target=zai_push_loop,
                                 args=(zai_stop, lambda: zai_targets,
                                       float(cfg.get("push_interval", DEFAULT_PUSH_INTERVAL))),
                                 daemon=True).start()
            else:
                log("Z.AI enabled but no --push-to host configured - nothing to push to")

    # OpenRouter: independent of the primary Transport too, same reasoning as z.ai.
    if cfg.get("openrouter_enabled"):
        if not _openrouter_api_key:
            log("OpenRouter enabled but no --openrouter-key/CLAWDMETER_OPENROUTER_KEY/~/.openrouter_dot_ai_key configured - nothing to poll")
        else:
            openrouter_targets = tuple(_static_push_urls(cfg))
            if openrouter_targets:
                openrouter_interval = float(cfg.get("openrouter_interval", DEFAULT_OPENROUTER_INTERVAL))
                threading.Thread(target=openrouter_poller_loop,
                                 args=(openrouter_interval, _openrouter_api_key), daemon=True).start()
                openrouter_stop = threading.Event()
                threading.Thread(target=openrouter_push_loop,
                                 args=(openrouter_stop, lambda: openrouter_targets,
                                       float(cfg.get("push_interval", DEFAULT_PUSH_INTERVAL))),
                                 daemon=True).start()
            else:
                log("OpenRouter enabled but no --push-to host configured - nothing to push to")

    # Codex: independent of the primary Transport too, same reasoning as z.ai.
    if cfg.get("codex_enabled"):
        codex_targets = tuple(_static_push_urls(cfg))
        if codex_targets:
            codex_interval = float(cfg.get("codex_interval", DEFAULT_CODEX_INTERVAL))
            threading.Thread(target=codex_poller_loop, args=(codex_interval,), daemon=True).start()
            codex_stop = threading.Event()
            threading.Thread(target=codex_push_loop,
                             args=(codex_stop, lambda: codex_targets,
                                   float(cfg.get("push_interval", DEFAULT_PUSH_INTERVAL))),
                             daemon=True).start()
        else:
            log("Codex enabled but no --push-to host configured - nothing to push to")

    # Antigravity: independent of the primary Transport too, same reasoning
    # as z.ai/Codex. Real per-poll cost -- see poll_antigravity().
    if cfg.get("antigravity_enabled"):
        antigravity_targets = tuple(_static_push_urls(cfg))
        if antigravity_targets:
            antigravity_interval = float(cfg.get("antigravity_interval", DEFAULT_ANTIGRAVITY_INTERVAL))
            threading.Thread(target=antigravity_poller_loop, args=(antigravity_interval,), daemon=True).start()
            antigravity_stop = threading.Event()
            threading.Thread(target=antigravity_push_loop,
                             args=(antigravity_stop, lambda: antigravity_targets,
                                   float(cfg.get("push_interval", DEFAULT_PUSH_INTERVAL))),
                             daemon=True).start()
        else:
            log("Antigravity enabled but no --push-to host configured - nothing to push to")

    use_tray = not args.no_tray
    if use_tray and not tray_backend_available():
        log("No system-tray backend on this session - running headless. (Linux needs a "
            "display plus AppIndicator/Xlib; GNOME on Wayland also needs the AppIndicator "
            "GNOME extension.)")
        use_tray = False
    if use_tray:
        try:
            import pystray  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError:
            log("pystray/Pillow not installed - running headless (pip install pystray Pillow)")
            use_tray = False
    if use_tray:
        try:
            run_with_tray(transports)
        except Exception as e:      # backend init/runtime failure -> don't die silently
            log(f"Tray failed to start ({e!r}) - running headless")
            run_console()
    else:
        run_console()
    transports.shutdown()
    log("Stopped")


if __name__ == "__main__":
    # A dialog re-invocation (from the tray's "Configure push targets...") shows the
    # input window in this fresh process, prints the result, and exits — it must
    # never fall through to the daemon.
    if len(sys.argv) >= 2 and sys.argv[1] == DIALOG_FLAG:
        sys.exit(_run_targets_dialog(sys.argv[2] if len(sys.argv) > 2 else ""))
    main()
