"""A known non-English title gets one fixed English name on every host, with no translator call at all.

Bing's romanization of the same Thai title changes between calls, and each host caches the first draw it
got, so three hosts showed three spellings (kitt40 report). TRANSLATE_OVERRIDES wins over `trans`, over the
per-host cache, and over a missing `trans`."""

import importlib
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

# Keep this test's log lines out of the real ~/.clawdmeter-daemon.log (CONTRIBUTING.md rule 3).
import pathlib as _pathlib, tempfile as _tempfile  # noqa: E402
module.LOG_PATH = _pathlib.Path(_tempfile.mkdtemp()) / "test.log"

module._save_translate_cache = lambda cache: None
module.TRANSLATE_CALL_SPACING_SEC = 0
module.TRANSLATE_RETRY_SEC = 0
real_which, real_run = module.shutil.which, module._run

MARKET = "REDACTED"
calls = []

def spy(*a, **k):
    calls.append(a); return types.SimpleNamespace(stdout="REDACTED\n")

try:
    module._translate_cache.clear(); module._translate_fail_until.clear()
    module.shutil.which = lambda name: "/usr/bin/trans"; module._run = spy
    assert module._translate_to_english(MARKET) == "REDACTED"
    assert not calls, "an override must not call the translator"
    module._translate_cache[MARKET] = "REDACTED"          # a stale per-host draw
    assert module._translate_to_english(MARKET) == "REDACTED", "override beats the cache"
    module.shutil.which = lambda name: None                        # trans not installed
    assert module._translate_to_english(MARKET) == "REDACTED", "override works without trans"
    assert module._translate_to_english("ประชุม") == module.TRANSLATE_FAILED_LABEL, "other titles still use trans"
    assert all(len(v) <= 20 for v in module.TRANSLATE_OVERRIDES.values()), "keep names short enough to draw"
finally:
    module.shutil.which, module._run = real_which, real_run
print("test_translate_override: OK")
