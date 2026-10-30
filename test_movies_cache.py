"""Lists refresh every 6 h, release years are fetched once per movie, posters once per image."""
import re
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

import movies

FX = Path(__file__).parent / "fixtures"
PAGES = {"theaters": (FX / "rt_theaters.html").read_text(encoding="utf-8"),
         "home": (FX / "rt_home.html").read_text(encoding="utf-8")}
JPEG = (FX / "poster_clayface_160x240.jpg").read_bytes()
MOVIE = '<rt-text data-qa="item-label">Release Date (Theaters)</rt-text> <rt-text data-qa="item-value">Oct 1, {y}</rt-text>'
OLD = {"the_housemaid_2025"}          # anything here is a 2025 film; the rest are 2026


class Net:
    def __init__(self):
        self.calls = Counter()
        self.pages = dict(PAGES)
        self.fail = set()

    def __call__(self, url):
        self.calls[url] += 1
        for k, u in movies.SOURCES.items():
            if url == u:
                return self.pages[k].encode()
        m = re.match(r"https://www\.rottentomatoes\.com/m/([^/?#]+)$", url)
        if m:
            return MOVIE.format(y=2025 if m.group(1) in OLD else 2026).encode()
        if url in self.fail:
            return None
        return JPEG

    def browse_calls(self):
        return sum(n for u, n in self.calls.items() if "/browse/" in u)

    def movie_calls(self):
        return sum(n for u, n in self.calls.items() if "/m/" in u and "flixster" not in u)


clock = [datetime(2026, 10, 31, 12, 0).timestamp()]
root = Path(tempfile.mkdtemp())
net = Net()
cache = movies.MovieCache(root, net, lambda: clock[0])

# Release years are fetched once per movie, ever.
cache.refresh_lists(force=True)
first = net.movie_calls()
assert first > 0
cache.refresh_lists(force=True)
assert net.movie_calls() == first, (first, net.movie_calls())

# Browse pages refresh only after 6 h.
b = net.browse_calls()
clock[0] += 5 * 3600
cache.refresh_lists()
assert net.browse_calls() == b
clock[0] += 3600
cache.refresh_lists()
assert net.browse_calls() == b + 2

# A page that suddenly parses to nothing keeps the last good list.
both = cache.entries({"theaters", "home"})
net.pages = {"theaters": "<html></html>", "home": "<html></html>"}
cache.refresh_lists(force=True)
assert cache.entries({"theaters", "home"}) == both
net.pages = dict(PAGES)
cache.refresh_lists(force=True)

# Interleaved theaters/home, no slug twice, year filter applied, state survives a new cache object.
ent = cache.entries({"theaters", "home"})
assert [t.source for t, _ in ent[:4]] == ["theaters", "home", "theaters", "home"], [t.source for t, _ in ent[:4]]
slugs = [t.slug for t, _ in ent]
assert len(slugs) == len(set(slugs))
assert all(y in (2025, 2026) for _, y in ent)
assert [t.slug for t, _ in cache.entries({"home"})] == [t.slug for t, _ in ent if t.source == "home"] or \
       all(t.source == "home" for t, _ in cache.entries({"home"}))
assert cache.entries(set()) == []
again = movies.MovieCache(root, net, lambda: clock[0])
assert [t.slug for t, _ in again.entries({"theaters", "home"})] == slugs

# A poster that cannot be fetched skips the movie and is retried later.
tile, year = ent[0]
small = movies.poster_url_at(tile.poster_url, 160, 240)
net.fail.add(small)
for p in (root / "posters").glob(f"{tile.poster_hash}.jpg"):
    p.unlink()
assert cache.frame_for(tile, year) is None
net.fail.discard(small)
before = net.calls[small]
assert len(cache.frame_for(tile, year)) == 115200
assert net.calls[small] == before + 1

# New artwork for the same movie: new hash, new fetch, old file pruned at the next refresh.
old_hash = tile.poster_hash
assert (root / "posters" / f"{old_hash}.jpg").exists()
new_url = tile.poster_url.replace("ems.", "ems.Tk").replace("=/", "=/", 1)
net.pages["theaters"] = PAGES["theaters"].replace(tile.poster_url, new_url, 1)
cache.refresh_lists(force=True)
t2 = next(t for t, _ in cache.entries({"theaters"}) if t.slug == tile.slug)
assert t2.poster_hash != old_hash
assert cache.frame_for(t2, year) is not None
assert (root / "posters" / f"{t2.poster_hash}.jpg").exists()
assert not (root / "posters" / f"{old_hash}.jpg").exists()
print("test_movies_cache: OK")
