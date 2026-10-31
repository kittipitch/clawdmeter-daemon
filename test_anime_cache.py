"""AnimeCache keeps the two seasons' shows, English titles and posters on disk; pack_anime builds the
76,872-byte upload with the anime trailer. Offline: fetch is faked from the fixtures."""
import tempfile
from collections import Counter
from pathlib import Path

import anime
import movies

FX = Path(__file__).parent / "fixtures"
NOW = (FX / "mal_season_now.html").read_bytes()
PREV = (FX / "mal_season_prev.html").read_bytes()
SHOW_PAGE = (FX / "mal_anime_61987.html").read_bytes()
JPEG = (FX / "poster_clayface_160x240.jpg").read_bytes()
PREV_URL = "https://myanimelist.net/anime/season/2026/summer"
T0 = 1_793_500_000.0   # 2026-11-01


class Net:
    def __init__(self):
        self.calls, self.down = Counter(), False

    def __call__(self, url):
        self.calls[url] += 1
        if self.down:
            return None
        if url == anime.SEASON_URL:
            return NOW
        if url == PREV_URL:
            return PREV
        if url == "https://myanimelist.net/anime/61987":
            return SHOW_PAGE
        if url.startswith("https://myanimelist.net/anime/"):
            return b"<html>no english title here</html>"
        if url.startswith("https://cdn.myanimelist.net/"):
            return JPEG
        return None


def cache(net, clock=lambda: T0):
    return anime.AnimeCache(tempfile.mkdtemp(), net, clock)


def show(**kw):
    base = dict(mal_id=1, title="Sousou no Frieren", poster_url="https://cdn.myanimelist.net/images/anime/1/2.jpg",
                poster_hash="0" * 16, score=8.64, members=218_496, kind="series", season="Fall 26")
    base.update(kw)
    return anime.Show(**base)


def test_pack_layout():
    f = movies.pack_anime(JPEG, show(), "Sousou no Frieren")
    assert len(f) == movies.MOVIE_BYTES == 76_872
    t = f[movies.POSTER_BYTES:]
    assert t[0:2] == b"MV"
    assert t[2] | t[3] << 8 == 864
    assert t[4] | t[5] << 8 == 218
    assert t[6] == 3 and t[7] == len("Sousou no Frieren")
    assert t[8:8 + 64] == b"Sousou no Frieren\0Fall 26".ljust(64, b"\0")
    assert movies.pack_anime(JPEG, show(kind="movie"), "x")[movies.POSTER_BYTES + 6] == 2
    big = movies.pack_anime(JPEG, show(members=70_000_000), "x")[movies.POSTER_BYTES:]
    assert big[4] | big[5] << 8 == 65535
    assert movies.pack_anime(b"not a jpeg", show(), "x") is None


def test_long_unicode_title():
    title = "Mahou Shoujo Madoka☆Magica Movie 4: Walpurgis no Kaiten and more words to pass fifty-six"
    t = movies.pack_anime(JPEG, show(), title)[movies.POSTER_BYTES:]
    n = t[7]
    name = t[8:8 + n].decode("ascii")
    assert n <= 56 and "☆" not in name and name.startswith("Mahou Shoujo Madoka"), name
    assert t[8 + n] == 0 and t[9 + n:9 + n + 7] == b"Fall 26"


def test_threshold_and_kinds():
    c = cache(Net())
    c.refresh()
    series = c.shows({"series"}, 8.5)
    assert series and all(s.kind == "series" and s.score >= 8.5 for s in series), series
    both = c.shows({"series", "movie"}, 7.5)
    kinds = [s.kind for s in both]
    assert kinds[:4] == ["movie", "series", "movie", "series"], kinds   # 2 movies today, interleaved first
    ser = [s.members for s in both if s.kind == "series"]
    assert ser == sorted(ser, reverse=True)
    ids = [s.mal_id for s in both]
    assert len(ids) == len(set(ids))                                  # Steel Ball Run is on both pages
    sbr = next(s for s in both if s.mal_id == 61469)
    assert sbr.season == "Summer 26", sbr                             # the season it started in
    assert c.shows(set(), 7.5) == []


def test_english_fallback():
    c = cache(Net())
    c.refresh()
    by = {s.mal_id: s for s in c.shows({"series", "movie"}, 7.5)}
    def title(mal_id, english):
        t = c.frame_for(by[mal_id], english)[movies.POSTER_BYTES:]
        return t[8:8 + t[7]].decode()
    assert title(61987, True) == "The Apothecary Diaries Season 3"
    assert title(61987, False) == "Kusuriya no Hitorigoto 3rd Season"
    assert title(61967, True) == "Black Clover 2nd Season"            # no English title: romaji


def test_refresh_keeps_list_on_failure():
    net, now = Net(), [T0]
    c = cache(net, lambda: now[0])
    c.refresh()
    before = c.shows({"series", "movie"}, 7.5)
    net.down, now[0] = True, T0 + movies.REFRESH_S + 1
    c.refresh()
    assert c.shows({"series", "movie"}, 7.5) == before


def test_show_page_fetched_once():
    net, now = Net(), [T0]
    c = cache(net, lambda: now[0])
    c.refresh()
    now[0] += movies.REFRESH_S + 1
    c.refresh()
    assert net.calls[anime.SEASON_URL] == 2
    assert net.calls["https://myanimelist.net/anime/61987"] == 1


def test_survives_restart():
    net = Net()
    root = tempfile.mkdtemp()
    anime.AnimeCache(root, net, lambda: T0).refresh()
    again = anime.AnimeCache(root, net, lambda: T0 + 60)
    again.refresh()                                       # inside 6 h: no fetch
    assert net.calls[anime.SEASON_URL] == 1
    assert again.shows({"series"}, 7.5)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("test_anime_cache: OK")
