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
    length cannot skip or repeat a movie. Never random: the round walks the list in order."""
    current: str | None = None
    shown_at: float | None = None
    seen: list = field(default_factory=list)

    def next_slug(self, order):
        """First movie in `order` not yet shown this round; a finished round starts over at order[0].
        Seen slugs that left the list are simply ignored (that is how removed movies drop out)."""
        if not order:
            return None
        seen = set(self.seen)
        for s in order:
            if s not in seen:
                return s
        self.seen = []
        return order[0]

    def mark_shown(self, slug, now):
        self.current, self.shown_at = slug, now
        if slug not in self.seen:
            self.seen.append(slug)

    def to_dict(self):
        return {"current": self.current, "shown_at": self.shown_at, "seen": list(self.seen)}

    @classmethod
    def from_dict(cls, d):
        d = d if isinstance(d, dict) else {}
        seen = d.get("seen") if isinstance(d.get("seen"), list) else []
        at = d.get("shown_at")
        return cls(current=d.get("current"), shown_at=at if isinstance(at, (int, float)) else None,
                   seen=[x for x in seen if isinstance(x, str)])


def due(rot, now, period_s=PERIOD_S) -> bool:
    return rot.shown_at is None or now - rot.shown_at >= period_s


# ---- painting --------------------------------------------------------------------------------
# 5x7 glyphs copied from the classic Adafruit GFX font (glcdfont, BSD licence), the font the SmallTV
# itself draws with, so the scores look like the rest of the device. Each tuple is 5 columns, bit 0 =
# top row.
GLYPHS = {
    '0': (0x3E, 0x51, 0x49, 0x45, 0x3E),
    '1': (0x00, 0x42, 0x7F, 0x40, 0x00),
    '2': (0x72, 0x49, 0x49, 0x49, 0x46),
    '3': (0x21, 0x41, 0x49, 0x4D, 0x33),
    '4': (0x18, 0x14, 0x12, 0x7F, 0x10),
    '5': (0x27, 0x45, 0x45, 0x45, 0x39),
    '6': (0x3C, 0x4A, 0x49, 0x49, 0x31),
    '7': (0x41, 0x21, 0x11, 0x09, 0x07),
    '8': (0x36, 0x49, 0x49, 0x49, 0x36),
    '9': (0x46, 0x49, 0x49, 0x29, 0x1E),
    '%': (0x23, 0x13, 0x08, 0x64, 0x62),
    '-': (0x08, 0x08, 0x08, 0x08, 0x08),
    ' ': (0x00, 0x00, 0x00, 0x00, 0x00),
    'A': (0x7C, 0x12, 0x11, 0x12, 0x7C),
    'B': (0x7F, 0x49, 0x49, 0x49, 0x36),
    'C': (0x3E, 0x41, 0x41, 0x41, 0x22),
    'D': (0x7F, 0x41, 0x41, 0x41, 0x3E),
    'E': (0x7F, 0x49, 0x49, 0x49, 0x41),
    'F': (0x7F, 0x09, 0x09, 0x09, 0x01),
    'G': (0x3E, 0x41, 0x41, 0x51, 0x73),
    'H': (0x7F, 0x08, 0x08, 0x08, 0x7F),
    'I': (0x00, 0x41, 0x7F, 0x41, 0x00),
    'J': (0x20, 0x40, 0x41, 0x3F, 0x01),
    'K': (0x7F, 0x08, 0x14, 0x22, 0x41),
    'L': (0x7F, 0x40, 0x40, 0x40, 0x40),
    'M': (0x7F, 0x02, 0x1C, 0x02, 0x7F),
    'N': (0x7F, 0x04, 0x08, 0x10, 0x7F),
    'O': (0x3E, 0x41, 0x41, 0x41, 0x3E),
    'P': (0x7F, 0x09, 0x09, 0x09, 0x06),
    'Q': (0x3E, 0x41, 0x51, 0x21, 0x5E),
    'R': (0x7F, 0x09, 0x19, 0x29, 0x46),
    'S': (0x26, 0x49, 0x49, 0x49, 0x32),
    'T': (0x03, 0x01, 0x7F, 0x01, 0x03),
    'U': (0x3F, 0x40, 0x40, 0x40, 0x3F),
    'V': (0x1F, 0x20, 0x40, 0x20, 0x1F),
    'W': (0x3F, 0x40, 0x38, 0x40, 0x3F),
    'X': (0x63, 0x14, 0x08, 0x14, 0x63),
    'Y': (0x03, 0x04, 0x78, 0x04, 0x03),
    'Z': (0x61, 0x59, 0x49, 0x4D, 0x43)
}

W = H = 240
POSTER_W, POSTER_H = 160, 240
COLUMN_RGB = (0x1F, 0x1F, 0x1E)        # the firmware's C_PANEL card fill (0x18E3)
TEXT_RGB = (0xFF, 0xFF, 0xFF)
DIM_RGB = (0x9A, 0xAA, 0xAA)
TOMATO_RGB = (0xFA, 0x32, 0x0A)
POPCORN_RGB = (0xFF, 0xD2, 0x00)
STRIPE_RGB = (0xE8, 0x2B, 0x2B)

# 12x12 pixel-art icons, drawn at 2x (24x24). "#" = main colour, "g" = green / stripe, "." = clear.
TOMATO = [
    "....gg.g....", "...gggggg...", "..##gggg##..", ".##########.", "############", "############",
    "############", "############", "############", ".##########.", "..########..", "....####....",
]
POPCORN = [
    "..###.###...", ".#########..", "###########.", ".#########..", "..g#g#g#g...", "..g#g#g#g...",
    "..g#g#g#g...", "...g#g#g#...", "...g#g#g#...", "...g#g#g#...", "....g#g#....", "....g#g#....",
]


def rgb565_be(img) -> bytes:
    """A 240x240 RGB Pillow image as big-endian RGB565 (the firmware byte-swaps per row)."""
    rgb = img.convert("RGB").tobytes()
    out = bytearray(len(rgb) // 3 * 2)
    for i in range(len(rgb) // 3):
        r, g, b = rgb[3 * i], rgb[3 * i + 1], rgb[3 * i + 2]
        v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out[2 * i] = v >> 8
        out[2 * i + 1] = v & 0xFF
    return bytes(out)


def _text_w(text, scale):
    return len(text) * 6 * scale - scale          # 5 px glyph + 1 px gap, no gap after the last


def _draw_text(d, text, cx, y, scale, rgb):
    """Centre `text` on x=cx at the top y, scale x scale pixels per font dot."""
    x = cx - _text_w(text, scale) // 2
    for ch in text:
        cols = GLYPHS.get(ch, GLYPHS[" "])
        for c, bits in enumerate(cols):
            for r in range(7):
                if bits >> r & 1:
                    d.rectangle([x + c * scale, y + r * scale, x + c * scale + scale - 1, y + r * scale + scale - 1],
                                fill=rgb)
        x += 6 * scale


def _draw_icon(d, art, cx, y, main, other, k=2):
    x0 = cx - len(art[0]) * k // 2
    for r, row in enumerate(art):
        for c, p in enumerate(row):
            if p != ".":
                d.rectangle([x0 + c * k, y + r * k, x0 + c * k + k - 1, y + r * k + k - 1],
                            fill=main if p == "#" else other)


def _fit_scale(text, width, scales):
    return next((s for s in scales if _text_w(text, s) <= width), scales[-1])


def paint_frame(poster_jpeg: bytes, tile, year: int):
    """The finished screen: poster 160x240 at the left, a dark 80 px column with the Tomatometer, the
    Popcorn score (or --), the release year and IN THEATERS / AT HOME. None if the JPEG is unusable."""
    import io
    from PIL import Image, ImageDraw, ImageOps
    try:
        with Image.open(io.BytesIO(poster_jpeg)) as im:
            poster = ImageOps.fit(im.convert("RGB"), (POSTER_W, POSTER_H), Image.LANCZOS)
    except Exception:
        return None
    img = Image.new("RGB", (W, H), COLUMN_RGB)
    img.paste(poster, (0, 0))
    d = ImageDraw.Draw(img)
    cx, inner = POSTER_W + (W - POSTER_W) // 2, W - POSTER_W - 6
    tm = "--" if tile.tomatometer is None else f"{tile.tomatometer}%"
    pc = "--" if tile.popcorn is None else f"{tile.popcorn}%"
    _draw_icon(d, TOMATO, cx, 10, TOMATO_RGB, (0x3C, 0xB0, 0x3C))
    _draw_text(d, tm, cx, 40, _fit_scale(tm, inner, (4, 3)), TEXT_RGB)
    _draw_icon(d, POPCORN, cx, 84, POPCORN_RGB, STRIPE_RGB)
    _draw_text(d, pc, cx, 114, _fit_scale(pc, inner, (4, 3)), TEXT_RGB)
    _draw_text(d, str(year), cx, 162, 3, TEXT_RGB)
    lines = ["IN", "THEATERS"] if tile.source == "theaters" else ["AT", "HOME"]
    y = 194
    for ln in lines:
        s = _fit_scale(ln, inner, (2, 1))
        _draw_text(d, ln, cx, y, s, DIM_RGB)
        y += 7 * s + 4
    return rgb565_be(img)


def frame_hash(frame: bytes) -> str:
    return hashlib.sha256(frame).hexdigest()
