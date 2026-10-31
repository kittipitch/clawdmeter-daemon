"""Movies page: Rotten Tomatoes Certified Fresh lists -> one painted 240x240 frame per unit per hour.

Pure logic, no side effects on import, so it can be tested on its own. clawdmeter_daemon.py owns the
thread, the CLI flag and the device pushes. Sources (owner-chosen, Certified Fresh only):
"""
import hashlib
import html
import re
from dataclasses import dataclass, field

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


PERIOD_S = 3600  # one movie an hour (owner 2026-10-31), wall clock


@dataclass
class Rotation:
    """Where one unit is in its round. Keyed by slug, never by index, so a list that changes order or
    length cannot skip or repeat a movie. Never random: the round walks a fixed order.

    `round` is the order of the round in progress: movies that join the list mid-round go to its END
    (the theaters list is sorted by popularity, so a new movie usually appears near the top and would
    otherwise jump the queue); movies that left the list drop out. A new round copies the list order."""
    current: str | None = None
    shown_at: float | None = None
    seen: list = field(default_factory=list)
    round: list = field(default_factory=list)

    def next_slug(self, order):
        if not order:
            return None
        listed = set(order)
        rnd = [s for s in self.round if s in listed] + [s for s in order if s not in set(self.round)]
        seen = set(self.seen)
        unseen = [s for s in rnd if s not in seen]
        if not unseen:                                  # round finished: start a new one
            rnd, self.seen = list(order), []
            if len(rnd) > 1 and rnd[0] == self.current:  # never the same movie two hours running
                rnd.append(rnd.pop(0))
            unseen = rnd
        self.round = rnd
        return unseen[0]

    def upcoming(self):
        """Unseen movies of the current round, in order (call after next_slug)."""
        seen = set(self.seen)
        return [s for s in self.round if s not in seen]

    def mark_shown(self, slug, now):
        self.current, self.shown_at = slug, now
        if slug not in self.seen:
            self.seen.append(slug)

    def to_dict(self):
        return {"current": self.current, "shown_at": self.shown_at, "seen": list(self.seen),
                "round": list(self.round)}

    @classmethod
    def from_dict(cls, d):
        d = d if isinstance(d, dict) else {}
        strs = lambda v: [x for x in v if isinstance(x, str)] if isinstance(v, list) else []
        at = d.get("shown_at")
        return cls(current=d.get("current"), shown_at=at if isinstance(at, (int, float)) else None,
                   seen=strs(d.get("seen")), round=strs(d.get("round")))


def due(rot, now, period_s=PERIOD_S) -> bool:
    return rot.shown_at is None or now - rot.shown_at >= period_s


# ---- packing the upload -------------------------------------------------------------------------
# The poster is sent as a picture; the DEVICE draws the cards, icons and text in its own font so the
# page matches the quota pages exactly (owner 2026-10-31: "poster = pic and the font shud be font").
POSTER_W, POSTER_H = 160, 240
POSTER_BYTES = POSTER_W * POSTER_H * 2
TITLE_BYTES = 64
MOVIE_BYTES = POSTER_BYTES + 8 + TITLE_BYTES   # + trailer: 'M','V', tomatometer, popcorn, year LE, source,
                                               #   title length, then the ASCII title zero-padded to 64
COLUMN_RGB = (0x1F, 0x1F, 0x1E)    # the firmware's C_PANEL (kept for reference/tests)


def rgb565_be(img) -> bytes:
    """An RGB Pillow image as big-endian RGB565 (the firmware byte-swaps per row)."""
    rgb = img.convert("RGB").tobytes()
    out = bytearray(len(rgb) // 3 * 2)
    for i in range(len(rgb) // 3):
        r, g, b = rgb[3 * i], rgb[3 * i + 1], rgb[3 * i + 2]
        v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out[2 * i] = v >> 8
        out[2 * i + 1] = v & 0xFF
    return bytes(out)


def _score_byte(v):
    return 255 if v is None else max(0, min(100, int(v)))


def ascii_title(title: str) -> str:
    """The title in the device font's range: accents folded, other symbols dropped, spaces collapsed."""
    import unicodedata
    folded = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return " ".join("".join(c if c.isprintable() else " " for c in folded).split())[:TITLE_BYTES]


def pack_movie(poster_jpeg: bytes, tile, year: int):
    """Poster 160x240 RGB565 + 72-byte trailer, or None if the JPEG is unusable."""
    import io
    from PIL import Image, ImageOps
    try:
        with Image.open(io.BytesIO(poster_jpeg)) as im:
            poster = ImageOps.fit(im.convert("RGB"), (POSTER_W, POSTER_H), Image.LANCZOS)
    except Exception:
        return None
    y = max(0, min(0xFFFF, int(year)))
    name = ascii_title(tile.title).encode()
    trailer = bytes([ord("M"), ord("V"), _score_byte(tile.tomatometer), _score_byte(tile.popcorn),
                     y & 0xFF, y >> 8, 0 if tile.source == "theaters" else 1, len(name)])
    return rgb565_be(poster) + trailer + name.ljust(TITLE_BYTES, b"\0")


ANIME_TITLE_MAX = 53   # 64-byte field = title + 0 + label (up to 9: "Summer 26") + its closing 0


def pack_anime(poster_jpeg: bytes, show, title: str):
    """An anime frame (anime.Show): same size and poster as pack_movie; the trailer carries the MAL score
    in hundredths and the members in thousands (uint16 LE each), source 2 = anime movie / 3 = series, and
    the 64-byte field holds the title, a 0 byte, then the season label the device prints in row 3."""
    import io
    from PIL import Image, ImageOps
    try:
        with Image.open(io.BytesIO(poster_jpeg)) as im:
            poster = ImageOps.fit(im.convert("RGB"), (POSTER_W, POSTER_H), Image.LANCZOS)
    except Exception:
        return None
    score = max(0, min(0xFFFF, round(show.score * 100)))
    members = max(0, min(0xFFFF, show.members // 1000))
    name = ascii_title(title)[:ANIME_TITLE_MAX].rstrip().encode()
    field = name + b"\0" + ascii_title(show.season).encode()
    trailer = bytes([ord("M"), ord("V"), score & 0xFF, score >> 8, members & 0xFF, members >> 8,
                     2 if show.kind == "movie" else 3, len(name)])
    return rgb565_be(poster) + trailer + field[:TITLE_BYTES].ljust(TITLE_BYTES, b"\0")


def frame_hash(frame: bytes) -> str:
    return hashlib.sha256(frame).hexdigest()


# ---- fetching and caching --------------------------------------------------------------------
RETRY_S = 10 * 60      # after a refresh that fetched nothing
REFRESH_S = 6 * 3600   # lists refresh every 6 h; movie pages are read once per movie, posters once per image
MOVIE_URL = "https://www.rottentomatoes.com/m/{slug}"


class MovieCache:
    """Lists, release years and posters on disk under `root`. `fetch(url) -> bytes | None` is the only
    network access (injected, so tests run offline); `now() -> float` is the clock."""

    def __init__(self, root, fetch, now):
        import json
        from pathlib import Path
        self.root, self.fetch, self.now = Path(root), fetch, now
        (self.root / "posters").mkdir(parents=True, exist_ok=True)
        self._lists = self._load("lists.json", {"fetched_at": None, "theaters": [], "home": []})
        self._years = self._load("years.json", {})
        self._json = json

    def _load(self, name, default):
        import json
        try:
            d = json.loads((self.root / name).read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else default
        except (OSError, ValueError):
            return default

    def _save(self, name, data):
        p = self.root / name
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(self._json.dumps(data), encoding="utf-8")
        tmp.replace(p)

    def refresh_lists(self, force=False):
        at = self._lists.get("fetched_at")
        if not force and isinstance(at, (int, float)) and self.now() - at < REFRESH_S:
            return
        got_any = False
        for source, url in SOURCES.items():
            raw = self.fetch(url)
            tiles = parse_browse(raw.decode("utf-8", "replace"), source) if raw else []
            if tiles:   # a page that parses to nothing (site change, outage) keeps the last good list
                self._lists[source] = [t.__dict__ for t in tiles]
                got_any = True
        if got_any:
            self._lists["fetched_at"] = self.now()
        else:   # nothing fetched (network not up yet, site down): try again in RETRY_S, not in 6 h
            self._lists["fetched_at"] = self.now() - REFRESH_S + RETRY_S
        self._save("lists.json", self._lists)
        for t in self._tiles("theaters") + self._tiles("home"):
            if t.slug not in self._years:
                raw = self.fetch(MOVIE_URL.format(slug=t.slug))
                if raw is not None:
                    self._years[t.slug] = release_year(raw.decode("utf-8", "replace"))
        self._save("years.json", self._years)
        keep = {t.poster_hash for t in self._tiles("theaters") + self._tiles("home")}
        for p in (self.root / "posters").glob("*.jpg"):
            if p.stem not in keep:
                p.unlink(missing_ok=True)

    def _tiles(self, source):
        out = []
        for d in self._lists.get(source) or []:
            try:
                out.append(Tile(**d))
            except TypeError:
                continue
        return out

    def entries(self, sources):
        """(tile, year) for the requested sources, year-filtered, in page order; theaters and home
        interleaved when both are asked for; a movie on both lists appears once (theaters first)."""
        from datetime import date
        today = date.fromtimestamp(self.now())
        lists = [[t for t in self._tiles(s) if keep_year(self._years.get(t.slug), today)]
                 for s in ("theaters", "home") if s in sources]
        out, seen = [], set()
        for i in range(max((len(x) for x in lists), default=0)):
            for lst in lists:
                if i < len(lst) and lst[i].slug not in seen:
                    seen.add(lst[i].slug)
                    out.append((lst[i], self._years[lst[i].slug]))
        return out

    def frame_for(self, tile, year):
        """The painted frame for this movie, fetching its poster once per image. None (skip it this
        hour, retry next time) when the poster cannot be fetched or decoded."""
        p = self.root / "posters" / f"{tile.poster_hash}.jpg"
        if not p.is_file():
            raw = self.fetch(poster_url_at(tile.poster_url, POSTER_W, POSTER_H))
            if not raw:
                return None
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_bytes(raw)
            tmp.replace(p)
        frame = pack_movie(p.read_bytes(), tile, year)
        if frame is None:
            p.unlink(missing_ok=True)   # not a usable image: fetch again next time
        return frame
