"""One movie an hour, in list order, every movie once per round, keyed by slug, surviving restarts."""
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


# Owner example: 50 movies -> day 1 = 1-24, day 2 = 25-48, day 3 = 49, 50, then a new round.
shown = run(movies.Rotation(), ORDER, 72)
assert shown[:24] == ORDER[:24], shown[:24]
assert shown[24:48] == ORDER[24:48], shown[24:48]
assert shown[48:52] == ["m49", "m50", "m1", "m2"], shown[48:52]

# Restart mid-round: the saved state continues with the next unseen movie.
r = movies.Rotation()
run(r, ORDER, 10)
r2 = movies.Rotation.from_dict(json.loads(json.dumps(r.to_dict())))
assert r2.current == "m10"
assert run(r2, ORDER, 2, start=T0 + 10 * HOUR) == ["m11", "m12"]

# A refresh mid-round drops a removed movie and appends a new one, with no repeats.
r = movies.Rotation()
assert run(r, ORDER, 5) == ["m1", "m2", "m3", "m4", "m5"]
new_order = [s for s in ORDER if s != "m3"] + ["new1"]
rest = run(r, new_order, 46, start=T0 + 5 * HOUR)
assert rest == [s for s in ORDER[5:]] + ["new1"], rest
assert run(r, new_order, 1, start=T0 + 51 * HOUR) == ["m1"]          # only now does the round restart

# Hourly: not due before an hour, due at an hour, due when nothing was ever shown.
r = movies.Rotation()
assert movies.due(r, T0)
r.mark_shown("m1", T0)
assert not movies.due(r, T0 + 3599)
assert movies.due(r, T0 + 3600)

assert movies.Rotation().next_slug([]) is None
print("test_movies_rotation: OK")
