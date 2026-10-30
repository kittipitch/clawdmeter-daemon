"""movies_tick pushes one movie an hour to each unit through the device contract, using the unit's
own Movies settings, and never advances a unit it could not reach."""
import json
import sys
import tempfile
import threading
import types
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.modules.setdefault("aqi", types.ModuleType("aqi"))
import clawdmeter_daemon as d  # noqa: E402
import httpx  # noqa: E402
import movies  # noqa: E402

d.LOG_PATH = Path(tempfile.mkdtemp()) / "test.log"   # CONTRIBUTING rule: never the real daemon log
FX = Path(__file__).parent / "fixtures"
JPEG = (FX / "poster_clayface_160x240.jpg").read_bytes()
PAGES = {k: (FX / f"rt_{k}.html").read_text(encoding="utf-8") for k in ("theaters", "home")}


def fake_fetch(url):
    for k, u in movies.SOURCES.items():
        if url == u:
            return PAGES[k].encode()
    if "/m/" in url and "flixster" not in url:
        return b'<rt-text data-qa="item-label">Release Date (Theaters)</rt-text> <rt-text data-qa="item-value">Oct 1, 2026</rt-text>'
    return JPEG


class Unit:
    """A fake SmallTV: /api/config, POST /api/movie (multipart frame) and /api/movie/promote."""

    def __init__(self, movies_on=True, theaters=True, home=True):
        self.cfg = {"carouselMovies": movies_on, "moviesTheaters": theaters, "moviesHome": home}
        self.frames, self.promotes, self.next = [], 0, None
        unit = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                b = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                self._send(200, unit.cfg) if self.path == "/api/config" else self._send(404, {})

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if self.path == "/api/movie":
                    i = raw.find(b"\r\n\r\n") + 4
                    j = raw.rfind(b"\r\n--")
                    frame = raw[i:j]
                    if len(frame) != 115200:
                        return self._send(400, {"ok": False})
                    unit.frames.append(frame)
                    unit.next = frame
                    return self._send(200, {"ok": True, "has": 1})
                if self.path == "/api/movie/promote":
                    if unit.next is None:
                        return self._send(409, {"ok": False})
                    unit.promotes += 1
                    unit.next = None
                    return self._send(200, {"ok": True})
                self._send(404, {})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/api/usage"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


T0 = datetime(2026, 10, 31, 12, 0).timestamp()
cache = movies.MovieCache(Path(tempfile.mkdtemp()), fake_fetch, lambda: T0)
cache.refresh_lists(force=True)
order = [t.slug for t, _ in cache.entries({"theaters", "home"})]

# First tick: push, then promote; the state records what is on screen.
u = Unit()
state = d.movies_tick(cache, [u.url], {}, T0, httpx)
assert len(u.frames) == 1 and u.promotes == 1, (len(u.frames), u.promotes)
assert state[u.url]["rotation"]["current"] == order[0]
assert state[u.url]["rotation"]["shown_at"] == T0

# Ten minutes later: nothing.
state = d.movies_tick(cache, [u.url], state, T0 + 600, httpx)
assert len(u.frames) == 1

# An hour later: the next movie.
state = d.movies_tick(cache, [u.url], state, T0 + 3600, httpx)
assert len(u.frames) == 2 and u.promotes == 2
assert state[u.url]["rotation"]["current"] == order[1]

# An identical frame is not pushed again (one-movie list, new round, same picture).
one = movies.MovieCache(Path(tempfile.mkdtemp()), fake_fetch, lambda: T0)
one.refresh_lists(force=True)
only = one.entries({"theaters"})[0][0].slug
one._lists["theaters"] = [x for x in one._lists["theaters"] if x["slug"] == only]
one._lists["home"] = []
u1 = Unit(home=False)
s1 = d.movies_tick(one, [u1.url], {}, T0, httpx)
s1 = d.movies_tick(one, [u1.url], s1, T0 + 3600, httpx)
assert len(u1.frames) == 1, len(u1.frames)
assert s1[u1.url]["rotation"]["shown_at"] == T0 + 3600       # still counted as shown this hour

# A unit that is off for three hours does not advance, then shows the very next movie.
u2 = Unit()
s2 = d.movies_tick(cache, [u2.url], {}, T0, httpx)
u2.stop()
for h in (1, 2, 3):
    s2 = d.movies_tick(cache, [u2.url], s2, T0 + h * 3600, httpx)
assert s2[u2.url]["rotation"]["current"] == order[0] and s2[u2.url]["rotation"]["shown_at"] == T0
u3 = Unit()                                                     # back, at a new address in this test
s2[u3.url] = s2.pop(u2.url)
s2 = d.movies_tick(cache, [u3.url], s2, T0 + 4 * 3600, httpx)
assert s2[u3.url]["rotation"]["current"] == order[1]

# Movies off, or both sources off: nothing pushed, nothing recorded.
for off in (Unit(movies_on=False), Unit(theaters=False, home=False)):
    s = d.movies_tick(cache, [off.url], {}, T0, httpx)
    assert off.frames == [] and off.promotes == 0 and off.url not in s

# Two units, different sources, separate rotations in one state.
ua, ub = Unit(home=False), Unit(theaters=False)
s = d.movies_tick(cache, [ua.url, ub.url], {}, T0, httpx)
assert s[ua.url]["rotation"]["current"] == cache.entries({"theaters"})[0][0].slug
assert s[ub.url]["rotation"]["current"] == cache.entries({"home"})[0][0].slug
print("test_movies_push: OK")
