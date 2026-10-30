"""Movies page: Rotten Tomatoes Certified Fresh lists -> one painted 240x240 frame per unit per hour.

Pure logic, no side effects on import, so it can be tested on its own. clawdmeter_daemon.py owns the
thread, the CLI flag and the device pushes. Sources (owner-chosen, Certified Fresh only):
"""
import hashlib
import html
import re
from dataclasses import dataclass

SOURCES = {
    "theaters": "https://www.rottentomatoes.com/browse/movies_in_theaters/critics:certified_fresh~sort:popular",
    "home": "https://www.rottentomatoes.com/browse/movies_at_home/critics:certified_fresh",
}


@dataclass(frozen=True)
class Tile:
    slug: str            # /m/<slug>: the movie's stable identity (rotation counts movies)
    title: str
    poster_url: str
    poster_hash: str     # identity of the poster IMAGE (new artwork -> new hash)
    tomatometer: int | None
    popcorn: int | None  # absent for some movies -> drawn as "--"
    source: str          # "theaters" or "home"


_TILE = re.compile(r"<media-info-tile>(.*?)</media-info-tile>", re.S)
_SIZE = re.compile(r"/\d+x\d+/v2/")


def _first(pattern, text):
    m = re.search(pattern, text, re.S)
    return m.group(1) if m else None


def _pct(text):
    return int(text) if text and text.isdigit() else None


def poster_asset_hash(url: str) -> str:
    """Hash of the image FILE the URL names. The resize URL wraps a signed inner URL ending in
    `ems.<base64 path>`; only that part identifies the artwork (the outer part changes with the size)."""
    m = re.search(r"/(ems\.[A-Za-z0-9+/=_-]+)", url or "")
    key = m.group(1) if m else (url or "")
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def poster_url_at(url: str, w: int, h: int) -> str:
    """Same poster at another size: rewrite only the /<W>x<H>/v2/ segment (tested: still a JPEG)."""
    return _SIZE.sub(f"/{w}x{h}/v2/", url, count=1)


def parse_browse(page: str, source: str) -> list:
    """Tiles of one browse page, in page order. Unparseable tiles are skipped, never raised."""
    flat = re.sub(r"\s+", " ", page or "")
    tiles = []
    for t in _TILE.findall(flat):
        slug = _first(r'media-url="/m/([^"/]+)"', t)
        poster = _first(r'<rt-img[^>]*\bsrc="([^"]+)"', t)
        if not slug or not poster:
            continue
        title = _first(r'data-qa="discovery-media-list-item-title"[^>]*> ?([^<]+?) ?<', t) or slug
        tiles.append(Tile(
            slug=slug,
            title=html.unescape(title.strip()),
            poster_url=html.unescape(poster),
            poster_hash=poster_asset_hash(poster),
            tomatometer=_pct(_first(r'slot="criticsScore"[^>]*> ?(\d+)%', t)),
            popcorn=_pct(_first(r'slot="audienceScore"[^>]*> ?(\d+)%', t)),
            source=source,
        ))
    return tiles


def _label_year(flat, label):
    m = re.search(r'data-qa="item-label"> ?' + re.escape(label) + r' ?</rt-text>.*?data-qa="item-value"> ?([^<]+?) ?<',
                  flat)
    y = re.search(r"\b(19|20)\d\d\b", m.group(1)) if m else None
    return int(y.group(0)) if y else None


def release_year(page: str):
    """The movie's release year from its page: "Release Date (Theaters)", else "(Streaming)", else
    the JSON-LD dateCreated (least reliable). None when none is found (the movie is then dropped)."""
    flat = re.sub(r"\s+", " ", page or "")
    for label in ("Release Date (Theaters)", "Release Date (Streaming)"):
        y = _label_year(flat, label)
        if y:
            return y
    m = re.search(r'"dateCreated" ?: ?"((?:19|20)\d\d)-', flat)
    return int(m.group(1)) if m else None


def keep_year(year, today) -> bool:
    """Owner rule: this year or the year before, never further back."""
    return year is not None and today.year - 1 <= year <= today.year
