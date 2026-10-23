"""Regression checks for the quota windows the device shows: the Antigravity
`/quota` parser and the Codex long-window length. Runs with no network and no agy."""

import importlib
import sys
import time
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

parse = module.parse_agy_quota
NOW = 1_800_000_000.0
IN = lambda mins: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW + mins * 60))


def doc(*groups):
    return {"command": {"name": "usage", "data": {"groups": list(groups)}}}


def bucket(window, frac, mins=None):
    b = {"window": window, "remaining_fraction": frac}
    if mins is not None:
        b["reset_time"] = IN(mins)
    return b


# --- new plan, as `agy -p /quota` printed it on 2026-10-18 (agy 1.4.2) ---------
new_plan = doc(
    {"name": "Gemini Models", "buckets": [bucket("weekly", 0.9995788, 10077), bucket("5h", 0.9985259, 297)]},
    {"name": "Claude and GPT models", "buckets": [bucket("weekly", 0.8328, 500), bucket("5h", 1)]},
    {"name": "Gemini 4 Argon", "buckets": [bucket("weekly", 0.9887, 10080)]},
)
p = parse(new_plan, NOW)
assert p == {"ok": True, "pctPro": 0, "labelPro": "5h", "rPro": 297,
             "pctFlash": 0, "labelFlash": "7d", "rFlash": 10077,
             "pctArgon": 1, "labelArgon": "7d", "rArgon": 10080,
             "tagArgon": "Ar", "tagFlash": "Fl"}, p  # 5h top; halves "7d ... Fl" | "7d ... Ar" below

# --- no Argon group: no Argon keys at all (never a fallback guess), 5h/7d as before ---------
p = parse(doc({"name": "Gemini Models", "buckets": [bucket("weekly", 0.9995788, 10077), bucket("5h", 0.9985259, 297)]}), NOW)
assert p["labelFlash"] == "7d" and p["pctFlash"] == 0 and not any("Argon" in k for k in p), p
assert "tagFlash" not in p, p  # a whole bottom card has no model tag

# --- Argon exhausted, as live on 2026-10-19: 100% used, reset carried ------------------------
p = parse(doc({"name": "Gemini Models", "buckets": [bucket("weekly", 0.9975751, 8280), bucket("5h", 1, 297)]},
              {"name": "Gemini 4 Argon", "buckets": [bucket("weekly", 0, 8280)]}), NOW)
assert (p["pctArgon"], p["labelArgon"], p["rArgon"], p["pctFlash"]) == (100, "7d", 8280, 0), p

# --- Argon as the ONLY usable group: it is the main group (5h/7d), no separate Argon keys ----
p = parse(doc({"name": "Gemini 4 Argon", "buckets": [bucket("weekly", 0.5, 9000), bucket("5h", 0.25, 60)]}), NOW)
assert (p["pctPro"], p["labelPro"], p["pctFlash"], p["labelFlash"]) == (75, "5h", 50, "7d"), p
assert not any("Argon" in k for k in p) and "tagFlash" not in p, p

# bucket order in the response must not matter, and a used quota maps to used %
shuffled = doc({"name": "Gemini Models", "buckets": [bucket("5h", 0.25, 60), bucket("weekly", 0.5, 9000)]})
p = parse(shuffled, NOW)
assert (p["pctPro"], p["labelPro"], p["pctFlash"], p["labelFlash"]) == (75, "5h", 50, "7d"), p

# the non-Gemini group is used only when no Gemini group exists
only_other = doc({"name": "Claude and GPT models", "buckets": [bucket("weekly", 0.5, 9000), bucket("5h", 1, 30)]})
assert parse(only_other, NOW)["labelPro"] == "5h"

# --- other plans: one window only, a monthly window, a window name we have not seen ---
one = parse(doc({"name": "Gemini Models", "buckets": [bucket("monthly", 0.9, 43000)]}), NOW)
assert one == {"ok": True, "pctPro": 10, "labelPro": "30d", "rPro": 43000}, one  # bottom card left absent
assert parse(doc({"name": "g", "buckets": [bucket("3d", 0.4, 100), bucket("daily", 0.6, 50)]}), NOW)["labelFlash"] == "3d"

# --- degenerate input never raises and never reports ok ---------------------------
assert parse({}, NOW) == {"ok": False}
assert parse(None, NOW) == {"ok": False}
assert parse(doc(), NOW) == {"ok": False}
assert parse(doc({"name": "Gemini Models", "buckets": [{"window": "weekly"}]}), NOW) == {"ok": False}  # disabled bucket
assert parse(doc({"name": "Gemini Models", "buckets": [bucket("fortnightly", 0.5, 10)]}), NOW) == {"ok": False}
assert parse(doc({"name": "Gemini Models", "buckets": [bucket("5h", 0.5)]}), NOW) == {"ok": True, "pctPro": 50, "labelPro": "5h"}  # no reset_time
assert parse(doc({"name": "Gemini Models", "buckets": [bucket("5h", 0.5, -30)]}), NOW)["rPro"] == 0  # reset already passed

# --- an agy whose JSON lacks command.data: rebuild the groups from the TSV `response` ---
tsv = ("Gemini Models\tWeekly Limit Remaining\t100%\t" + IN(10077) + "\n"
       "Gemini Models\tFive Hour Limit Remaining\t75%\t" + IN(297) + "\n"
       "Claude and GPT models\tWeekly Limit Remaining\t83%\t" + IN(500) + "\n"
       "Gemini 4 Argon\tGemini 4 Argon Weekly Limit\t99%\t" + IN(9000) + "\n")
p = parse({"response": tsv}, NOW)
assert (p["pctPro"], p["labelPro"], p["rPro"], p["pctFlash"], p["labelFlash"]) == (25, "5h", 297, 0, "7d"), p
assert (p["pctArgon"], p["labelArgon"], p["tagArgon"], p["tagFlash"]) == (1, "7d", "Ar", "Fl"), p
assert parse({"response": "just a model answer, no tabs"}, NOW) == {"ok": False}

# --- Copilot: independent Chat/Completions monthly allowances --------------------
module._copilot_quota_call = lambda: {
    "quota_reset_date_utc": IN(10077),
    "quota_snapshots": {
        "chat": {"has_quota": True, "percent_remaining": 75.4},
        "completions": {"has_quota": True, "percent_remaining": 0},
        "premium_interactions": {"has_quota": False, "percent_remaining": 100},
    },
}
p = module.poll_copilot(NOW)
assert p == {"ok": True, "pctChat": 25, "rChat": 10077,
             "pctCompletions": 100, "rCompletions": 10077}, p

module._copilot_quota_call = lambda: {
    "quota_reset_date": "2027-02-01",
    "quota_snapshots": {"chat": {"has_quota": True, "percent_remaining": 100}},
}
p = module.poll_copilot(1_800_000_000.0)
assert p["pctChat"] == 0 and p["rChat"] > 0 and "pctCompletions" not in p, p

module._copilot_quota_call = lambda: {
    "quota_snapshots": {"premium_interactions": {"has_quota": False, "percent_remaining": 100}},
}
assert module.poll_copilot(NOW) == {"ok": False}

# --- the version gate: modern agy never reaches the paid prompt path -----------------
calls = []
module._poll_antigravity_quota = lambda: calls.append("quota") or {"ok": False, "via": "quota"}
real_which = module.shutil.which
module.shutil.which = lambda name: calls.append("legacy") if name == "agy" else real_which(name)  # None: agy "missing"
try:
    for ver, want in (((1, 4, 2), "quota"), ((1, 1, 11), "quota"), ((1, 1, 10), "legacy"), (None, "legacy")):
        calls.clear()
        module._agy_version = lambda v=ver: v
        r = module.poll_antigravity()
        assert calls[0] == want, (ver, calls)
        if want == "quota":
            assert r.get("via") == "quota" and calls == ["quota"], (ver, calls, r)  # no legacy call after a failure
finally:
    module.shutil.which = real_which

# --- Codex: the long window's real length is sent, so a free plan can be labelled ----
def codex(primary, secondary=None):
    rl = {"primary": primary, "secondary": secondary}
    module._codex_rpc_call = lambda: {"rateLimits": rl}
    return module.poll_codex()

free = codex({"usedPercent": 23, "windowDurationMins": 43200, "resetsAt": time.time() + 43140 * 60})
assert (free["pctWeek"], free["weekMins"]) == (23, 43200) and "pct5h" not in free, free
pro = codex({"usedPercent": 40, "windowDurationMins": 10080, "resetsAt": time.time() + 600 * 60})
assert pro["weekMins"] == 10080 and "pct5h" not in pro, pro
plus = codex({"usedPercent": 40, "windowDurationMins": 10080, "resetsAt": time.time() + 600 * 60},
             {"usedPercent": 10, "windowDurationMins": 300, "resetsAt": time.time() + 60 * 60})
assert (plus["pct5h"], plus["pctWeek"], plus["weekMins"]) == (10, 40, 10080), plus
nodur = codex({"usedPercent": 5, "resetsAt": time.time() + 600 * 60})  # no duration: no label claim
assert "weekMins" not in nodur and nodur["pctWeek"] == 5, nodur

print("ok")
