"""One movie an hour, in a shuffled order (owner 2026-11-01: "shuffle the poster and dont order by
anything"), every movie once per round, keyed by slug, surviving restarts."""
import json
from datetime import datetime

import movies

ORDER = [f"m{i}" for i in range(1, 51)]
T0 = datetime(2026, 10, 31, 0, 0).timestamp()
HOUR = 3600


def run(rot, order, hours, start=T0):
    """Step the clock an hour at a time; return the slugs shown, in order."""
    shown = []
    for h in range(hours):
        now = start + h * HOUR
        if movies.due(rot, now):
            s = rot.next_slug(order)
            rot.mark_shown(s, now)
            shown.append(s)
    return shown


# 50 movies: each round shows every movie exactly once, then a new round; never the list order.
r = movies.Rotation()
shown = run(r, ORDER, 150)
rounds = [shown[0:50], shown[50:100], shown[100:150]]
for rnd in rounds:
    assert sorted(rnd) == sorted(ORDER), rnd
assert all(rnd != ORDER for rnd in rounds), "rounds must be shuffled, not in list order"
assert len({tuple(x) for x in rounds}) == 3, "each round gets its own shuffle"
assert all(a != b for a, b in zip(shown, shown[1:])), "never the same movie two hours running"

# Restart mid-round: the saved state continues the SAME shuffled round with the next unseen movie.
r = movies.Rotation()
first = run(r, ORDER, 10)
r2 = movies.Rotation.from_dict(json.loads(json.dumps(r.to_dict())))
assert r2.current == first[-1]
nxt = run(r2, ORDER, 40, start=T0 + 10 * HOUR)
assert sorted(first + nxt) == sorted(ORDER), "restart must neither repeat nor skip"
assert nxt == r.round[10:], "restart continues the saved order"

# A refresh mid-round drops a removed movie and adds a new one somewhere in the rest of the round.
r = movies.Rotation()
seen5 = run(r, ORDER, 5)
gone = next(s for s in ORDER if s not in seen5)
new_order = [s for s in ORDER if s != gone] + ["new1"]
rest = run(r, new_order, 45, start=T0 + 5 * HOUR)
assert sorted(seen5 + rest) == sorted(set(ORDER) - {gone} | {"new1"}), rest
assert gone not in rest

# Hourly: not due before an hour, due at an hour, due when nothing was ever shown.
r = movies.Rotation()
assert movies.due(r, T0)
r.mark_shown("m1", T0)
assert not movies.due(r, T0 + 3599)
assert movies.due(r, T0 + 3600)

assert movies.Rotation().next_slug([]) is None
assert movies.Rotation().next_slug(["only"]) == "only"
print("test_movies_rotation: OK")
