"""MyAnimeList season pages parse into shows (fixtures: trimmed pages as fetched 2026-11-01)."""
import re
from datetime import date
from pathlib import Path

import anime

FX = Path(__file__).parent / "fixtures"
NOW = (FX / "mal_season_now.html").read_text(encoding="utf-8")
PREV = (FX / "mal_season_prev.html").read_text(encoding="utf-8")
SHOW_PAGE = (FX / "mal_anime_61987.html").read_text(encoding="utf-8")


def test_filters():
    now = {s.mal_id: s for s in anime.parse_season(NOW)}
    assert sorted(now) == [61469, 61967, 61987, 61990, 62582], sorted(now)
    # Continuing (One Piece, Conan), kids, r18, unscored, 43,669 members, Special: all out
    for gone in (21, 235, 53876, 63053, 63509, 63938, 61607, 65111):
        assert gone not in now, gone
    assert now[61990].kind == "series" and now[61469].kind == "series"   # TV and ONA
    assert now[62582].kind == "movie"
    s = now[61987]
    assert (s.title, s.score, s.members) == ("Kusuriya no Hitorigoto 3rd Season", 8.64, 218503), s
    assert s.poster_url.startswith("https://cdn.myanimelist.net/images/anime/"), s.poster_url
    assert sorted(x.mal_id for x in anime.parse_season(PREV)) == [48820, 49233, 59193, 61469]


def test_r18_and_kids_dropped_even_when_popular():
    # Lift an r18 and a kids tile above every threshold: still out.
    def boost(page, mal_id):
        i = page.index(f"/anime/{mal_id}/")
        j = page.index("js-anime-category-producer", i) if "js-anime-category-producer" in page[i:] else len(page)
        tile = page[i:j]
        new = re.sub(r'js-score">[^<]*', 'js-score">9.50', tile)
        new = re.sub(r'js-members">\d+', 'js-members">900000', new)
        return page.replace(tile, new)
    page = boost(boost(NOW, 63509), 63053)
    ids = {s.mal_id for s in anime.parse_season(page)}
    assert 63509 not in ids and 63053 not in ids, ids


def test_members_threshold_boundary():
    page = NOW.replace('js-members">43669', 'js-members">50000')
    assert 61607 in {s.mal_id for s in anime.parse_season(page)}
    page = NOW.replace('js-members">43669', 'js-members">49999')
    assert 61607 not in {s.mal_id for s in anime.parse_season(page)}


def test_season_label():
    assert {s.season for s in anime.parse_season(NOW)} == {"Fall 26"}
    assert {s.season for s in anime.parse_season(PREV)} == {"Summer 26"}


def test_previous_link():
    assert anime.previous_season_url(NOW) == "https://myanimelist.net/anime/season/2026/summer"


def test_previous_missing_falls_back():
    broken = NOW.replace("season_nav", "nav_changed")
    assert anime.previous_season_url(broken) is None
    assert anime.season_url_by_date(date(2026, 11, 1), 1) == "https://myanimelist.net/anime/season/2026/summer"
    assert anime.season_url_by_date(date(2026, 8, 15), 1) == "https://myanimelist.net/anime/season/2026/spring"
    assert anime.season_url_by_date(date(2027, 2, 1), 1) == "https://myanimelist.net/anime/season/2026/fall"
    assert anime.season_url_by_date(date(2027, 2, 1), 0) == "https://myanimelist.net/anime/season/2027/winter"


def test_english_title():
    assert anime.english_title(SHOW_PAGE) == "The Apothecary Diaries Season 3"
    assert anime.english_title("<html></html>") is None


def test_poster_hash_stable():
    u = "https://cdn.myanimelist.net/images/anime/1234/5678.jpg"
    h = anime.poster_hash(u)
    assert len(h) == 16 and anime.poster_hash(u + "?s=abc") == h
    assert anime.poster_hash("https://cdn.myanimelist.net/images/anime/1234/9999.jpg") != h


def test_broken_page():
    assert anime.parse_season("<html></html>") == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("test_anime_parse: OK")
