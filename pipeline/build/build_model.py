"""Elasticity matrix (party x province) + provinces table for 29N.

Elasticity of party i in province p in election e:  E = %V(p,i,e) / %N(i,e)
  generales/europeas: %N = the party's real national result in that election
  autonómicas: national parties -> national poll average at that date ("polls" mode, the spec)
               or the in-community profile scaled to the generales level ("distribution" mode);
               regional parties -> always the in-community profile scaled to the generales level.
Final E = weighted mean over the elections of the last 10 years (weights by election type and age).
The weights / mean / autonómica mode are the ones that best predict past general elections
(backtest on 10N 2019 and 23J 2023 using only data available before each of them).

Run from the project root:  .venv/Scripts/python pipeline/build/build_model.py
  -> data/party_elasticity.json, data/provinces_data.json, data/backtest.json
"""
import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_polls import load_national, poll_reference  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / "raw" / "results"
PARTIES = json.loads((ROOT / "pipeline" / "build" / "parties.json").read_text("utf-8"))
PIDS = [p["party_id"] for p in PARTIES]
REGION = {p["party_id"]: set(p["provinces"]) for p in PARTIES if p["provinces"]}
BASE = pd.read_csv(ROOT / "pipeline" / "build" / "provinces_base.csv", dtype={"id": str})
PROV = list(BASE["id"])
PIDX = {p: k for k, p in enumerate(PROV)}
ELECTION_DAY = pd.Timestamp("2026-11-29")
WINDOW_YEARS = 10
TURNOUT_ELECTIONS = {"gen-2019-04-28": 0.2, "gen-2019-11-10": 0.3, "gen-2023-07-23": 0.5}
BACKTEST_TARGETS = ["gen-2019-11-10", "gen-2023-07-23"]
E_FLOOR = 0.01  # for the geometric mean

GRID = {
    "w_eur": [0, 0.25, 0.5, 1],
    "w_aut": [0, 0.1, 0.25, 0.5],
    "half_life": [1, 2, 4, 8, 1000],
    "mean": ["arith", "geo"],
    "aut_ref": ["polls", "distribution"],
}


# ---------- loading ----------

def load_results():
    frames, totals = [], {}
    for f in sorted(RES.glob("*.csv")):
        if f.name.startswith("_") or f.name.endswith("_totals.csv") or f.name == "national_totals.csv":
            continue
        frames.append(pd.read_csv(f, dtype=str, keep_default_na=False))
        t = f.with_name(f.stem + "_totals.csv")
        if t.exists():
            tt = pd.read_csv(t, dtype={"province_id": str})
            tt["province_id"] = tt["province_id"].str.zfill(2)
            totals[f.stem] = tt.set_index("province_id").apply(pd.to_numeric, errors="coerce")
    res = pd.concat(frames, ignore_index=True)
    res["province_id"] = res["province_id"].str.zfill(2)
    res["date"] = pd.to_datetime(res["date"])
    res["pct"] = pd.to_numeric(res["pct"], errors="coerce")
    res["votes"] = pd.to_numeric(res["votes"], errors="coerce")
    res["ids"] = res["party_ids"].str.split(";").apply(lambda xs: [x.strip() for x in xs if x.strip() in PIDS])
    # provincial parties are matched by name here instead of in every scraper
    for p in PARTIES:
        if p.get("aliases"):
            hit = (res["ids"].str.len() == 0) & res["province_id"].isin(p["provinces"]) & res["candidacy"].str.contains(p["aliases"], case=False, regex=True)
            res.loc[hit, "ids"] = pd.Series([[p["party_id"]]] * hit.sum(), index=res.index[hit])
    return res[res["pct"].notna()], totals


def default_pct(res, W):
    """National-equivalent % from each party's latest election in its provinces (parties without polls)."""
    out = {}
    lin = lineage(res)
    for pid in REGION:
        g = lin[lin["pid"] == pid]
        if len(g):
            gen = g[g["type"] == "generales"]  # autonómica levels overstate local parties in a general election
            g = gen if len(gen) else g
            g = g[g["date"] == g["date"].max()]
            out[pid] = float(sum(r.pct * W[PIDX[r.province_id]] for r in g.itertuples()) / W.sum())
    return out


def in_region(pid, prov):
    return pid not in REGION or prov in REGION[pid]


def lineage(res):
    """History view: each candidacy counts for every lineage id it carries (profiles may be shared)."""
    r = res.explode("ids").dropna(subset=["ids"])
    r = r[[in_region(i, p) for i, p in zip(r["ids"], r["province_id"])]]
    return r.groupby(["election_id", "type", "date", "province_id", "ids"], as_index=False)[["pct", "votes"]].sum(min_count=1).rename(columns={"ids": "pid"})


def primary(ids):
    """Target view: one contender per candidacy (Sumar 2023 = 'fa', not also 'podemos')."""
    return "fa" if "fa" in ids else (ids[0] if ids else "otros")


# ---------- provinces table ----------

def provinces_table(totals):
    rows = []
    last = totals[max(TURNOUT_ELECTIONS)]
    for p in BASE.itertuples():
        valid_rate = turnout = wsum = 0.0
        for eid, w in TURNOUT_ELECTIONS.items():
            t = totals[eid].loc[p.id]
            valid_rate += w * t["valid_votes"] / t["census"]
            turnout += w * t["voters"] / t["census"]
            wsum += w
        census = int(last.loc[p.id, "census"])
        rows.append({"id": p.id, "name": p.name, "community": p.community, "seats": int(p.seats),
                     "census": census, "expected_census": int(round(census * valid_rate / wsum)),
                     "abstention": round(1 - turnout / wsum, 4)})
    return rows


# ---------- elasticity points ----------

def national_shares(lin, totals):
    """%N for generales/europeas from provincial votes."""
    out = {}
    for eid, g in lin[lin["type"].isin(["generales", "europeas"])].groupby("election_id"):
        valid = totals[eid]["valid_votes"]
        votes = g["votes"].fillna(g["pct"] * g["province_id"].map(valid) / 100)
        for pid, v in votes.groupby(g["pid"]).sum().items():
            out[(eid, pid)] = 100 * v / valid.sum()
    return out


def gen_points(lin, totals):
    nat = national_shares(lin, totals)
    g = lin[lin["type"].isin(["generales", "europeas"])].copy()
    g["E"] = [pct / nat[(e, pid)] if nat.get((e, pid)) else np.nan for e, pid, pct in zip(g["election_id"], g["pid"], g["pct"])]
    # a regional party that ran in only part of its region (AA 2023: Cádiz only) gives a distorted profile
    n = g.groupby(["election_id", "pid"])["province_id"].transform("nunique")
    partial = [pid in REGION and k < len(REGION[pid]) for pid, k in zip(g["pid"], n)]
    return g[~np.array(partial)].dropna(subset=["E"])


def aut_points(lin, gen, polls, W, cutoff, mode):
    """Elasticity points of autonómicas before `cutoff`."""
    a = lin[(lin["type"] == "autonomicas") & (lin["date"] < cutoff)]
    gen_before = gen[gen["date"] < cutoff].groupby(["pid", "province_id"])["E"].mean()
    out = []
    for (eid, pid), g in a.groupby(["election_id", "pid"]):
        provs = list(g["province_id"])
        w = W[[PIDX[p] for p in provs]]
        if pid not in REGION and mode == "polls":
            ref = poll_reference(polls, g["date"].iloc[0], pid)
            if not ref:
                continue
            e = g["pct"].to_numpy() / ref
        else:
            known = [gen_before.get((pid, p)) for p in provs]
            if all(k is not None and not np.isnan(k) for k in known):
                level = np.average(known, weights=w)
            elif pid in REGION:  # never ran in generales: all its vote is inside its provinces
                level = W.sum() / W[[PIDX[p] for p in REGION[pid]]].sum()
            else:
                level = 1.0
            mean_pct = np.average(g["pct"].to_numpy(), weights=w)
            if mean_pct <= 0:
                continue
            e = g["pct"].to_numpy() / mean_pct * level
        out.append(g.assign(E=e))
    return pd.concat(out) if out else a.assign(E=[])


def elasticity(points, target, W, params, pids):
    """Matrix P x len(pids); missing -> 1 (national) or the in-region uniform level (regional)."""
    pts = points[(points["date"] < target) & (points["date"] >= target - pd.DateOffset(years=WINDOW_YEARS))]
    age = (target - pts["date"]).dt.days / 365.25
    wt = pts["type"].map({"generales": 1.0, "europeas": params["w_eur"], "autonomicas": params["w_aut"]}) * 0.5 ** (age / params["half_life"])
    pts = pts.assign(w=wt)
    pts = pts[pts["w"] > 0]
    if params["mean"] == "geo":
        pts = pts.assign(v=np.log(np.maximum(pts["E"], E_FLOOR)))
    else:
        pts = pts.assign(v=pts["E"])
    agg = (pts["v"] * pts["w"]).groupby([pts["pid"], pts["province_id"]]).sum() / pts.groupby(["pid", "province_id"])["w"].sum()
    if params["mean"] == "geo":
        agg = np.exp(agg)
    M = np.zeros((len(PROV), len(pids)))
    for j, pid in enumerate(pids):
        if pid == "otros":
            M[:, j] = 1.0
            continue
        fallback = W.sum() / W[[PIDX[p] for p in REGION[pid]]].sum() if pid in REGION else 1.0
        for p in PROV:
            if in_region(pid, p):
                M[PIDX[p], j] = agg.get((pid, p), fallback)
    return M


# ---------- engine (mirror of js/engine.js) ----------

def rake(N, E, W, iters=300, tol=1e-9):
    w = W / W.sum()
    V = N[None, :] * E
    for _ in range(iters):
        rs = V.sum(1, keepdims=True)
        V = np.divide(V * 100, rs, out=np.zeros_like(V), where=rs > 0)
        nat = w @ V
        f = np.divide(N, nat, out=np.zeros_like(N), where=nat > 0)
        V = V * f[None, :]
        if np.abs(f[N > 0] - 1).max() < tol:
            break
    rs = V.sum(1, keepdims=True)
    return np.divide(V * 100, rs, out=np.zeros_like(V), where=rs > 0)


def dhondt(pct, seats, eligible):
    v = np.where(eligible & (pct >= 3), pct, 0.0)
    s = np.zeros(len(v), int)
    for _ in range(seats):
        q = np.where(v > 0, v / (s + 1), -1)
        s[int(np.argmax(q))] += 1
    return s


# ---------- backtest ----------

def target_frame(res, totals, eid):
    t = res[res["election_id"] == eid].copy()
    t["c"] = t["ids"].apply(primary)
    t = t[[in_region(c, p) or c == "otros" for c, p in zip(t["c"], t["province_id"])]]
    truth = t[t["c"] != "otros"].groupby(["province_id", "c"])["pct"].sum().unstack(fill_value=np.nan)
    valid = totals[eid]["valid_votes"].reindex(PROV).to_numpy(float)
    votes = t[t["c"] != "otros"].assign(v=lambda d: d["votes"].fillna(d["pct"] * d["province_id"].map(totals[eid]["valid_votes"]) / 100)).groupby("c")["v"].sum()
    N = (100 * votes / valid.sum()).to_dict()
    pids = sorted(N) + ["otros"]
    Nv = np.array([N[c] for c in pids[:-1]] + [max(100 - sum(N.values()), 0)])
    T = truth.reindex(index=PROV, columns=pids[:-1]).to_numpy(float)
    return pids, Nv, T, valid


def score(pids, N, T, valid, E):
    pred = rake(N, E, valid)[:, :-1]
    mask = ~np.isnan(T)
    mae = float(np.abs(pred - np.nan_to_num(T))[mask].mean())
    elig = np.array([True] * (len(pids) - 1))
    seats = BASE["seats"].to_numpy()
    serr = 0
    for k in range(len(PROV)):
        truth_row = np.nan_to_num(T[k])
        serr += np.abs(dhondt(pred[k], seats[k], elig) - dhondt(truth_row, seats[k], elig)).sum() / 2
    return mae, int(serr)


def backtest(res, totals, gen, lin, polls):
    targets = {eid: target_frame(res, totals, eid) for eid in BACKTEST_TARGETS if eid in totals}
    cutoffs = {eid: res.loc[res["election_id"] == eid, "date"].iloc[0] for eid in targets}
    aut = {(eid, mode): aut_points(lin, gen, polls, targets[eid][3], cutoffs[eid], mode)
           for eid in targets for mode in GRID["aut_ref"]}
    rows = []
    for combo in itertools.product(*GRID.values()):
        params = dict(zip(GRID, combo))
        if params["w_aut"] == 0 and params["aut_ref"] == "distribution":
            continue  # identical to the polls variant
        r = {**params}
        for eid, (pids, N, T, valid) in targets.items():
            pts = pd.concat([gen, aut[(eid, params["aut_ref"])]])
            r[eid] = score(pids, N, T, valid, elasticity(pts, cutoffs[eid], valid, params, pids))
        r["mae"] = float(np.mean([r[e][0] for e in targets]))
        r["seat_err"] = float(np.mean([r[e][1] for e in targets]))
        rows.append(r)
    # baseline: no history at all (E=1 nationally, uniform inside the region for regional parties)
    uniform = {"w_eur": 0, "w_aut": 0, "half_life": 1, "mean": "arith", "aut_ref": "polls"}
    base = {eid: score(pids, N, T, valid, elasticity(gen.iloc[:0], cutoffs[eid], valid, uniform, pids))
            for eid, (pids, N, T, valid) in targets.items()}
    rows.sort(key=lambda r: (round(r["mae"], 3), r["seat_err"]))
    return rows, base


def selfcheck():
    # D'Hondt textbook case (7 seats) and the 3% barrier
    assert list(dhondt(np.array([34.0, 28.0, 16.0, 6.0, 1.5]), 7, np.ones(5, bool))) == [3, 3, 1, 0, 0]
    # IPF: rows sum 100 and weighted national shares match the input
    N, W = np.array([40.0, 35.0, 25.0]), np.array([1.0, 3.0, 2.0])
    V = rake(N, np.array([[1.5, 0.5, 1.0], [0.8, 1.2, 1.0], [1.0, 1.0, 1.0]]), W)
    assert np.allclose(V.sum(1), 100) and np.allclose(W / W.sum() @ V, N, atol=1e-4)


def main():
    selfcheck()
    res, totals = load_results()
    provinces = provinces_table(totals)
    W = np.array([p["expected_census"] for p in provinces], float)
    lin = lineage(res)
    gen = gen_points(lin, totals)
    polls = load_national()

    rows, base = backtest(res, totals, gen, lin, polls)
    best = {k: rows[0][k] for k in GRID}
    print("baseline (uniform E):", base)
    for r in rows[:8]:
        print({k: r[k] for k in GRID}, "mae=%.3f seats=%.1f" % (r["mae"], r["seat_err"]), {e: r[e] for e in BACKTEST_TARGETS if e in r})

    pts = pd.concat([gen, aut_points(lin, gen, polls, W, ELECTION_DAY, best["aut_ref"])])
    E = elasticity(pts, ELECTION_DAY, W, best, PIDS + ["otros"])
    used = pts[(pts["date"] >= ELECTION_DAY - pd.DateOffset(years=WINDOW_YEARS))]
    coverage = used.groupby("pid")["election_id"].nunique().to_dict()

    defaults = default_pct(res, W)
    parties = [{**{k: v for k, v in p.items() if k != "aliases"}, "seats_eligible": True,
                "default_pct": round(defaults.get(p["party_id"], 0), 4),
                "elasticity_vector": {prov: round(float(E[PIDX[prov], j]), 4) for prov in PROV},
                "n_elections": int(coverage.get(p["party_id"], 0))}
               for j, p in enumerate(PARTIES)]
    parties.append({"party_id": "otros", "name": "Otros/Blanco", "full_name": "Otras candidaturas y voto en blanco",
                    "color": "#B0B0B0", "scope": "national", "provinces": None, "order": 99, "seats_eligible": False,
                    "elasticity_vector": {prov: 1.0 for prov in PROV}, "n_elections": 0})
    elections = sorted(used["election_id"].unique())
    method = {
        "summary": "Elasticidad = % del partido en la provincia / % nacional en esa elección (resultado real en generales y "
                   "europeas; en autonómicas, " + ("media de encuestas nacionales de esa fecha" if best["aut_ref"] == "polls" else
                   "perfil dentro de la comunidad escalado al nivel de las generales") + "). Se promedian las elecciones de los "
                   "últimos 10 años con pesos por tipo y antigüedad elegidos por backtest (predecir 10N 2019 y 23J 2023 solo con "
                   "datos anteriores). Partidos sin histórico: elasticidad 1. En el simulador, el voto provincial se ajusta "
                   "(IPF) para que cada provincia sume 100% y cada partido reproduzca su % nacional.",
        "params": best,
        "backtest": {e: {"mae_points": round(rows[0][e][0], 3), "seat_error": rows[0][e][1],
                         "baseline_uniform_mae": round(base[e][0], 3), "baseline_uniform_seat_error": base[e][1]} for e in base},
        "elections_used": elections,
    }
    out = {"generated": str(pd.Timestamp.today().date()), "method": method, "parties": parties}
    (ROOT / "data" / "party_elasticity.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    (ROOT / "data" / "provinces_data.json").write_text(json.dumps(provinces, ensure_ascii=False, indent=1), "utf-8")
    (ROOT / "data" / "backtest.json").write_text(json.dumps({"best": rows[0], "top": rows[:20], "baseline": base}, ensure_ascii=False, indent=1, default=str), "utf-8")
    print("best params:", best)
    print("elections used:", len(elections))


if __name__ == "__main__":
    main()
