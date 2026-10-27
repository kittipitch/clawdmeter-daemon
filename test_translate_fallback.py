"""A non-English title that cannot be translated shows "Not translated", never leftover glyphs."""

import importlib
import subprocess
import sys
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

module._save_translate_cache = lambda cache: None
module.TRANSLATE_CALL_SPACING_SEC = 0
module.TRANSLATE_RETRY_SEC = 0
LABEL = module.TRANSLATE_FAILED_LABEL
assert len(LABEL) <= 16, "must fit the agenda card's 16-char title"
real_which, real_run = module.shutil.which, module._run

def fresh():
    module._translate_cache.clear(); module._translate_fail_until.clear()

try:
    fresh(); module.shutil.which = lambda name: None                       # trans not installed
    assert module._translate_to_english("ประชุม") == LABEL
    module.shutil.which = lambda name: "/usr/bin/trans"
    fresh(); module._run = lambda *a, **k: types.SimpleNamespace(stdout="\x1b[1m[ERROR] Failed to extract IG.\x1b[0m\n")
    assert module._translate_to_english("ประชุม") == LABEL, "an [ERROR] line is not a title"
    assert module._translate_to_english("ประชุม") == LABEL, "inside the retry backoff"
    fresh()
    def boom(*a, **k): raise subprocess.TimeoutExpired("trans", 15)
    module._run = boom
    assert module._translate_to_english("ประชุม") == LABEL
    calls = []
    def flaky(*a, **k):
        calls.append(1); return types.SimpleNamespace(stdout="\n" if len(calls) == 1 else "Meeting\n")
    fresh(); module._run = flaky
    assert module._translate_to_english("ประชุม") == "Meeting" and len(calls) == 2, "one refused call is retried"
    fresh(); module._run = lambda *a, **k: types.SimpleNamespace(stdout="Meeting\n")
    assert module._translate_to_english("ประชุม") == "Meeting"
finally:
    module.shutil.which, module._run = real_which, real_run
print("test_translate_fallback: OK")
