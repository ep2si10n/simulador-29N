"""Self-check for build_polls.senate_average: run with .venv/Scripts/python tests/test_polls_senate.py"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline" / "build"))
from build_polls import senate_average  # noqa: E402

TODAY = date(2026, 10, 20)
W = {"a": 0.5, "b": 0.25}

# no polls: nothing to average
assert senate_average([], W, TODAY) == {"seats": None, "vote_pct": None}

# same day, same pollster weight -> plain mean; a party missing from one poll counts as 0
polls = [
    {"pollster_key": "a", "fieldwork_end": "2026-10-20", "seats": {"pp": 100, "vox": 20}, "vote_pct": None},
    {"pollster_key": "a", "fieldwork_end": "2026-10-20", "seats": {"pp": 120}, "vote_pct": None},
]
avg = senate_average(polls, W, TODAY)
assert avg["seats"] == {"pp": 110, "vox": 10}, avg
assert avg["vote_pct"] is None

# 14 days old counts half as much as today's poll
polls = [
    {"pollster_key": "a", "fieldwork_end": "2026-10-20", "seats": {"pp": 100}, "vote_pct": {"pp": 30.0}},
    {"pollster_key": "a", "fieldwork_end": "2026-10-06", "seats": {"pp": 160}, "vote_pct": {"pp": 40.0}},
]
avg = senate_average(polls, W, TODAY)
assert avg["seats"] == {"pp": 120}, avg  # (100*1 + 160*0.5) / 1.5
assert avg["vote_pct"] == {"pp": 33.33}, avg

# unknown pollster takes the minimum weight (0.25 here, the lowest in W)
polls = [
    {"pollster_key": "a", "fieldwork_end": "2026-10-20", "seats": {"pp": 100}, "vote_pct": None},
    {"pollster_key": "zzz", "fieldwork_end": "2026-10-20", "seats": {"pp": 160}, "vote_pct": None},
]
assert senate_average(polls, W, TODAY)["seats"] == {"pp": 120}, "min weight is 0.25: (100*0.5+160*0.25)/0.75"

print("ok: senate_average")
