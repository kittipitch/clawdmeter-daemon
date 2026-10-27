"""Tests for the Better Stack Uptime feed: parsing, ordering, filtering,
secret protection, key resolution, polling, and pagination."""

import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types

try:
    module = importlib.import_module("clawdmeter_daemon")
except ModuleNotFoundError:
    sys.modules.pop("clawdmeter_daemon", None)
    httpx = types.ModuleType("httpx")
    httpx.HTTPError = Exception
    sys.modules.setdefault("httpx", httpx)
    sys.modules.setdefault("aqi", types.ModuleType("aqi"))
    module = importlib.import_module("clawdmeter_daemon")


# --- 1. Status mapping incl. unknown -> pending -----------------------------
test_cases = [
    ("up", "up"),
    ("down", "down"),
    ("paused", "paused"),
    ("maintenance", "paused"),
    ("pending", "pending"),
    ("validating", "pending"),
    ("new", "pending"),
    ("unknown_status", "pending"),
]
for raw, expected in test_cases:
    m = {"data": [{"id": "1", "type": "monitor", "attributes": {"pronounceable_name": "srv", "status": raw}}]}
    r = module.parse_uptime(m, {"data": []})
    assert r["ok"] is True
    assert r["targets"][0]["status"] == expected, f"{raw} -> {r['targets'][0]['status']} != {expected}"

# missing status attribute maps to pending
hb_missing = {"data": [{"id": "2", "type": "heartbeat", "attributes": {"name": "beat"}}]}
r_missing = module.parse_uptime({"data": []}, hb_missing)
assert r_missing["ok"] is True
assert r_missing["targets"][0]["status"] == "pending"


# --- 2. Ordering down < pending < paused < up with name ties ----------------
monitors_order = {
    "data": [
        {"id": "1", "type": "monitor", "attributes": {"pronounceable_name": "bravo", "status": "up"}},
        {"id": "2", "type": "monitor", "attributes": {"pronounceable_name": "alpha", "status": "up"}},
        {"id": "3", "type": "monitor", "attributes": {"pronounceable_name": "zulu", "status": "paused"}},
        {"id": "4", "type": "monitor", "attributes": {"pronounceable_name": "echo", "status": "pending"}},
        {"id": "5", "type": "monitor", "attributes": {"pronounceable_name": "delta", "status": "down"}},
        {"id": "6", "type": "monitor", "attributes": {"pronounceable_name": "charlie", "status": "down"}},
    ]
}
res_order = module.parse_uptime(monitors_order, {"data": []})
assert res_order["ok"] is True
ordered_names = [t["name"] for t in res_order["targets"]]
# down ties: charlie, delta; then pending: echo; then paused: zulu; then up ties: alpha, bravo
assert ordered_names == ["charlie", "delta", "echo", "zulu", "alpha", "bravo"], ordered_names


# --- 3. Target filter case-insensitive --------------------------------------
monitors_filter = {
    "data": [
        {"id": "1", "type": "monitor", "attributes": {"pronounceable_name": "Web-Prod", "status": "up"}},
        {"id": "2", "type": "monitor", "attributes": {"pronounceable_name": "Database", "status": "down"}},
        {"id": "3", "type": "monitor", "attributes": {"pronounceable_name": "Cache", "status": "up"}},
    ]
}
res_filter = module.parse_uptime(monitors_filter, {"data": []}, targets=["  web-prod  ", "DATABASE"])
assert res_filter["ok"] is True
filtered_names = [t["name"] for t in res_filter["targets"]]
assert filtered_names == ["Database", "Web-Prod"]  # down first, then up


# --- 4. Cap at 8 & 5. down count --------------------------------------------
many_monitors = {
    "data": [
        {"id": str(i), "type": "monitor", "attributes": {"pronounceable_name": f"host-{i:02d}", "status": "down" if i < 10 else "up"}}
        for i in range(12)
    ]
}
res_cap = module.parse_uptime(many_monitors, {"data": []})
assert res_cap["ok"] is True
assert len(res_cap["targets"]) == 8
# down count is counted among ALL kept items before the cap (10 down items)
assert res_cap["down"] == 10


# --- 6. Any input None or invalid -> {"ok": False} --------------------------
assert module.parse_uptime(None, {"data": []}) == {"ok": False}
assert module.parse_uptime({"data": []}, None) == {"ok": False}
assert module.parse_uptime(None, None) == {"ok": False}
assert module.parse_uptime({}, {"data": []}) == {"ok": False}
assert module.parse_uptime({"data": []}, {}) == {"ok": False}
assert module.parse_uptime({"data": "not-list"}, {"data": []}) == {"ok": False}


# --- 7. Item with no name falls back to id / ? & truncation to 40 chars -----
monitors_noname = {
    "data": [
        {"id": "1", "type": "monitor", "attributes": {"pronounceable_name": "", "name": None, "status": "up"}},
        {"id": "2", "type": "monitor", "attributes": {"pronounceable_name": "   ", "status": "up"}},
        {"id": "3", "type": "monitor", "attributes": {"status": "up"}},
        {"id": "4", "type": "monitor", "attributes": {"pronounceable_name": "valid-name", "status": "up"}},
        {"id": "5", "type": "monitor", "attributes": {"pronounceable_name": "abcdefghijklmnopqrstuvwxyz1234567890ABCDEFGHIJ", "status": "up"}},
    ]
}
res_noname = module.parse_uptime(monitors_noname, {"data": []})
assert res_noname["ok"] is True
assert len(res_noname["targets"]) == 5
assert [t["name"] for t in res_noname["targets"]] == ["1", "2", "3", "abcdefghijklmnopqrstuvwxyz1234567890ABCD", "valid-name"]
assert res_noname["targets"][3]["name"] == "abcdefghijklmnopqrstuvwxyz1234567890ABCD"  # truncated to 40
assert len(res_noname["targets"][3]["name"]) == 40
assert res_noname["targets"][3]["kind"] == "site"
assert res_noname["targets"][4]["name"] == "valid-name"


# --- 8. SECRET LEAK TEST ----------------------------------------------------
heartbeat_secret = {
    "data": [
        {
            "id": "hb1",
            "type": "heartbeat",
            "attributes": {
                "name": "backup-runner",
                "status": "up",
                "url": "https://uptime.betterstack.com/api/v1/heartbeat/SECRETTOKEN123",
                "email": "leak@example.com",
            },
        }
    ]
}
res_secret = module.parse_uptime({"data": []}, heartbeat_secret)
assert res_secret["ok"] is True
dumped = json.dumps(res_secret)
assert "SECRETTOKEN123" not in dumped
assert "leak@example.com" not in dumped
assert all("url" not in t for t in res_secret["targets"])
assert res_secret["targets"] == [{"name": "backup-runner", "kind": "beat", "status": "up", "sinceS": None, "cause": None}]


# --- 9. Key resolution order -------------------------------------------------
with tempfile.TemporaryDirectory() as tmpdir:
    tmp_path = Path(tmpdir)
    fake_home = tmp_path / "home"
    default_dir = fake_home / ".config" / "clawdmeter"
    default_dir.mkdir(parents=True)
    default_key_file = default_dir / "betterstack.key"
    default_key_file.write_text("  default-token-val  \n")

    custom_key_file = tmp_path / "custom.key"
    custom_key_file.write_text("\t custom-token-val \n")

    empty_key_file = tmp_path / "empty.key"
    empty_key_file.write_text("   \n\t  ")

    orig_home = module.Path.home
    module.Path.home = lambda: fake_home

    orig_env = dict(os.environ)
    try:
        # A. Default path resolution (when no env vars set)
        os.environ.pop("CLAWDMETER_BETTERSTACK_KEY", None)
        os.environ.pop("CLAWDMETER_BETTERSTACK_KEY_FILE", None)
        os.environ.pop("XDG_CONFIG_HOME", None)
        assert module._resolve_betterstack_key() == "default-token-val"

        # B. KEY_FILE wins over default path
        os.environ["CLAWDMETER_BETTERSTACK_KEY_FILE"] = str(custom_key_file)
        assert module._resolve_betterstack_key() == "custom-token-val"

        # C. Env value wins over KEY_FILE
        os.environ["CLAWDMETER_BETTERSTACK_KEY"] = "  direct-env-token  "
        assert module._resolve_betterstack_key() == "direct-env-token"

        # D. Whitespace stripped and empty file -> ""
        os.environ.pop("CLAWDMETER_BETTERSTACK_KEY", None)
        os.environ["CLAWDMETER_BETTERSTACK_KEY_FILE"] = str(empty_key_file)
        assert module._resolve_betterstack_key() == ""

        # Nonexistent file -> ""
        os.environ["CLAWDMETER_BETTERSTACK_KEY_FILE"] = str(tmp_path / "missing.key")
        assert module._resolve_betterstack_key() == ""
    finally:
        module.Path.home = orig_home
        os.environ.clear()
        os.environ.update(orig_env)


# --- 10. poll_uptime with monkeypatched module._betterstack_get --------------
orig_key_fn = module._resolve_betterstack_key
orig_get_fn = module._betterstack_get
try:
    # A. No key -> ok False
    module._resolve_betterstack_key = lambda: ""
    assert module.poll_uptime() == {"ok": False}

    # B. Success
    module._resolve_betterstack_key = lambda: "valid-key"
    module._betterstack_get = lambda resource, key: {
        "data": [{"id": "1", "type": "monitor", "attributes": {"pronounceable_name": "app", "status": "up"}}]
    } if resource == "monitors" else {"data": []}
    res_poll = module.poll_uptime(cfg_targets=["app"])
    assert res_poll["ok"] is True
    assert res_poll["targets"][0]["name"] == "app"

    # C. One resource None -> ok False
    module._betterstack_get = lambda resource, key: None if resource == "monitors" else {"data": []}
    assert module.poll_uptime() == {"ok": False}

    module._betterstack_get = lambda resource, key: {"data": []} if resource == "monitors" else None
    assert module.poll_uptime() == {"ok": False}
finally:
    module._resolve_betterstack_key = orig_key_fn
    module._betterstack_get = orig_get_fn


# --- 11. Pagination in _betterstack_get with monkeypatched httpx.get --------
class DummyResponse:
    def __init__(self, data_list, next_url, status_code=200):
        self.status_code = status_code
        self._payload = {
            "data": data_list,
            "pagination": {"next": next_url},
        }

    def json(self):
        return self._payload


page1_resp = DummyResponse(
    [{"id": "1", "attributes": {"name": "p1"}}],
    f"{module.BETTERSTACK_API}/monitors?page=2",
)
page2_resp = DummyResponse(
    [{"id": "2", "attributes": {"name": "p2"}}],
    None,
)

calls = []


def fake_httpx_get(url, headers=None, params=None, timeout=None):
    calls.append((url, headers, params))
    if "page=2" in url:
        return page2_resp
    return page1_resp


orig_httpx_get = module.httpx.get
try:
    module.httpx.get = fake_httpx_get
    res_pag = module._betterstack_get("monitors", "dummy-key")
    assert res_pag == {
        "data": [
            {"id": "1", "attributes": {"name": "p1"}},
            {"id": "2", "attributes": {"name": "p2"}},
        ]
    }
    assert len(calls) == 2
    assert calls[0][0] == f"{module.BETTERSTACK_API}/monitors"
    assert calls[0][1] == {"Authorization": "Bearer dummy-key"}
    assert calls[0][2] == {"per_page": 50}
    assert calls[1][0] == f"{module.BETTERSTACK_API}/monitors?page=2"
finally:
    module.httpx.get = orig_httpx_get


# --- 12. Log leak test (item 7a) --------------------------------------------
captured_logs = []
orig_log = module.log
module.log = lambda msg: captured_logs.append(str(msg))

secret_key = "SUPERSECRETKEY123"

class Non200Response:
    status_code = 500
    def json(self):
        return {"error": "server error"}

class NonJSONResponse:
    status_code = 200
    def json(self):
        raise ValueError("Invalid JSON")

class MissingDataResponse:
    status_code = 200
    def json(self):
        return {"error": "no data key"}

orig_httpx_get = module.httpx.get

if not hasattr(module.httpx, "ConnectError"):
    class ConnectError(module.httpx.HTTPError):
        pass
    module.httpx.ConnectError = ConnectError

try:
    # (i) non-200 response
    module.httpx.get = lambda *args, **kwargs: Non200Response()
    res = module._betterstack_get("monitors", secret_key)
    assert res is None

    # (ii) raise httpx.ConnectError whose str() contains the Authorization header value
    def raise_connect_error(*args, **kwargs):
        raise module.httpx.ConnectError(f"Failed to connect with Authorization: Bearer {secret_key}")
    module.httpx.get = raise_connect_error
    res = module._betterstack_get("monitors", secret_key)
    assert res is None

    # (iii) return non-JSON body
    module.httpx.get = lambda *args, **kwargs: NonJSONResponse()
    res = module._betterstack_get("monitors", secret_key)
    assert res is None

    # (iv) return a dict without a list "data"
    module.httpx.get = lambda *args, **kwargs: MissingDataResponse()
    res = module._betterstack_get("monitors", secret_key)
    assert res is None

    assert len(captured_logs) > 0
    for line in captured_logs:
        assert secret_key not in line, f"Secret leaked in log: {line}"
        assert "Bearer" not in line, f"Bearer leaked in log: {line}"
finally:
    module.log = orig_log
    module.httpx.get = orig_httpx_get


# --- 13. Foreign-host next and prefix-without-slash (item 7b) ---------------
orig_httpx_get = module.httpx.get
try:
    # 1. Foreign-host next
    captured_calls = []
    resp_evil_foreign = DummyResponse(
        [{"id": "1", "attributes": {"name": "m1"}}],
        "https://evil.example/api/v2/monitors",
    )
    def fake_get_evil(url, headers=None, params=None, timeout=None):
        captured_calls.append((url, headers))
        return resp_evil_foreign

    module.httpx.get = fake_get_evil
    res = module._betterstack_get("monitors", "test-key-b1")
    assert res == {"data": [{"id": "1", "attributes": {"name": "m1"}}]}
    assert len(captured_calls) == 1
    assert captured_calls[0][0] == f"{module.BETTERSTACK_API}/monitors"
    assert captured_calls[0][1]["Authorization"] == "Bearer test-key-b1"

    # 2. Prefix-without-slash host
    captured_calls.clear()
    resp_evil_prefix = DummyResponse(
        [{"id": "2", "attributes": {"name": "m2"}}],
        "https://uptime.betterstack.com/api/v2evil",
    )
    def fake_get_prefix(url, headers=None, params=None, timeout=None):
        captured_calls.append((url, headers))
        return resp_evil_prefix

    module.httpx.get = fake_get_prefix
    res = module._betterstack_get("monitors", "test-key-b2")
    assert res == {"data": [{"id": "2", "attributes": {"name": "m2"}}]}
    assert len(captured_calls) == 1
    assert captured_calls[0][0] == f"{module.BETTERSTACK_API}/monitors"
    assert captured_calls[0][1]["Authorization"] == "Bearer test-key-b2"
finally:
    module.httpx.get = orig_httpx_get


# --- 14. Pagination: self-next loop prevention and 5-page cap (item 7c) -----
orig_httpx_get = module.httpx.get
captured_logs = []
orig_log = module.log
module.log = lambda msg: captured_logs.append(str(msg))

try:
    # 1. Next equal to current URL
    captured_calls = []
    curr_url = f"{module.BETTERSTACK_API}/monitors"
    resp_self_loop = DummyResponse([{"id": "1", "attributes": {"name": "m1"}}], curr_url)
    def fake_get_self(url, headers=None, params=None, timeout=None):
        captured_calls.append(url)
        return resp_self_loop

    module.httpx.get = fake_get_self
    res = module._betterstack_get("monitors", "test-key-c1")
    assert res == {"data": [{"id": "1", "attributes": {"name": "m1"}}]}
    assert len(captured_calls) == 1

    # 2. 5-page cap fetches exactly 5 pages and logs truncation line
    captured_calls.clear()
    captured_logs.clear()
    def fake_get_pages(url, headers=None, params=None, timeout=None):
        captured_calls.append(url)
        page_num = len(captured_calls)
        next_page = f"{module.BETTERSTACK_API}/monitors?page={page_num + 1}"
        return DummyResponse([{"id": str(page_num), "attributes": {"name": f"m{page_num}"}}], next_page)

    module.httpx.get = fake_get_pages
    res = module._betterstack_get("monitors", "test-key-c2")
    assert len(captured_calls) == 5
    assert len(res["data"]) == 5
    assert any("Better Stack monitors: page limit reached, results truncated" in line for line in captured_logs)
finally:
    module.httpx.get = orig_httpx_get
    module.log = orig_log


# --- 15. Attribute edge cases, types, and fallback (item 7d) ----------------
data_monitors = [
    # null status -> maps to pending
    {"id": "m1", "type": "monitor", "attributes": {"name": "status-null", "status": None}},
    # null attributes with id -> fallback to id "m2", status pending
    {"id": "m2", "type": "monitor", "attributes": None},
    # null attributes without id -> fallback to "?", status pending
    {"type": "monitor", "attributes": None},
    # non-dict item -> skipped
    "this is not a dict",
    None,
    42,
    # type overriding default kind: in monitors list with type="heartbeat" -> kind="beat"
    {"id": "m3", "type": "heartbeat", "attributes": {"name": "typed-beat", "status": "up"}},
    # monitor name fallback (pronounceable_name missing -> name)
    {"id": "m4", "type": "monitor", "attributes": {"pronounceable_name": None, "name": "fallback-name", "status": "up"}},
    {"id": "m5", "type": "monitor", "attributes": {"pronounceable_name": "", "name": "fallback-name-empty-p", "status": "up"}},
]
data_heartbeats = [
    # type overriding default kind: in heartbeats list with type="monitor" -> kind="site"
    {"id": "h1", "type": "monitor", "attributes": {"name": "typed-site", "status": "up"}},
]

res_d = module.parse_uptime({"data": data_monitors}, {"data": data_heartbeats})
assert res_d["ok"] is True
by_name = {t["name"]: t for t in res_d["targets"]}

assert by_name["status-null"]["status"] == "pending"
assert by_name["m2"]["status"] == "pending"
assert by_name["?"]["status"] == "pending"
assert by_name["m2"]["kind"] == "site"
assert by_name["typed-beat"]["kind"] == "beat"
assert by_name["typed-site"]["kind"] == "site"
assert "fallback-name" in by_name
assert "fallback-name-empty-p" in by_name


# --- 16. Unnamed down item counted and listed with id (item 7e) -------------
monitors_unnamed_down = {
    "data": [
        {"id": "down-id-123", "type": "monitor", "attributes": {"status": "down"}},
        {"id": "up-id-456", "type": "monitor", "attributes": {"pronounceable_name": "up-monitor", "status": "up"}},
    ]
}
res_e = module.parse_uptime(monitors_unnamed_down, {"data": []})
assert res_e["ok"] is True
assert res_e["down"] == 1
down_targets = [t for t in res_e["targets"] if t["status"] == "down"]
assert len(down_targets) == 1
assert down_targets[0]["name"] == "down-id-123"
assert down_targets[0]["status"] == "down"


# --- 17. ageS staleness computation (item 7f) -------------------------------
base_payload = {
    "ok": True,
    "down": 0,
    "targets": [{"name": "srv", "kind": "site", "status": "up"}],
    "ts": 1700000000,
}
pushed = module._uptime_push_payload(base_payload, now=1700000042)
assert pushed["ageS"] == 42
assert "ts" not in pushed
assert "ageS" not in base_payload
assert base_payload["ts"] == 1700000000  # unmutated

# clock skew check (now < ts)
pushed_skew = module._uptime_push_payload(base_payload, now=1699999990)
assert pushed_skew["ageS"] == 0
assert "ts" not in pushed_skew


# --- 18. UnicodeDecodeError and XDG_CONFIG_HOME (item 7g) --------------------
with tempfile.TemporaryDirectory() as tmpdir:
    tmp_path = Path(tmpdir)
    bad_file = tmp_path / "bad_unicode.key"
    bad_file.write_bytes(b"\xff\xfe\x00")

    xdg_dir = tmp_path / "xdg"
    xdg_clawd = xdg_dir / "clawdmeter"
    xdg_clawd.mkdir(parents=True)
    xdg_key = xdg_clawd / "betterstack.key"
    xdg_key.write_text("xdg-token-value\n")

    orig_env = dict(os.environ)
    try:
        # UnicodeDecodeError returns ""
        os.environ["CLAWDMETER_BETTERSTACK_KEY_FILE"] = str(bad_file)
        os.environ.pop("CLAWDMETER_BETTERSTACK_KEY", None)
        assert module._resolve_betterstack_key() == ""

        # XDG_CONFIG_HOME honoured for default path
        os.environ.pop("CLAWDMETER_BETTERSTACK_KEY_FILE", None)
        os.environ["XDG_CONFIG_HOME"] = str(xdg_dir)
        assert module._resolve_betterstack_key() == "xdg-token-value"
    finally:
        os.environ.clear()
        os.environ.update(orig_env)


# --- 19. Unmatched target log line (item 7h) --------------------------------
captured_logs = []
orig_log = module.log
module.log = lambda msg: captured_logs.append(str(msg))

orig_betterstack_get = module._betterstack_get
orig_resolve_key = module._resolve_betterstack_key

try:
    module._resolve_betterstack_key = lambda: "dummy-key"
    module._betterstack_get = lambda resource, key: {
        "data": [{"id": "1", "type": "monitor", "attributes": {"pronounceable_name": "target-one", "status": "up"}}]
    } if resource == "monitors" else {"data": []}

    module._last_unmatched_targets = None
    module.do_uptime_poll(targets=["target-one", "target-two", "   "])

    unmatched_logs = [l for l in captured_logs if "target(s) matched nothing" in l]
    assert len(unmatched_logs) == 1
    assert "target-two" in unmatched_logs[0]
    assert "target-one" not in unmatched_logs[0]

    # Poll again with identical targets: no new log line produced
    module.do_uptime_poll(targets=["target-one", "target-two", "   "])
    unmatched_logs_2 = [l for l in captured_logs if "target(s) matched nothing" in l]
    assert len(unmatched_logs_2) == 1
finally:
    module.log = orig_log
    module._betterstack_get = orig_betterstack_get
    module._resolve_betterstack_key = orig_resolve_key

# --- 20. Unmatched-target log is NOT raised when the 8-target cap cut the list -
captured_logs = []
orig_log = module.log
module.log = lambda msg: captured_logs.append(str(msg))
orig_betterstack_get = module._betterstack_get
orig_resolve_key = module._resolve_betterstack_key
try:
    module._resolve_betterstack_key = lambda: "dummy-key"
    nine = [{"id": str(i), "type": "monitor", "attributes": {"pronounceable_name": f"srv-{i}", "status": "up"}} for i in range(9)]
    module._betterstack_get = lambda resource, key: {"data": nine} if resource == "monitors" else {"data": []}
    module._last_unmatched_targets = None
    wanted = [f"srv-{i}" for i in range(9)]            # all nine exist; payload is capped at 8
    module.do_uptime_poll(targets=wanted)
    assert not [l for l in captured_logs if "matched nothing" in l], "cap must not cause a false 'matched nothing'"
finally:
    module.log = orig_log
    module._betterstack_get = orig_betterstack_get
    module._resolve_betterstack_key = orig_resolve_key

# --- 21. Page-limit truncation is logged once per resource, re-armed after a clean run
captured_logs = []
orig_log = module.log
module.log = lambda msg: captured_logs.append(str(msg))
orig_get = module.httpx.get
try:
    api = module.BETTERSTACK_API
    class _R:
        status_code = 200
        def __init__(self, n): self._n = n
        def json(self):
            return {"data": [{"id": str(self._n)}], "pagination": {"next": f"{api}/monitors?page={self._n + 1}"}}
    module.httpx.get = lambda url, headers=None, params=None, timeout=None: _R(int(str(url).split("page=")[-1]) if "page=" in str(url) else 1)
    module._betterstack_truncated.clear()
    module._betterstack_get("monitors", "k")
    module._betterstack_get("monitors", "k")
    assert len([l for l in captured_logs if "page limit reached" in l]) == 1, "truncation must log once"
    class _Last(_R):
        def json(self): return {"data": [{"id": "x"}], "pagination": {"next": None}}
    module.httpx.get = lambda url, headers=None, params=None, timeout=None: _Last(1)
    module._betterstack_get("monitors", "k")           # clean run re-arms the one-shot log
    assert "monitors" not in module._betterstack_truncated
finally:
    module.log = orig_log
    module.httpx.get = orig_get

# --- 22. A relative XDG_CONFIG_HOME is ignored (XDG spec) ------------------------
orig_env = dict(os.environ)
try:
    os.environ.pop("CLAWDMETER_BETTERSTACK_KEY", None)
    os.environ.pop("CLAWDMETER_BETTERSTACK_KEY_FILE", None)
    with tempfile.TemporaryDirectory() as home:
        os.environ["HOME"] = home
        os.environ["XDG_CONFIG_HOME"] = "relative/dir"
        rel = Path("relative/dir/clawdmeter/betterstack.key")
        assert not rel.exists()
        (Path(home) / ".config" / "clawdmeter").mkdir(parents=True)
        (Path(home) / ".config" / "clawdmeter" / "betterstack.key").write_text("home-default-token\n")
        assert module._resolve_betterstack_key() == "home-default-token", "relative XDG must be ignored"
finally:
    os.environ.clear()
    os.environ.update(orig_env)

# --- 23. ageS uses a monotonic clock: a wall-clock step cannot change it --------
import time as _time
payload = {"ok": True, "down": 0, "targets": [], "ts": 1000}
assert module._uptime_push_payload(payload, now=1090)["ageS"] == 90
assert "ts" not in module._uptime_push_payload(payload, now=1090)
orig_time, orig_mono = _time.time, _time.monotonic
try:
    _time.time = lambda: 0.0                 # a wall clock stepped to 1970 must not matter
    _time.monotonic = lambda: 1123.0
    assert module._uptime_push_payload(payload)["ageS"] == 123, "default `now` must come from time.monotonic"
finally:
    _time.time, _time.monotonic = orig_time, orig_mono

# --- 24. Real regression: two 27-char names sharing their first 24 chars stay distinct
cmu = {"data": [
    {"id": "1", "type": "monitor", "attributes": {"pronounceable_name": "status.example.org/gdr111", "status": "up"}},
    {"id": "2", "type": "monitor", "attributes": {"pronounceable_name": "status.example.org/gdrbws", "status": "down"}},
]}
both = module.parse_uptime(cmu, {"data": []})
assert [t["name"] for t in both["targets"]] == ["status.example.org/gdrbws", "status.example.org/gdr111"], both
assert len({t["name"] for t in both["targets"]}) == 2, "names must stay distinct"
only = module.parse_uptime(cmu, {"data": []}, targets=["status.example.org/gdr111"])
assert [t["name"] for t in only["targets"]] == ["status.example.org/gdr111"], "filter must pick exactly one"
assert only["down"] == 0

# --- 25. DOWN target with open incident: cause and sinceS -------------------
ref_now = "2026-10-27T14:00:00Z"
m_down = {
    "data": [
        {
            "id": "m1",
            "type": "monitor",
            "attributes": {"pronounceable_name": "site-a", "status": "down", "created_at": "2026-10-01T00:00:00Z"},
        }
    ]
}
hb_down = {
    "data": [
        {
            "id": "h1",
            "type": "heartbeat",
            "attributes": {"name": "beat-a", "status": "down", "created_at": "2026-10-01T00:00:00Z"},
        }
    ]
}
incidents_down = {
    "data": [
        {
            "id": "inc-1",
            "type": "incident",
            "attributes": {
                "name": "Synthetic incident 1",
                "cause": "Status 502",
                "started_at": "2026-10-27T13:44:00Z",
                "resolved_at": None,
                "status": "Started",
            },
            "relationships": {"monitor": {"data": {"id": "m1", "type": "monitor"}}},
        },
        {
            "id": "inc-2",
            "type": "incident",
            "attributes": {
                "name": "Synthetic incident 2",
                "cause": "Missed heartbeat",
                "started_at": "2026-10-27T13:30:00Z",
                "resolved_at": None,
                "status": "Started",
            },
            "relationships": {"heartbeat": {"data": {"id": "h1", "type": "heartbeat"}}},
        },
    ]
}
res_down = module.parse_uptime(m_down, hb_down, incidents=incidents_down, now=ref_now)
assert res_down["ok"] is True
assert res_down["down"] == 2
targets_by_name = {t["name"]: t for t in res_down["targets"]}
assert targets_by_name["site-a"]["status"] == "down"
assert targets_by_name["site-a"]["cause"] == "Status 502"
assert targets_by_name["site-a"]["sinceS"] == 960  # 14:00:00 - 13:44:00 = 16m = 960s
assert targets_by_name["beat-a"]["status"] == "down"
assert targets_by_name["beat-a"]["cause"] == "Missed heartbeat"
assert targets_by_name["beat-a"]["sinceS"] == 1800  # 14:00:00 - 13:30:00 = 30m = 1800s

# DOWN target with NO open incident found -> cause null, sinceS null
incidents_empty = {"data": []}
res_down_no_inc = module.parse_uptime(m_down, {"data": []}, incidents=incidents_empty, now=ref_now)
assert res_down_no_inc["targets"][0]["status"] == "down"
assert res_down_no_inc["targets"][0]["cause"] is None
assert res_down_no_inc["targets"][0]["sinceS"] is None


# --- 26. UP target after a resolved incident: cause null, sinceS from resolved_at
m_up = {
    "data": [
        {
            "id": "m2",
            "type": "monitor",
            "attributes": {"pronounceable_name": "site-b", "status": "up", "created_at": "2026-10-01T00:00:00Z"},
        }
    ]
}
incidents_resolved = {
    "data": [
        {
            "id": "inc-res-1",
            "type": "incident",
            "attributes": {
                "name": "Recovered incident",
                "cause": "Status 500",
                "started_at": "2026-10-27T11:00:00Z",
                "resolved_at": "2026-10-27T11:43:00Z",
                "status": "Resolved",
            },
            "relationships": {"monitor": {"data": {"id": "m2", "type": "monitor"}}},
        },
        {
            # Older resolved incident for the same monitor (newest-first ordering)
            "id": "inc-res-old",
            "type": "incident",
            "attributes": {
                "name": "Older incident",
                "cause": "Connection timeout",
                "started_at": "2026-10-26T10:00:00Z",
                "resolved_at": "2026-10-26T10:30:00Z",
                "status": "Resolved",
            },
            "relationships": {"monitor": {"data": {"id": "m2", "type": "monitor"}}},
        },
    ]
}
res_up_resolved = module.parse_uptime(m_up, {"data": []}, incidents=incidents_resolved, now=ref_now)
assert res_up_resolved["ok"] is True
assert res_up_resolved["targets"][0]["status"] == "up"
assert res_up_resolved["targets"][0]["cause"] is None
# 14:00:00 - 11:43:00 = 2h 17m = 8220s
assert res_up_resolved["targets"][0]["sinceS"] == 8220


# --- 27. UP target with no incident: sinceS from created_at or null --------
m_up_created = {
    "data": [
        {
            "id": "m3",
            "type": "monitor",
            "attributes": {"pronounceable_name": "site-c", "status": "up", "created_at": "2026-10-27T12:00:00Z"},
        },
        {
            "id": "m4",
            "type": "monitor",
            "attributes": {"pronounceable_name": "site-d", "status": "up"},  # no created_at
        },
    ]
}
res_up_created = module.parse_uptime(m_up_created, {"data": []}, incidents={"data": []}, now=ref_now)
assert res_up_created["ok"] is True
targets_c = {t["name"]: t for t in res_up_created["targets"]}
# 14:00:00 - 12:00:00 = 2h = 7200s
assert targets_c["site-c"]["cause"] is None
assert targets_c["site-c"]["sinceS"] == 7200
# m4 has neither resolved incident nor created_at -> null
assert targets_c["site-d"]["cause"] is None
assert targets_c["site-d"]["sinceS"] is None

# Case where created_at is more recent than an ancient resolved incident: max(resolved_at, created_at)
incidents_ancient = {
    "data": [
        {
            "id": "inc-anc",
            "type": "incident",
            "attributes": {
                "name": "Ancient incident",
                "cause": "Ancient",
                "started_at": "2026-10-27T08:00:00Z",
                "resolved_at": "2026-10-27T08:30:00Z",
            },
            "relationships": {"monitor": {"data": {"id": "m3", "type": "monitor"}}},
        }
    ]
}
res_up_max = module.parse_uptime(m_up_created, {"data": []}, incidents=incidents_ancient, now=ref_now)
# created_at is 12:00:00, resolved_at is 08:30:00 -> max is 12:00:00 -> sinceS is 7200
assert res_up_max["targets"][0]["sinceS"] == 7200


# --- 28. Paused and pending targets: cause null, sinceS null ----------------
m_paused_pending = {
    "data": [
        {
            "id": "m5",
            "type": "monitor",
            "attributes": {"pronounceable_name": "site-e", "status": "paused", "created_at": "2026-10-01T00:00:00Z"},
        },
        {
            "id": "m6",
            "type": "monitor",
            "attributes": {"pronounceable_name": "site-f", "status": "maintenance", "created_at": "2026-10-01T00:00:00Z"},
        },
        {
            "id": "m7",
            "type": "monitor",
            "attributes": {"pronounceable_name": "site-g", "status": "validating", "created_at": "2026-10-01T00:00:00Z"},
        },
    ]
}
incidents_mixed = {
    "data": [
        {
            "id": "inc-5",
            "type": "incident",
            "attributes": {"cause": "Should be ignored", "started_at": "2026-10-27T13:00:00Z", "resolved_at": None},
            "relationships": {"monitor": {"data": {"id": "m5", "type": "monitor"}}},
        }
    ]
}
res_pp = module.parse_uptime(m_paused_pending, {"data": []}, incidents=incidents_mixed, now=ref_now)
for t in res_pp["targets"]:
    assert t["status"] in ("paused", "pending")
    assert t["cause"] is None, f"{t['name']} cause must be null"
    assert t["sinceS"] is None, f"{t['name']} sinceS must be null"


# --- 29. Incidents call failing: feed still works with cause/sinceS null ----
m_mixed = {
    "data": [
        {"id": "m1", "type": "monitor", "attributes": {"pronounceable_name": "site-down", "status": "down"}},
        {"id": "m2", "type": "monitor", "attributes": {"pronounceable_name": "site-up", "status": "up", "created_at": "2026-10-01T00:00:00Z"}},
    ]
}
# incidents=None simulates call failure
res_fail = module.parse_uptime(m_mixed, {"data": []}, incidents=None, now=ref_now)
assert res_fail["ok"] is True
assert res_fail["down"] == 1
assert len(res_fail["targets"]) == 2
for t in res_fail["targets"]:
    assert t["cause"] is None
    assert t["sinceS"] is None

# In poll_uptime when _betterstack_get("incidents", ...) returns None:
orig_betterstack_get = module._betterstack_get
orig_resolve_key = module._resolve_betterstack_key
try:
    module._resolve_betterstack_key = lambda: "dummy-key"
    def fake_get_inc_fail(resource, key):
        if resource == "monitors":
            return m_mixed
        if resource == "heartbeats":
            return {"data": []}
        if resource == "incidents":
            return None  # HTTP error / failure
        return None
    module._betterstack_get = fake_get_inc_fail
    res_poll_inc_fail = module.poll_uptime()
    assert res_poll_inc_fail["ok"] is True
    assert res_poll_inc_fail["down"] == 1
    assert all(t["cause"] is None for t in res_poll_inc_fail["targets"])
    assert all(t["sinceS"] is None for t in res_poll_inc_fail["targets"])
finally:
    module._betterstack_get = orig_betterstack_get
    module._resolve_betterstack_key = orig_resolve_key


# --- 30. Cause sanitising: printable ASCII, whitespace collapse, 40 cap, non-negative sinceS
# Helper direct unit tests
assert module._sanitise_cause("Missed heartbeat") == "Missed heartbeat"
assert module._sanitise_cause("  Status \n\t  502  ") == "Status 502"
assert module._sanitise_cause("Server error: \x00\x08\x1b[31mBad Gateway") == "Server error: [31mBad Gateway"
assert module._sanitise_cause("Non-ASCII \U0001f525 emoji \xe9") == "Non-ASCII emoji"
assert module._sanitise_cause("a" * 60) == "a" * 40
assert len(module._sanitise_cause("a" * 60)) == 40
assert module._sanitise_cause("") is None
assert module._sanitise_cause("   \t\n  ") is None
assert module._sanitise_cause("\x00\x01\x02") is None
assert module._sanitise_cause("\U0001f525\U0001f525\U0001f525") is None
assert module._sanitise_cause(None) is None

# End-to-end via parse_uptime with future timestamp (sinceS never negative)
m_future = {
    "data": [
        {"id": "mf", "type": "monitor", "attributes": {"pronounceable_name": "site-future", "status": "down"}}
    ]
}
inc_future = {
    "data": [
        {
            "id": "inc-fut",
            "type": "incident",
            "attributes": {
                "cause": "  Error  with   extra   spaces \t\n and \U0001f525  ",
                "started_at": "2026-10-27T14:00:10Z",  # 10s in the future relative to ref_now
                "resolved_at": None,
            },
            "relationships": {"monitor": {"data": {"id": "mf", "type": "monitor"}}},
        }
    ]
}
res_future = module.parse_uptime(m_future, {"data": []}, incidents=inc_future, now=ref_now)
assert res_future["targets"][0]["cause"] == "Error with extra spaces and"
assert res_future["targets"][0]["sinceS"] == 0, "sinceS must never be negative"


# --- 31. No incident URL or internal fields leak into the payload ------------
m_leak_check = {
    "data": [
        {
            "id": "m-leak",
            "type": "monitor",
            "attributes": {
                "pronounceable_name": "site-leak-check",
                "status": "down",
                "url": "https://secret.monitor.example.com",
            },
        }
    ]
}
inc_leak = {
    "data": [
        {
            "id": "inc-leak-id",
            "type": "incident",
            "attributes": {
                "name": "Incident Secret Leak Test",
                "cause": "Status 502",
                "started_at": "2026-10-27T13:50:00Z",
                "resolved_at": None,
                "status": "Started",
                "url": "https://uptime.betterstack.com/incidents/SECRET-INCIDENT-URL-9999",
                "response_url": "https://uptime.betterstack.com/incidents/SECRET-RESPONSE-URL-8888",
                "screenshot_url": "https://uptime.betterstack.com/incidents/SECRET-SCREENSHOT-URL-7777",
                "extra_private_field": "SECRET-PRIVATE-FIELD-VALUE",
            },
            "relationships": {"monitor": {"data": {"id": "m-leak", "type": "monitor"}}},
        }
    ]
}
res_leak = module.parse_uptime(m_leak_check, {"data": []}, incidents=inc_leak, now=ref_now)
dumped_payload = json.dumps(res_leak)
assert "SECRET-INCIDENT-URL-9999" not in dumped_payload
assert "SECRET-RESPONSE-URL-8888" not in dumped_payload
assert "SECRET-SCREENSHOT-URL-7777" not in dumped_payload
assert "SECRET-PRIVATE-FIELD-VALUE" not in dumped_payload
assert "secret.monitor.example.com" not in dumped_payload

# Verify each target dict strictly contains only the allowed public keys
for target in res_leak["targets"]:
    assert set(target.keys()) == {"name", "kind", "status", "sinceS", "cause"}


# --- 32. poll_uptime requests incidents with 1-page cap and handles truncation
captured_calls = []
captured_logs = []
orig_log = module.log
module.log = lambda msg: captured_logs.append(str(msg))
orig_betterstack_get = module._betterstack_get
orig_resolve_key = module._resolve_betterstack_key

try:
    module._resolve_betterstack_key = lambda: "dummy-key"

    def fake_get_accounting(resource, key):
        captured_calls.append(resource)
        if resource == "monitors":
            return {"data": [{"id": "m1", "type": "monitor", "attributes": {"name": "site-a", "status": "up"}}]}
        if resource == "heartbeats":
            return {"data": []}
        if resource == "incidents":
            return {"data": []}
        return None

    module._betterstack_get = fake_get_accounting
    res_account = module.poll_uptime()
    assert res_account["ok"] is True
    # Verify exactly one request for monitors, one for heartbeats, one for incidents
    assert captured_calls == ["monitors", "heartbeats", "incidents"], captured_calls

    # Verify _betterstack_get default max_pages for incidents is 1 with truncation logging
    orig_httpx_get = module.httpx.get
    try:
        class _IncResponse:
            status_code = 200
            def __init__(self, page):
                self._page = page
            def json(self):
                return {
                    "data": [{"id": f"inc-{self._page}"}],
                    "pagination": {"next": f"{module.BETTERSTACK_API}/incidents?page={self._page + 1}"},
                }

        inc_calls = []
        def fake_httpx_inc(url, headers=None, params=None, timeout=None):
            inc_calls.append(url)
            page = 1 if "page=" not in str(url) else int(str(url).split("page=")[-1])
            return _IncResponse(page)

        module.httpx.get = fake_httpx_inc
        module._betterstack_get = orig_betterstack_get
        module._betterstack_truncated.clear()

        res_inc_get = module._betterstack_get("incidents", "dummy-key")
        assert len(inc_calls) == 1, "incidents must only fetch 1 page"
        assert len(res_inc_get["data"]) == 1
        assert any("Better Stack incidents: page limit reached, results truncated" in l for l in captured_logs)
    finally:
        module.httpx.get = orig_httpx_get
finally:
    module.log = orig_log
    module._betterstack_get = orig_betterstack_get
    module._resolve_betterstack_key = orig_resolve_key

print("ok")
