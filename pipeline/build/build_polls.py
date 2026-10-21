"""National poll average for 29N, with the recipe chosen by pipeline/model/model_lab.py.

The lab tries ~100 epochs of algorithms (moving averages, LOESS, Holt, Kalman, Gaussian processes, ML corrections,
ensembles) validated on 40 Spanish elections 2018-2026 and stores the winner in data/poll_model.json ("config");
this script applies it with model_lab.predict(), its learned parts fitted on every past election.

Run from the project root:  .venv/Scripts/python pipeline/build/build_polls.py   -> data/polls.json
"""
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "model"))
import model_lab as lab  # noqa: E402
import poll_accuracy as pa  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "raw" / "polls"
PARTIES = json.loads((ROOT / "pipeline" / "build" / "parties.json").read_text("utf-8"))
PIDS = [p["party_id"] for p in PARTIES]

MODEL = json.loads((ROOT / "data" / "poll_model.json").read_text("utf-8"))  # pipeline/model/poll_accuracy.py
ELECTION = pd.Timestamp("2026-11-29")
CFG = lab.full(MODEL.get("config", lab.BASE))  # recipe chosen by pipeline/model/model_lab.py
K_MAE = CFG.get("k_mae", 2)       # pseudo-elections pulling a pollster's MAE toward the global mean
HALF_LIFE_DAYS = CFG.get("half_life") or 14  # only for the displayed pollster weights
WINDOW_DAYS = CFG.get("window", 45)          # polls listed in the app
FALLBACK_DAYS = 150    # for parties that no pollster measured inside WINDOW_DAYS
MIN_OTROS = 1.0
SENATE_HALF_LIFE = 14  # days; Senate polls (data/polls_senate.json) decay like the national window


def _load(name):
    df = pd.read_csv(RAW / name, dtype=str, keep_default_na=False)
    df["poll"] = df[["pollster_key", "commissioner", "fieldwork_start", "fieldwork_end", "sample_size"]].agg("|".join, axis=1)
    df["pct"] = pd.to_numeric(df["pct"], errors="coerce")
    df["sample_size"] = pd.to_numeric(df["sample_size"], errors="coerce")
    df["fieldwork_end"] = pd.to_datetime(df["fieldwork_end"], errors="coerce")
    for col in ("is_result", "is_aggregate"):
        df[col] = df[col].isin(["1", "True", "true"])
    df["ids"] = df["party_ids"].str.split(";").apply(lambda xs: [x.strip() for x in xs if x.strip()])
    return df[df["pct"].notna() & ~df["is_aggregate"]]


def load_national():
    return _load("national_polls.csv")


def poll_reference(nat, day, pid, days=30):
    """Mean national poll figure for a lineage id in the `days` before `day` (None if unpolled)."""
    for span in (days, 2 * days):
        w = nat[~nat["is_result"] & (nat["fieldwork_end"] < day) & (nat["fieldwork_end"] >= day - pd.Timedelta(days=span))]
        w = w[w["ids"].apply(lambda ids: pid in ids)]
        if len(w):
            return float(w.groupby("poll")["pct"].sum().mean())
    return None


def build(today=None):
    today = pd.Timestamp(today or pd.Timestamp.today().normalize())
    nat = load_national()
    maes = {k: [m for _, m in v] for k, v in MODEL["pollster_mae"].items()}
    bias = MODEL["industry_bias"]

    all_maes = [m for v in maes.values() for m in v]
    global_mae = float(np.mean(all_maes)) if all_maes else 2.0
    def shrunk_mae(key):
        v = maes.get(key, [])
        return (sum(v) + K_MAE * global_mae) / (len(v) + K_MAE)
    # relative error of the poll average (sigma = r x party %): at today's horizon and in the final week
    hs = sorted((int(h), r) for h, r in MODEL["sigma_relative"].items())
    horizon = max(0, (ELECTION - today).days)
    sigma_rel_now = float(np.interp(horizon, [h for h, _ in hs], [r for _, r in hs]))

    cur = nat[(nat["cycle"] == "next") & ~nat["is_result"]]
    cur = cur[cur["ids"].apply(lambda ids: len(ids) == 1 and ids[0] in PIDS)]
    cur = cur.assign(pid=cur["ids"].str[0])
    cur = cur.groupby(["poll", "pollster", "pollster_key", "fieldwork_end", "sample_size", "pid"], as_index=False, dropna=False)["pct"].sum()

    wiki = pa.load()
    history = pd.concat([wiki, pa.load_em(wiki) if pa.EM_FILE.exists() else wiki.iloc[:0]], ignore_index=True)
    test = cur.rename(columns={"fieldwork_end": "end", "sample_size": "sample"}).assign(
        party_raw=cur["pid"], party_id=cur["pid"], edate=ELECTION, etype="gen", election_id="29N")
    lab.TURNOUT["29N"] = (None, lab.TURNOUT["gen-2023"][0])  # previous general election's turnout (ML feature)
    avg = lab.predict(test, today, CFG, lab.Learned(history))
    old = cur[cur["fieldwork_end"] >= today - pd.Timedelta(days=FALLBACK_DAYS)]
    for pid, g in old.groupby("pid"):  # parties no recent poll measured: plain mean of the last 150 days
        if pid not in avg:
            avg[pid] = float(g.groupby("pollster_key")["pct"].mean().mean()) / (1 + bias.get(pid, 0.0))
    window = cur[cur["fieldwork_end"] >= today - pd.Timedelta(days=WINDOW_DAYS)]
    age = (today - window.groupby("pollster_key")["fieldwork_end"].max()).dt.days
    weight = {k: 0.5 ** (a / HALF_LIFE_DAYS) / shrunk_mae(k) ** CFG.get("w_exp", 0) for k, a in age.items()}
    total = sum(avg.values())
    otros = max(100 - total, MIN_OTROS)
    avg = {k: v * (100 - otros) / total for k, v in avg.items()}
    avg["otros"] = otros

    wsum = sum(weight.values()) or 1
    names = window.groupby("pollster_key")["pollster"].first()
    keys = sorted(weight, key=lambda k: -weight[k])
    pollsters = [{
        "key": k,
        "name": names.get(k, k),
        "weight": round(weight.get(k, 0) / wsum, 4),
        "mae": round(float(np.mean(maes[k])), 2) if k in maes else None,
        "mae_shrunk": round(shrunk_mae(k), 2),
        "n_elections": len(maes.get(k, [])),
        "elections": [e for e, _ in MODEL["pollster_mae"].get(k, [])],
        "last_fieldwork": str(window[window["pollster_key"] == k]["fieldwork_end"].max().date()) if k in weight else None,
    } for k in keys]

    polls = [{
        "pollster": g["pollster"].iloc[0],
        "pollster_key": key[1],
        "fieldwork_end": str(key[0].date()),
        "sample_size": None if pd.isna(g["sample_size"].iloc[0]) else int(g["sample_size"].iloc[0]),
        "values": {r.pid: round(r.pct, 2) for r in g.itertuples()},
    } for key, g in window.groupby(["fieldwork_end", "pollster_key", "poll"])]
    polls.sort(key=lambda p: p["fieldwork_end"], reverse=True)

    return {
        "generated": str(today.date()),
        "method": {
            "summary": f"Receta elegida por el laboratorio del modelo ({MODEL.get('lab', {}).get('tested', '—')} recetas en "
                       f"{MODEL.get('lab', {}).get('epochs', '—')} epochs, validadas en {len(MODEL['elections'])} elecciones españolas "
                       f"2018-2026 con validación cruzada por elección y elecciones apartadas): {lab.label(CFG)}.",
            "window_days": WINDOW_DAYS, "config": CFG,
            "turnout_effect": {r["party"]: r["slope"] for r in MODEL.get("lab", {}).get("turnout", {}).get("by_party", [])},
            "global_mae": round(global_mae, 2),
            "industry_bias_pct": {k: round(v * 100, 1) for k, v in bias.items() if abs(v) >= 0.005},
            "horizon_days": horizon,
            "sigma_rel_now": round(sigma_rel_now, 4),
            "sigma_rel_final": hs[0][1],
        },
        "average": {k: round(v, 2) for k, v in avg.items()},
        "pollsters": pollsters,
        "polls": polls,
    }


def senate_average(polls, weights, today):
    """Recency-weighted mean of Senate seats and vote_pct per party (None when no poll has that field).
    Weight = pollster weight (min weight if the pollster is unknown) x 0.5 ** (age in days / SENATE_HALF_LIFE).
    A party a poll does not list counts as 0 in that poll."""
    fallback = min(weights.values())
    out = {}
    for field in ("seats", "vote_pct"):
        rows = [p for p in polls if p.get(field)]
        if not rows:
            out[field] = None
            continue
        ws = [weights.get(p["pollster_key"], fallback) * 0.5 ** ((today - date.fromisoformat(p["fieldwork_end"])).days / SENATE_HALF_LIFE)
              for p in rows]
        pids = sorted({pid for p in rows for pid in p[field]})
        out[field] = {pid: round(sum(w * p[field].get(pid, 0) for w, p in zip(ws, rows)) / sum(ws), 2) for pid in pids}
    return out


if __name__ == "__main__":
    out = build()
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "polls.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    print("average:", out["average"])
    print("pollsters:", [(p["key"], p["weight"], p["mae_shrunk"], p["n_elections"]) for p in out["pollsters"][:15]])
    senate_file = ROOT / "data" / "polls_senate.json"
    senate = json.loads(senate_file.read_text("utf-8"))
    weights = {p["key"]: p["weight"] for p in out["pollsters"]}
    senate["average"] = senate_average(senate["polls"], weights, date.fromisoformat(out["generated"]))
    senate_file.write_text(json.dumps(senate, ensure_ascii=False, indent=1), "utf-8")
    print("senate average:", senate["average"])
