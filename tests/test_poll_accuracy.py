"""Run: .venv/Scripts/python tests/test_poll_accuracy.py"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline" / "model"))
import model_lab as lab  # noqa: E402
import poll_accuracy as pa  # noqa: E402

# Party names from Electomanía and Wikipedia land on the same key.
assert pa.canon("PSC") == pa.canon("PSOE") == "psoe"
assert pa.canon("Más Madrid") == "mm" and pa.canon("Se Acabó la Fiesta") == "salf" and pa.canon("Cs") == "cs"

# Two houses, same day: the more reliable one (lower past MAE) weighs more; industry bias divides the average.
at = pd.Timestamp("2026-01-31")
polls = pd.DataFrame([
    {"pollster_key": "a", "end": at, "party_raw": "X", "party_id": "x", "pct": 30.0},
    {"pollster_key": "b", "end": at, "party_raw": "X", "party_id": "x", "pct": 20.0},
])
maes = {"a": [("e1", 1.0)] * 10, "b": [("e1", 3.0)] * 10}
v = pa.average(polls, at, maes=maes)["X"]
assert 28 < v < 30, v
assert abs(pa.average(polls, at, maes=maes, industry={"x": 0.25})["X"] - v / 1.25) < 1e-9
assert pa.average(polls, at - pd.Timedelta(days=1)) == {}, "polls after the nowcast date are ignored"

# Industry bias: polls 10 % above the result in every election, shrunk by k pseudo-elections.
train = pd.DataFrame([{"election_id": f"e{i}", "days": 5, "party_id": "x", "pct": 11.0, "result": 10.0} for i in range(3)])
assert abs(pa.industry_bias(train, k=3)["x"] - 0.05) < 1e-9
# Grouped stratified k-fold: every election is tested exactly once per repeat, never in its own training set,
# and each type spreads over several folds.
el = pd.DataFrame([{"election_id": f"{t}-{i}", "etype": t, "edate": pd.Timestamp(f"{2018 + i % 9}-06-01")}
                   for t, n in (("gen", 5), ("eur", 3), ("aut", 30)) for i in range(n)])
splits = pa.group_kfold(el, k=5, seed=1)
tested = [x for _, _, te in splits for x in te]
assert sorted(tested) == sorted(el["election_id"]) and all(not (tr & te) for _, tr, te in splits)
assert len({i for i, (_, _, te) in enumerate(splits) if any(x.startswith("gen") for x in te)}) >= 3
roll = pa.rolling(el)
assert all(el.set_index("election_id").loc[list(tr), "edate"].max() < pd.Timestamp(f"{y}-01-01") for y, tr, _ in roll if tr)

# Lab: every algorithm family recovers a flat 30 % from noisy polls and a rising line ends near its last value.
rng = __import__("numpy").random.default_rng(0)
days = pd.date_range("2025-09-01", "2026-01-31", freq="3D")
flat = pd.DataFrame([{"election_id": "t", "edate": at + pd.Timedelta(days=30), "etype": "aut", "pollster_key": f"p{i % 4}",
                      "end": d, "party_raw": "X", "party_id": "", "pct": 30 + rng.normal(0, 0.5), "sample": 1000.0}
                     for i, d in enumerate(days)])
L = lab.Learned(flat.assign(days=30, result=30.0, err=0.0, poll=flat.index.astype(str)))
for m in lab.METHODS:
    v = lab.predict(flat, at, lab.full({"method": m, "industry": None}), L)["X"]
    assert abs(v - 30) < 0.8, (m, v)
rise = flat.assign(pct=20 + 0.05 * (flat["end"] - days[0]).dt.days)
assert lab.predict(rise, at, lab.full({"method": "kalman", "industry": None, "q": 0.2}), L)["X"] > 26
assert lab.label(lab.full({"method": "gp", "ell": 7})) == "Proceso gaussiano: ell=7"
print("poll_accuracy tests: OK")
