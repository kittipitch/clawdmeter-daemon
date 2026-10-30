"""Release year comes from the movie page; only this year and last year are kept."""
import re
from datetime import date
from pathlib import Path

import movies

FX = Path(__file__).parent / "fixtures"
page = lambda n: (FX / f"rt_movie_{n}.html").read_text(encoding="utf-8")

assert movies.release_year(page("housemaid")) == 2025          # streams in 2026, released Dec 2025
assert movies.release_year(page("forgotten_island")) == 2026
assert movies.release_year(page("clayface")) == 2026

# Theaters wins over JSON-LD: make the theaters date 2027 and the answer must follow it.
fi = page("forgotten_island")
edited = fi.replace("Sep 25, 2026, Wide", "Sep 25, 2027, Wide")
assert edited != fi
assert movies.release_year(edited) == 2027

# Fallback 1: no theaters date -> streaming date.
no_theaters = re.sub(r"Release Date \(Theaters\)", "Something Else", fi)
assert movies.release_year(no_theaters.replace("Oct 27, 2026", "Oct 27, 2024")) == 2024

# Fallback 2: neither label -> JSON-LD dateCreated.
neither = re.sub(r"Release Date \((Theaters|Streaming)\)", "Something Else", fi)
assert movies.release_year(neither) == 2026                     # dateCreated 2026-06-24

assert movies.release_year("<html></html>") is None

today = date(2026, 10, 31)
assert movies.keep_year(2026, today) and movies.keep_year(2025, today)
assert not movies.keep_year(2024, today)
assert not movies.keep_year(None, today)
assert not movies.keep_year(2025, date(2027, 1, 1))
print("test_movies_year: OK")
