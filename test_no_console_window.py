"""Every child process must go through _run()/_popen() so Windows gets CREATE_NO_WINDOW.

A direct subprocess.run/Popen under pythonw flashes a black console window on every poll.
"""

import importlib
import re
import subprocess
import sys
import types
from pathlib import Path

try:
    module = importlib.import_module("clawdmeter_daemon")
except ModuleNotFoundError:
    sys.modules.pop("clawdmeter_daemon", None)
    httpx = types.ModuleType("httpx")
    httpx.HTTPError = Exception
    sys.modules.setdefault("httpx", httpx)
    sys.modules.setdefault("aqi", types.ModuleType("aqi"))
    module = importlib.import_module("clawdmeter_daemon")

# 1. No spawn bypasses the helpers (the only direct calls are inside them).
src = Path(module.__file__).read_text(encoding="utf-8")
direct = re.findall(r"subprocess\.(?:run|Popen|call|check_call|check_output)\(|os\.(?:system|popen)\(", src)
assert direct == ["subprocess.run(", "subprocess.Popen("], f"spawn bypasses _run/_popen: {direct}"

# 2. On Windows both helpers add CREATE_NO_WINDOW and keep caller flags.
seen = {}
real_run, real_popen = subprocess.run, subprocess.Popen
subprocess.run = lambda cmd, **kw: seen.setdefault("run", kw)
subprocess.Popen = lambda cmd, **kw: seen.setdefault("popen", kw)
module._NO_WINDOW = 0x08000000
try:
    module._run(["x"], creationflags=0x200)
    module._popen(["x"])
finally:
    subprocess.run, subprocess.Popen = real_run, real_popen
assert seen["run"]["creationflags"] == 0x08000200, seen
assert seen["popen"]["creationflags"] == 0x08000000, seen

print("test_no_console_window: OK")
