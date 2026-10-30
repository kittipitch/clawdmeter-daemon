"""Every text file the daemon reads or writes is UTF-8, whatever the OS locale.

Without encoding="utf-8", Python uses the locale codec: cp874 on a Thai-locale Windows box, which cannot hold
Japanese, so saving the translate cache raised UnicodeEncodeError for every non-Latin title (found
2026-10-31 on the Windows host by tools/windows_console_check.py).
"""
import importlib
import pathlib
import re
import sys
import tempfile
import types
from pathlib import Path

try:
    d = importlib.import_module("clawdmeter_daemon")
except ModuleNotFoundError:
    sys.modules.pop("clawdmeter_daemon", None)
    httpx = types.ModuleType("httpx")
    httpx.HTTPError = Exception
    sys.modules.setdefault("httpx", httpx)
    sys.modules.setdefault("aqi", types.ModuleType("aqi"))
    d = importlib.import_module("clawdmeter_daemon")
d.LOG_PATH = Path(tempfile.mkdtemp()) / "test.log"     # CONTRIBUTING.md rule 3

# 1. Static guard: no text read/write/open without an explicit encoding (binary modes are fine).
for name in ("clawdmeter_daemon.py", "movies.py"):
    src = (Path(__file__).parent / name).read_text(encoding="utf-8")
    bad = []
    for n, line in enumerate(src.splitlines(), 1):
        code = line.split("#", 1)[0]
        for m in re.finditer(r"\b(read_text|write_text)\(|(?<![\w.])open\(", code):
            rest = code[m.start():]
            if "encoding=" in rest or re.search(r"""["'][rwa]\+?b\+?["']""", rest):
                continue
            bad.append(f"{name}:{n}: {line.strip()}")
    assert not bad, "text I/O without encoding=\"utf-8\":\n" + "\n".join(bad)

# 2. Behaviour: with a locale codec that cannot hold Japanese, the translate cache still round-trips.
real_write, real_read = pathlib.Path.write_text, pathlib.Path.read_text
pathlib.Path.write_text = lambda self, data, encoding=None, errors=None, newline=None: \
    real_write(self, data, encoding=encoding or "cp874", errors=errors, newline=newline)
pathlib.Path.read_text = lambda self, encoding=None, errors=None: \
    real_read(self, encoding=encoding or "cp874", errors=errors)
try:
    d.TRANSLATE_CACHE_PATH = Path(tempfile.mkdtemp()) / "translate.json"
    d._save_translate_cache({"こんにちは": "Hello"})
    assert d._load_translate_cache() == {"こんにちは": "Hello"}
finally:
    pathlib.Path.write_text, pathlib.Path.read_text = real_write, real_read
# 3. A file an older daemon wrote in the locale codec (cp874 holds Thai) still loads: strict UTF-8 crashed the
#    daemon at import on the Windows host, whose translate cache held Thai titles.
legacy = Path(tempfile.mkdtemp()) / "translate.json"
legacy.write_bytes('{"สวัสดี": "Hello"}'.encode("cp874"))
d.TRANSLATE_CACHE_PATH = legacy
import locale
real_pref = locale.getpreferredencoding
locale.getpreferredencoding = lambda do_setlocale=True: "cp874"
try:
    assert d._load_translate_cache() == {"สวัสดี": "Hello"}, d._load_translate_cache()
finally:
    locale.getpreferredencoding = real_pref
print("test_text_encoding: OK")
