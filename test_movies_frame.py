"""The movie frame is a 240x240 big-endian RGB565 picture: poster left, scores column right.

Run with --preview OUT.png to also save the frame as a PNG for eyeballing.
"""
import dataclasses
import sys
from pathlib import Path

import movies

FX = Path(__file__).parent / "fixtures"
jpeg = (FX / "poster_clayface_160x240.jpg").read_bytes()
tile = movies.parse_browse((FX / "rt_theaters.html").read_text(encoding="utf-8"), "theaters")[0]
assert tile.slug == "clayface"

frame = movies.paint_frame(jpeg, tile, 2026)
assert isinstance(frame, bytes) and len(frame) == 115200, len(frame or b"")


def px(f, x, y):
    v = (f[2 * (y * 240 + x)] << 8) | f[2 * (y * 240 + x) + 1]
    return ((v >> 11) & 0x1F) << 3, ((v >> 5) & 0x3F) << 2, (v & 0x1F) << 3


col = movies.COLUMN_RGB
for x, y in ((200, 2), (239, 120), (161, 237)):
    got = px(frame, x, y)
    assert all(abs(a - b) <= 8 for a, b in zip(got, col)), (x, y, got)
assert px(frame, 80, 120) != px(frame, 200, 2)          # the poster is on the left

h = movies.frame_hash(frame)
assert h == movies.frame_hash(movies.paint_frame(jpeg, tile, 2026))
no_pop = dataclasses.replace(tile, popcorn=None)
assert movies.frame_hash(movies.paint_frame(jpeg, no_pop, 2026)) != h
assert movies.frame_hash(movies.paint_frame(jpeg, dataclasses.replace(tile, source="home"), 2026)) != h
big = dataclasses.replace(tile, tomatometer=100)          # 4 characters must still fit the column
assert len(movies.paint_frame(jpeg, big, 2026)) == 115200

assert movies.paint_frame(b"not a jpeg", tile, 2026) is None

if "--preview" in sys.argv:
    from PIL import Image
    out = Image.new("RGB", (240, 240))
    out.putdata([px(frame, i % 240, i // 240) for i in range(240 * 240)])
    out.resize((480, 480), Image.NEAREST).save(sys.argv[sys.argv.index("--preview") + 1])
print("test_movies_frame: OK")
