"""movies_tick interleaves Rotten Tomatoes and MyAnimeList hour by hour per unit, using each unit's anime
settings; a unit with only anime ticked still gets anime, and old firmware/old state keep working."""
import json
import sys
import tempfile
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.modules.setdefault("aqi", types.ModuleType("aqi"))
import clawdmeter_daemon as d  # noqa: E402
import httpx  # noqa: E402
import anime  # noqa: E402
import movies  # noqa: E402
from test_anime_cache import JPEG, T0, Net  # noqa: E402

d.LOG_PATH = Path(tempfile.mkdtemp()) / "test.log"   # CONTRIBUTING rule: never the real daemon log
FX = Path(__file__).parent / "fixtures"
RT = {k: (FX / f"rt_{k}.html").read_bytes() for k in ("theaters", "home")}
HOUR = 3600


def rt_fetch(url):
    for k, u in movies.SOURCES.items():
        if url == u:
            return RT[k]
    if "/m/" in url and "flixster" not in url:
        return b'<rt-text data-qa="item-label">Release Date (Theaters)</rt-text> <rt-text data-qa="item-value">Oct 1, 2026</rt-text>'
    return JPEG


class Unit:
    """A fake SmallTV: /api/config, POST /api/movie and /api/movie/promote; records each frame's source byte."""

    def __init__(self, **cfg):
        self.cfg = {"carouselMovies": True, "moviesTheaters": True, "moviesHome": True, **cfg}
        self.sources, self.titles = [], []
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
                    frame = raw[raw.find(b"\r\n\r\n") + 4:raw.rfind(b"\r\n--")]
                    t = frame[movies.POSTER_BYTES:]
                    unit.sources.append(t[6])
                    unit.titles.append(t[8:8 + t[7]].decode())
                return self._send(200, {"ok": True})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/api/usage"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


rt = movies.MovieCache(Path(tempfile.mkdtemp()), rt_fetch, lambda: T0)
rt.refresh_lists(force=True)
mal = anime.AnimeCache(tempfile.mkdtemp(), Net(), lambda: T0)
mal.refresh()
ANIME_ON = {"animeMovies": True, "animeSeries": True, "animeMinScore": 75}


def run(unit, hours, state=None):
    state = state or {}
    for h in range(hours):
        state = d.movies_tick(rt, [unit.url], state, T0 + h * HOUR, httpx, anime=mal)
    return state


def test_alternates():
    u = Unit(**ANIME_ON)
    s = run(u, 4)
    kinds = ["mal" if x >= 2 else "rt" for x in u.sources]
    assert kinds == ["rt", "mal", "rt", "mal"], u.sources
    assert s[u.url]["last"] == "mal"
    assert s[u.url]["mal_rotation"]["current"].startswith("mal:")


def test_anime_only():
    u = Unit(moviesTheaters=False, moviesHome=False, **ANIME_ON)
    run(u, 3)
    assert len(u.sources) == 3 and all(x >= 2 for x in u.sources), u.sources


def test_rt_only_when_anime_empty():
    u = Unit(**{**ANIME_ON, "animeMinScore": 95})     # nothing scores 9.5 in the fixtures
    run(u, 3)
    assert u.sources and all(x < 2 for x in u.sources) and len(u.sources) == 3, u.sources


def test_old_firmware():
    u = Unit()                                          # no anime keys at all
    run(u, 3)
    assert all(x < 2 for x in u.sources) and len(u.sources) == 3, u.sources


def test_old_state_loads():
    u = Unit(**ANIME_ON)
    first = [t.slug for t, _ in rt.entries({"theaters", "home"})][0]
    old = {u.url: {"rotation": {"current": first, "shown_at": T0 - 2 * HOUR, "seen": [first], "round": []},
                   "frame_hash": "x"}}                  # a kitt37 state entry
    s = d.movies_tick(rt, [u.url], old, T0, httpx, anime=mal)
    assert u.sources and u.sources[0] >= 2, u.sources   # RT went last, so anime is next
    assert s[u.url]["rotation"]["seen"] == [first]      # RT round kept


def test_unit_threshold():
    u = Unit(moviesTheaters=False, moviesHome=False, **{**ANIME_ON, "animeMinScore": 88})
    run(u, 3)
    allowed = {s.title for s in mal.shows({"movie", "series"}, 8.8)}
    assert u.titles and set(u.titles) <= allowed, (u.titles, allowed)


def test_english_toggle_repushes():
    # Only show 61987 has an English title in the fixtures (the others fall back to romaji).
    u = Unit(moviesTheaters=False, moviesHome=False, animeSeries=True, animeMinScore=86)
    s = run(u, 4)
    assert "Kusuriya no Hitorigoto 3rd Season" in u.titles, u.titles
    u.cfg["animeEnglish"] = True
    s[u.url]["mal_rotation"]["seen"] = []               # same round again, now in English
    for h in range(4, 8):
        s = d.movies_tick(rt, [u.url], s, T0 + h * HOUR, httpx, anime=mal)
    assert "The Apothecary Diaries Season 3" in u.titles[4:], u.titles


def test_without_anime_cache():
    u = Unit(**ANIME_ON)
    s = d.movies_tick(rt, [u.url], {}, T0, httpx)      # old call shape: RT only
    assert u.sources == [0] or u.sources == [1], u.sources


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("test_anime_push: OK")
