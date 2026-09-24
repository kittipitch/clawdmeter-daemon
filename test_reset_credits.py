"""Regression checks for manually transcribed Claude reset-credit promos."""

import importlib
import sys
import types
from datetime import datetime

try:
    module = importlib.import_module("clawdmeter_daemon")
except ModuleNotFoundError:
    # This test reaches only the pure parser. Minimal import shims keep it runnable
    # on a fresh machine before optional runtime dependencies are installed.
    sys.modules.pop("clawdmeter_daemon", None)
    httpx = types.ModuleType("httpx")
    httpx.HTTPError = Exception
    sys.modules.setdefault("httpx", httpx)
    sys.modules.setdefault("aqi", types.ModuleType("aqi"))
    module = importlib.import_module("clawdmeter_daemon")

_parse_reset_credits = module._parse_reset_credits

now = datetime(2026, 9, 24, 12, 0)

assert _parse_reset_credits("", now) == {}
assert _parse_reset_credits(None, now) == {}
assert _parse_reset_credits("0", now) == {}
assert _parse_reset_credits("1", now) == {"resetCredits": 1}

expiry = _parse_reset_credits("1@2026-10-22", now)
assert expiry["resetCredits"] == 1
assert expiry["resetCreditExpireMins"] == 40320

assert _parse_reset_credits("1@2026-01-01", now) == {}
assert _parse_reset_credits("2@2026-10-22", now)["resetCredits"] == 2
assert _parse_reset_credits("x@y", now) == {}
assert _parse_reset_credits("1@not-a-date", now) == {}
assert _parse_reset_credits("@2026-10-22", now) == {}

print("ok")
