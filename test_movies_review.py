"""Regression tests for the final-review findings on the movies feature (each failed first)."""
import sys
import tempfile
import types
from datetime import datetime
from pathlib import Path

import movies

T0 = datetime(2026, 10, 31, 0, 0).timestamp()
HOUR = 3600

# I4: a movie that appears mid-round joins the END of the round, even when the list puts it first
# (the theaters list is sorted by popularity, so new movies usually land near the top).
r = movies.Rotation()
order = [f"m{i}" for i in range(1, 6)]
shown = []
for h in range(3):
    s = r.next_slug(order); r.mark_shown(s, T0 + h * HOUR); shown.append(s)
order2 = ["hot"] + order
for h in range(3, 6):
    s = r.next_slug(order2); r.mark_shown(s, T0 + h * HOUR); shown.append(s)
assert shown == ["m1", "m2", "m3", "m4", "m5", "hot"], shown

# M1: when a round restarts after a reorder, the movie just shown is not shown again next.
r = movies.Rotation()
for s in ["a", "b", "c"]:
    assert r.next_slug(["a", "b", "c"]) == s; r.mark_shown(s, T0)
nxt = r.next_slug(["c", "a", "b"])          # new round, and "c" (just shown) is now first
assert nxt != "c", nxt

# Old saved state without a "round" field still works and continues in list order.
r = movies.Rotation.from_dict({"current": "m2", "shown_at": T0, "seen": ["m1", "m2"]})
assert r.next_slug(order) == "m3"

# I2: if both list fetches fail (daemon started before the network), retry soon, not in 6 h.
calls = []
def down(url):
    calls.append(url); return None
clock = [T0]
c = movies.MovieCache(Path(tempfile.mkdtemp()), down, lambda: clock[0])
c.refresh_lists()
n = len(calls)
clock[0] += 15 * 60
c.refresh_lists()
assert len(calls) > n, "lists were not retried 15 minutes after a failed first fetch"

# I3: push/promote errors are logged once per unit while the problem lasts, and again if it returns.
sys.modules.setdefault("aqi", types.ModuleType("aqi"))
import clawdmeter_daemon as d  # noqa: E402
logged = []
d.log = lambda m: logged.append(m)
d._movies_problem("http://u1", "push", "Movies push http://u1 HTTP 400")
d._movies_problem("http://u1", "push", "Movies push http://u1 HTTP 400")
assert len(logged) == 1, logged
d._movies_ok("http://u1")
d._movies_problem("http://u1", "push", "Movies push http://u1 HTTP 400")
assert len(logged) == 2, logged
print("test_movies_review: OK")
