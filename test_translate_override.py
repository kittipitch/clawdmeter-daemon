"""A title listed in the owner's local override file gets that fixed English name, with no translator call.

Bing romanizes the same title differently from call to call and each host caches its first draw, so hosts can
disagree. ~/.clawdmeter-translate-overrides.json ({"title": "English"}) wins over `trans`, over the per-host
cache and over a missing `trans`. The table itself is private and never in the repo; this test uses a made-up title."""

import importlib
import json
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
_tmp = _pathlib.Path(_tempfile.mkdtemp())
module.LOG_PATH = _tmp / "test.log"
module.TRANSLATE_OVERRIDES_PATH = _tmp / "overrides.json"      # never the owner's real file

module._save_translate_cache = lambda cache: None
module.TRANSLATE_CALL_SPACING_SEC = 0
module.TRANSLATE_RETRY_SEC = 0
real_which, real_run = module.shutil.which, module._run

TITLE = "ทดสอบ"        # made-up test title
calls = []

def spy(*a, **k):
    calls.append(a); return types.SimpleNamespace(stdout="Machine Draw\n")

try:
    module._translate_cache.clear(); module._translate_fail_until.clear()
    module.shutil.which = lambda name: "/usr/bin/trans"; module._run = spy

    assert module._load_translate_overrides() == {}, "no file means no overrides"
    module.TRANSLATE_OVERRIDES_PATH.write_text("{not json", encoding="utf-8")
    assert module._load_translate_overrides() == {}, "a broken file means no overrides"
    module.TRANSLATE_OVERRIDES_PATH.write_text(json.dumps({TITLE: "Fixed Name", "x": 5}), encoding="utf-8")
    assert module._load_translate_overrides() == {TITLE: "Fixed Name"}, "non-string entries are ignored"

    assert module._translate_to_english(TITLE) == "Fixed Name"
    assert not calls, "an override must not call the translator"
    module._translate_cache[TITLE] = "Stale Draw"                  # a stale per-host draw
    assert module._translate_to_english(TITLE) == "Fixed Name", "override beats the cache"
    module.shutil.which = lambda name: None                        # trans not installed
    assert module._translate_to_english(TITLE) == "Fixed Name", "override works without trans"
    assert module._translate_to_english("ประชุม") == module.TRANSLATE_FAILED_LABEL, "other titles still use trans"
finally:
    module.shutil.which, module._run = real_which, real_run
print("test_translate_override: OK")
