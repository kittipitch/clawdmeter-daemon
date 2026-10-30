"""Rotten Tomatoes browse pages parse into tiles (fixtures: the two pages as fetched 2026-10-31)."""
from pathlib import Path

import movies

FX = Path(__file__).parent / "fixtures"
theaters = movies.parse_browse((FX / "rt_theaters.html").read_text(encoding="utf-8"), "theaters")
home = movies.parse_browse((FX / "rt_home.html").read_text(encoding="utf-8"), "home")

assert len(theaters) == 28, len(theaters)
t0 = theaters[0]
assert (t0.slug, t0.title, t0.tomatometer, t0.popcorn, t0.source) == ("clayface", "Clayface", 77, 94, "theaters"), t0
assert len(home) == 28, len(home)
h0 = home[0]
assert (h0.slug, h0.tomatometer, h0.popcorn, h0.source) == ("forgotten_island", 95, 94, "home"), h0
fil = next(t for t in home if t.title == "Filipiñana")
assert fil.popcorn is None and fil.tomatometer == 90, fil

wild = next(t for t in theaters if t.slug == "wildwood")
small = t0.poster_url
assert "/164x246/v2/" in small, small
other_size = movies.poster_url_at(small, 160, 240)
assert "/160x240/v2/" in other_size and other_size.replace("/160x240/v2/", "/164x246/v2/") == small
h = movies.poster_asset_hash(small)
assert len(h) == 16 and all(c in "0123456789abcdef" for c in h), h
assert movies.poster_asset_hash(other_size) == h
assert movies.poster_asset_hash(wild.poster_url) != h
assert t0.poster_hash == h

assert movies.parse_browse("<html></html>", "home") == []
print("test_movies_parse: OK")
