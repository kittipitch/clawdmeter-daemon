"""Anime for the Movies page: MyAnimeList season pages -> well-rated, popular shows of this season and the last.

Pure logic, no side effects on import, so it can be tested on its own (like movies.py). The movies thread
in clawdmeter_daemon.py owns the clock and the pushes; this module only reads pages it is handed.
Filters fixed here (owner 2026-11-01): >= 50,000 members, no "TV (Continuing)" (long-running shows), no
adult or kids titles (MAL's own r18/kids marks), only TV (New), ONA, OVA (series) and Movie sections.
The score threshold is per unit and applied later (AnimeCache.shows).
"""
import hashlib
import html
import re
from dataclasses import dataclass

SEASON_URL = "https://myanimelist.net/anime/season"
MIN_MEMBERS = 50_000
KINDS = {"TV (New)": "series", "ONA": "series", "OVA": "series", "Movie": "movie"}
SEASONS = ("winter", "spring", "summer", "fall")


@dataclass(frozen=True)
class Show:
    mal_id: int          # the show's identity (rotation counts shows)
    title: str           # MAL's main title (romaji)
    poster_url: str
    poster_hash: str     # identity of the poster IMAGE
    score: float
    members: int
    kind: str            # "movie" or "series"
    season: str          # "Fall 26": the season page it came from


_NAV = re.compile(r'<h1 class="season_nav">\s*<a[^>]*>\s*([A-Za-z]+)\s+(\d{4})\s*</a>')
_PREV = re.compile(r'<a href="(https://myanimelist\.net/anime/season/\d{4}/[a-z]+)">[^<]*</a>\s*</li>\s*<li>\s*'
                   r'<h1 class="season_nav">')


def _label(page):
    m = _NAV.search(page)
    return f"{m.group(1).capitalize()} {m.group(2)[2:]}" if m else ""


def poster_hash(url: str) -> str:
    return hashlib.sha256(url.split("?")[0].encode()).hexdigest()[:16]


def parse_season(page: str) -> list:
    season = _label(page)
    out = []
    for sec in page.split('class="anime-header">')[1:]:
        kind = KINDS.get(html.unescape(sec[:sec.find("<")]).strip())
        if not kind:
            continue
        for tile in sec.split('class="js-anime-category-producer')[1:]:
            classes = tile[:tile.find('"')].split()
            if "r18" in classes or "kids" in classes:
                continue
            ident = re.search(r'href="https://myanimelist\.net/anime/(\d+)/', tile)
            score = re.search(r'js-score">([\d.]+)<', tile)
            members = re.search(r'js-members">(\d+)<', tile)
            title = re.search(r'js-title">([^<]*)<', tile)
            img = re.search(r'(?:data-src|src)="(https://cdn\.myanimelist\.net/images/anime/[^"]+)"', tile)
            if not (ident and score and members and title and img):
                continue
            sc, mem = float(score.group(1)), int(members.group(1))
            if sc <= 0 or mem < MIN_MEMBERS:
                continue
            url = img.group(1)
            out.append(Show(int(ident.group(1)), html.unescape(title.group(1)).strip(), url, poster_hash(url),
                            sc, mem, kind, season))
    return out


def previous_season_url(page: str):
    m = _PREV.search(page)
    return m.group(1) if m else None


def season_url_by_date(d, back: int) -> str:
    """Fallback when the page's season nav is gone: seasons by calendar quarter."""
    i = d.year * 4 + (d.month - 1) // 3 - back
    return f"{SEASON_URL}/{i // 4}/{SEASONS[i % 4]}"


def english_title(page: str):
    m = re.search(r'class="title-english[^"]*">([^<]+)<', page)
    return html.unescape(m.group(1)).strip() if m else None
