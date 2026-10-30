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
assert up[-64:] == b"Clayface" + bytes(56), up[-64:]

home_none = dataclasses.replace(tile, popcorn=None, tomatometer=100, source="home")
t = movies.pack_movie(jpeg, home_none, 2025)[-72:-64]
assert t[:7] == bytes([ord("M"), ord("V"), 100, 255, 2025 & 0xFF, 2025 >> 8, 1]), list(t)

# The device font is ASCII: accents fold to their letter, other symbols and runs of spaces go, long
# titles are cut to the 64-byte field.
assert movies.ascii_title("Filipiñana") == "Filipinana"
assert movies.ascii_title("  Amélie —  Le\tFabuleux ") == "Amelie Le Fabuleux"
assert movies.ascii_title("x" * 80) == "x" * 64
long = movies.pack_movie(jpeg, dataclasses.replace(tile, title="Mission: Impossible " * 5), 2026)
assert len(long) == movies.MOVIE_BYTES and long[-65] == 64 and long[-64:] == ("Mission: Impossible " * 5)[:64].encode()


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
