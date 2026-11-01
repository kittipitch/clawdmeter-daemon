"""The movie upload is the poster as a 160x240 big-endian RGB565 picture plus a 72-byte trailer the
device draws its own text from: 'M','V', tomatometer, popcorn, year (LE), source, title length, then the
title as printable ASCII in a 64-byte zero-padded field.

Run with --preview OUT.png to also save the poster part as a PNG for eyeballing.
"""
import dataclasses
import sys
from pathlib import Path

import movies

FX = Path(__file__).parent / "fixtures"
jpeg = (FX / "poster_clayface_160x240.jpg").read_bytes()
tile = movies.parse_browse((FX / "rt_theaters.html").read_text(encoding="utf-8"), "theaters")[0]
assert tile.slug == "clayface"

assert movies.MOVIE_BYTES == 160 * 240 * 2 + 72 == 76872
up = movies.pack_movie(jpeg, tile, 2026)
assert isinstance(up, bytes) and len(up) == movies.MOVIE_BYTES, len(up or b"")
assert up[-72:-64] == bytes([ord("M"), ord("V"), 77, 94, 2026 & 0xFF, 2026 >> 8, 0, 8]), list(up[-72:-64])
assert up[-64:-3] == b"Clayface" + bytes(53), up[-64:]

home_none = dataclasses.replace(tile, popcorn=None, tomatometer=100, source="home")
t = movies.pack_movie(jpeg, home_none, 2025)[-72:-64]
assert t[:7] == bytes([ord("M"), ord("V"), 100, 255, 2025 & 0xFF, 2025 >> 8, 1]), list(t)

# The device font is ASCII: accents fold to their letter, other symbols and runs of spaces go, long
# titles are cut to the 64-byte field.
assert movies.ascii_title("Filipiñana") == "Filipinana"
assert movies.ascii_title("  Amélie —  Le\tFabuleux ") == "Amelie Le Fabuleux"
assert movies.ascii_title("x" * 80) == "x" * 64
# Title colour (owner 2026-11-01): the mean RGB of the poster's top 20% (48 rows), then its opposite on the
# art colour wheel; trailer bytes 69..71 = marker 0xC5 and the colour as RGB565 LE. The device has no
# framebuffer to sample, so the daemon works it out.
import colorsys
import io
from PIL import Image


def solid(color):
    b = io.BytesIO(); Image.new("RGB", (160, 240), color).save(b, "PNG"); return b.getvalue()


def lum(c):
    f = lambda v: v / 255 / 12.92 if v / 255 <= .03928 else ((v / 255 + .055) / 1.055) ** 2.4
    return .2126 * f(c[0]) + .7152 * f(c[1]) + .0722 * f(c[2])


def contrast(a, b):
    a, b = sorted((lum(a), lum(b)), reverse=True)
    return (a + .05) / (b + .05)


def hue(c):
    return colorsys.rgb_to_hsv(*[v / 255 for v in c])[0] * 360


assert 270 <= hue(movies.title_color((255, 255, 0))) <= 330         # yellow -> purple
assert 90 <= hue(movies.title_color((255, 0, 0))) <= 150            # red -> green
assert 15 <= hue(movies.title_color((0, 0, 255))) <= 45             # blue -> orange
assert movies.title_color((5, 5, 5)) == (255, 255, 0)               # no hue, dark: yellow
assert 270 <= hue(movies.title_color((250, 250, 250))) <= 330       # no hue, bright: purple
for r in range(0, 256, 51):                                         # always readable, even on mid grey
    for g in range(0, 256, 51):
        for b in range(0, 256, 51):
            assert contrast(movies.title_color((r, g, b)), (r, g, b)) >= 4.5, (r, g, b)
# Only the top 20% counts: a red top over a white body is judged red.
im = Image.new("RGB", (160, 240), (255, 255, 255)); im.paste((255, 0, 0), (0, 0, 160, 48))
buf = io.BytesIO(); im.save(buf, "PNG")
f = movies.pack_movie(buf.getvalue(), tile, 2026)
assert f[-3] == 0xC5
v = f[-2] | f[-1] << 8
assert 90 <= hue(((v >> 11) << 3, ((v >> 5) & 63) << 2, (v & 31) << 3)) <= 150, hex(v)
assert f[-64:-3] == b"Clayface" + bytes(53)                         # the title never reaches the colour

long = movies.pack_movie(jpeg, dataclasses.replace(tile, title="Mission: Impossible " * 5), 2026)
want = ("Mission: Impossible " * 5)[:60].rstrip().encode()             # cut at 60, no trailing space
assert len(long) == movies.MOVIE_BYTES and long[-65] == len(want) == 59, long[-65]
assert long[-64:-3] == want.ljust(61, b"\0") and long[-3] == 0xC5
full = movies.pack_movie(jpeg, dataclasses.replace(tile, title="x" * 80), 2026)
assert full[-65] == 60 and full[-64:-4] == b"x" * 60 and full[-4] == 0


def px(f, x, y):
    v = (f[2 * (y * 160 + x)] << 8) | f[2 * (y * 160 + x) + 1]
    return ((v >> 11) & 0x1F) << 3, ((v >> 5) & 0x3F) << 2, (v & 0x1F) << 3


# The picture part really is the poster: not one flat colour.
assert len({px(up, x, y) for x, y in ((10, 10), (80, 120), (150, 230), (80, 20))}) > 1

h = movies.frame_hash(up)
assert h == movies.frame_hash(movies.pack_movie(jpeg, tile, 2026))
assert movies.frame_hash(movies.pack_movie(jpeg, dataclasses.replace(tile, popcorn=None), 2026)) != h
assert movies.pack_movie(b"not a jpeg", tile, 2026) is None

if "--preview" in sys.argv:
    from PIL import Image
    out = Image.new("RGB", (160, 240))
    out.putdata([px(up, i % 160, i // 160) for i in range(160 * 240)])
    out.resize((320, 480), Image.NEAREST).save(sys.argv[sys.argv.index("--preview") + 1])
print("test_movies_frame: OK")
