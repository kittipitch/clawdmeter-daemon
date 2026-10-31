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


def is_season_page(page: str) -> bool:
    """A real season page (it may still list no show that passes the filters, e.g. the first days of a
    season before anything is scored) as opposed to a failed or changed page."""
    return '<h1 class="season_nav">' in page and 'class="anime-header">' in page


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


# ---- fetching and caching --------------------------------------------------------------------
SHOW_URL = "https://myanimelist.net/anime/{id}"


class AnimeCache:
    """This season's and last season's shows, English titles and posters on disk under `root`/anime.
    `fetch(url) -> bytes | None` is the only network access (injected, so tests run offline); `now()` is the
    clock. Refresh cadence and retry follow the RT lists (movies.REFRESH_S / movies.RETRY_S)."""

    def __init__(self, root, fetch, now):
        import json
        from pathlib import Path
        self.root, self.fetch, self.now, self._json = Path(root) / "anime", fetch, now, json
        (self.root / "posters").mkdir(parents=True, exist_ok=True)
        self._list = self._load("list.json", {"fetched_at": None, "shows": []})
        self._english = self._load("english.json", {})

    def _load(self, name, default):
        try:
            d = self._json.loads((self.root / name).read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else default
        except (OSError, ValueError):
            return default

    def _save(self, name, data):
        p = self.root / name
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(self._json.dumps(data), encoding="utf-8")
        tmp.replace(p)

    def _page(self, url):
        raw = self.fetch(url)
        return raw.decode("utf-8", "replace") if raw else ""

    def refresh(self, force=False):
        import movies
        from datetime import date
        at = self._list.get("fetched_at")
        if not force and isinstance(at, (int, float)) and self.now() - at < movies.REFRESH_S:
            return
        now_page = self._page(SEASON_URL)
        prev_url = previous_season_url(now_page) or season_url_by_date(date.fromtimestamp(self.now()), 1)
        prev_page = self._page(prev_url)
        cur, prev = parse_season(now_page), parse_season(prev_page)
        if is_season_page(now_page) and is_season_page(prev_page):
            # A show on both pages keeps the season it started in (the previous one).
            merged = {s.mal_id: s for s in cur}
            merged.update({s.mal_id: s for s in prev})
            self._list = {"fetched_at": self.now(), "shows": [s.__dict__ for s in merged.values()]}
        else:   # a page missing or unparsable: keep the last good list, try again in RETRY_S
            self._list["fetched_at"] = self.now() - movies.REFRESH_S + movies.RETRY_S
        self._save("list.json", self._list)
        for s in self._shows():
            key = str(s.mal_id)
            if key not in self._english:
                raw = self.fetch(SHOW_URL.format(id=s.mal_id))
                if raw is not None:   # "" = page read, no English title (never asked again)
                    self._english[key] = english_title(raw.decode("utf-8", "replace")) or ""
        self._save("english.json", self._english)
        keep = {s.poster_hash for s in self._shows()}
        for p in (self.root / "posters").glob("*.jpg"):
            if p.stem not in keep:
                p.unlink(missing_ok=True)

    def _shows(self):
        out = []
        for d in self._list.get("shows") or []:
            try:
                out.append(Show(**d))
            except TypeError:
                continue
        return out

    def shows(self, kinds, min_score):
        """Shows of the asked kinds at or above min_score: movies and series interleaved (movie first),
        each by members, most first."""
        lists = [sorted((s for s in self._shows() if s.kind == k and s.score >= min_score),
                        key=lambda s: -s.members) for k in ("movie", "series") if k in kinds]
        out = []
        for i in range(max((len(x) for x in lists), default=0)):
            out += [lst[i] for lst in lists if i < len(lst)]
        return out

    def frame_for(self, show, english):
        """The upload for this show, fetching its poster once per image; None (skip this hour) when the
        poster cannot be fetched or decoded."""
        import movies
        p = self.root / "posters" / f"{show.poster_hash}.jpg"
        if not p.is_file():
            raw = self.fetch(show.poster_url)
            if not raw:
                return None
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_bytes(raw)
            tmp.replace(p)
        title = (self._english.get(str(show.mal_id)) if english else "") or show.title
        frame = movies.pack_anime(p.read_bytes(), show, title)
        if frame is None:
            p.unlink(missing_ok=True)
        return frame
