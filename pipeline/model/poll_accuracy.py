"""How accurate are Spanish polls, and which way of averaging them predicts results best?

Data: every poll with a result row in the same Wikipedia table (generales 2019A/2019N/2023, European 2019/2024 and
regional elections 2018-2026) plus the Electomanía archive when present. Error = poll - result, in points.
Methods are scored "as if" h days before each election. Validation (the learned parts - pollster accuracy, house
effects, industry bias - only ever see training elections): grouped stratified k-fold (whole elections per fold,
stratified by election type x era, repeated with different seeds) and rolling origin (learn on the past, predict
each year). Writes data/poll_model.json and prints a report.

Run from the project root:  .venv/Scripts/python pipeline/model/poll_accuracy.py
"""
import json, re, sys, unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline" / "scrape"))
from polls_elections import pollster_key  # noqa: E402
RAW = ROOT / "raw" / "polls"
CYCLES = {"2019A": "2019-04-28", "2019N": "2019-11-10", "2023": "2023-07-23"}
AGGREGATES = {"keydata", "electocracia"}
MIN_PCT = 1.0          # parties below this in the result are not scored
FINAL_DAYS = 30        # a pollster's "final" poll for accuracy: fieldwork ended <= 30 days before the vote
HORIZONS = [7, 30, 45, 60, 90]


def load():
    """Long frame: eid, edate, etype, pollster_key, end, party, party_id, pct, result (one row per poll x column)."""
    nat = pd.read_csv(RAW / "national_polls.csv", dtype=str, keep_default_na=False)
    nat = nat[nat["cycle"].isin(CYCLES)].assign(election_id=lambda d: "gen-" + d["cycle"], election_date=lambda d: d["cycle"].map(CYCLES))
    frames = [nat]
    for f in ("election_polls.csv", "history_polls.csv"):
        if (RAW / f).exists():
            frames.append(pd.read_csv(RAW / f, dtype=str, keep_default_na=False))
    d = pd.concat(frames, ignore_index=True)
    d["pct"] = pd.to_numeric(d["pct"], errors="coerce")
    d = d[d["pct"].notna() & ~d["pollster_key"].isin(AGGREGATES)]
    d["poll"] = d[["election_id", "pollster_key", "commissioner", "fieldwork_start", "fieldwork_end", "sample_size"]].agg("|".join, axis=1)
    res = d[d["is_result"] == "1"].groupby(["election_id", "party_raw"])["pct"].first().rename("result")
    polls = d[d["is_result"] == "0"].join(res, on=["election_id", "party_raw"])
    polls = polls[polls["result"].notna()].copy()
    polls["edate"] = pd.to_datetime(polls["election_date"])
    polls["end"] = pd.to_datetime(polls["fieldwork_end"], errors="coerce")
    polls = polls[polls["end"].notna() & (polls["end"] < polls["edate"])]
    polls["days"] = (polls["edate"] - polls["end"]).dt.days
    polls["etype"] = polls["election_id"].str[:3]
    polls["party_id"] = polls["party_ids"].where(~polls["party_ids"].str.contains(";"), "")
    polls["err"] = polls["pct"] - polls["result"]
    polls["sample"] = pd.to_numeric(polls["sample_size"], errors="coerce")
    return polls[["election_id", "edate", "etype", "poll", "pollster_key", "end", "days", "party_raw", "party_id", "pct", "result", "err", "sample"]]


EM_FILE = ROOT / "raw" / "polls" / "electomania" / "polls.jsonl"  # pipeline/scrape/polls_electomania_archive.py
EM_WINDOW = 120        # Electomanía polls published more than 120 days before their election are not used
MUNICIPAL_DATES = ["2019-05-26", "2023-05-28"]
GEN_IDS = {v: "gen-" + k for k, v in CYCLES.items()}
CANON = {"psc": "psoe", "pspv": "psoe", "psibpsoe": "psoe", "psdeg": "psoe", "pseee": "psoe", "psn": "psoe",
         "ciudadanos": "cs", "unidaspodemos": "up", "unidospodemos": "up", "jxcat": "junts", "ehbildu": "bildu",
         "eajpnv": "pnv", "nav": "navarrasuma", "na": "navarrasuma", "cca": "cc", "nca": "nc", "frenteamplio": "sumar",
         "seacabolafiesta": "salf", "existe": "teruelexiste", "aliancacatalana": "ac", "masmadrid": "mm",
         "compromis": "compromis", "adelanteandalucia": "aa", "partidopopular": "pp"}


def canon(name):
    k = re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower())
    return CANON.get(k, k)


def official_results():
    """{election_id: {canonical party: % of valid votes}} from raw/results (generales use the poll cycle ids)."""
    out = {}
    for f in (ROOT / "raw" / "results").glob("*.csv"):
        if f.stem.endswith("_totals") or f.stem[:3] not in ("gen", "aut", "eur"):
            continue
        d = pd.read_csv(f, dtype=str, keep_default_na=False)
        d["votes"] = pd.to_numeric(d["votes"], errors="coerce")
        tot = d["votes"].sum()
        eid = GEN_IDS.get(f.stem[4:], f.stem) if f.stem.startswith("gen") else f.stem
        out[eid] = (d.assign(key=d["candidacy"].map(canon)).groupby("key")["votes"].sum() / tot * 100).to_dict()
    return out


def load_em(wiki):
    """Electomanía archive in the same long format. Generales/europeas/autonómicas: the poll goes to the next real
    election of its scope and is scored against the result row of that election's Wikipedia table, or the official
    results file when there is none (matched by canonical party name). Polls Wikipedia already has (same house,
    fieldwork ending <= 10 days before publication) are dropped. Municipales: truth = median pointsA (previous
    result) of the same town's polls published after the election."""
    recs = [json.loads(l) for l in EM_FILE.read_text("utf-8").splitlines() if l.strip()]
    wt = wiki.assign(key=wiki["party_raw"].map(canon)).groupby(["election_id", "key"])["result"].first()
    official = official_results()
    edates = {**{e: pd.Timestamp(e[-10:]) for e in official if e[-10:-6].isdigit()},
              **{GEN_IDS[d]: pd.Timestamp(d) for d in CYCLES.values()}, **wiki.groupby("election_id")["edate"].first().to_dict()}
    def truth(eid, key):
        return wt.get((eid, key), official.get(eid, {}).get(key))
    seen = wiki.groupby(["election_id", "pollster_key"])["end"].apply(list).to_dict()
    rows, mun = [], {}
    for r in recs:
        pub = pd.to_datetime(r.get("fecha") or r["pub"], dayfirst=True, errors="coerce")
        if pd.isna(pub) or not r.get("scope"):
            continue
        r["_pub"] = pub
        kind, _, where = r["scope"].partition("-")
        if kind == "mun":
            mun.setdefault(where, []).append(r)
            continue
        cands = [(e, d) for e, d in edates.items() if e[:3] == kind and (kind != "aut" or e[4:6] == where) and d > pub]
        if not cands:
            continue
        eid, edate = min(cands, key=lambda x: x[1])
        pk = pollster_key(r.get("nombreEncuesta") or "")
        if (edate - pub).days > EM_WINDOW or any(pd.Timedelta(0) <= pub - e <= pd.Timedelta(days=10) for e in seen.get((eid, pk), [])):
            continue
        for name, (pct, _) in r["vote"].items():
            res = truth(eid, canon(name))
            if pct is not None and res is not None and res > 0:
                rows.append((eid, edate, f"em{r['id']}", pk, pub - pd.Timedelta(days=2), name, float(pct), float(res), r["source"]))
    for town, rs in mun.items():
        for d in pd.to_datetime(MUNICIPAL_DATES):
            prev = [(canon(n), v[1]) for x in rs if x["_pub"] > d for n, v in x["vote"].items() if v[1] is not None]
            if not prev:
                continue
            res = pd.DataFrame(prev).groupby(0)[1].median()
            for x in rs:
                if pd.Timedelta(0) < d - x["_pub"] <= pd.Timedelta(days=EM_WINDOW):
                    for name, (pct, _) in x["vote"].items():
                        if pct is not None and canon(name) in res.index:
                            rows.append((f"mun-{town}-{d.date()}", d, f"em{x['id']}", pollster_key(x.get("nombreEncuesta") or ""),
                                         x["_pub"] - pd.Timedelta(days=2), name, float(pct), float(res[canon(name)]), x["source"]))
    em = pd.DataFrame(rows, columns=["election_id", "edate", "poll", "pollster_key", "end", "party_raw", "pct", "result", "source"])
    em["days"] = (em["edate"] - em["end"]).dt.days
    em["etype"] = em["election_id"].str[:3]
    em["party_id"] = em["party_raw"].map(lambda n: LINEAGE_OF.get(canon(n), ""))
    em["err"] = em["pct"] - em["result"]
    return em[em["days"] > 0]


LINEAGE_OF = {"pp": "pp", "psoe": "psoe", "vox": "vox", "podemos": "podemos", "sumar": "fa", "mm": "fa", "maspais": "fa",
              "compromis": "fv", "erc": "erc", "junts": "junts", "pnv": "pnv", "bildu": "bildu", "bng": "bng", "cc": "cc",
              "upn": "upn", "salf": "salf", "ac": "ac"}


def scored(df):
    return df[df["result"] >= MIN_PCT]


# ---------- learned components (always fitted on a training subset) ----------
def pollster_mae(train):
    """Per pollster: [(election_id, MAE of its final poll there)] over the training elections."""
    f = scored(train[train["days"] <= FINAL_DAYS])
    last = f.sort_values("end").groupby(["election_id", "pollster_key"])["poll"].last()
    f = f[f["poll"].isin(set(last))]
    m = f.groupby(["pollster_key", "election_id"])["err"].apply(lambda e: e.abs().mean())
    return {pk: [(eid, float(v)) for (_, eid), v in g.items()] for pk, g in m.groupby(level=0)}


def house_effects(train, k=2, cap=0.3):
    """Relative house effect per (pollster, party_id) from final polls, shrunk toward 0 with k pseudo-elections."""
    f = scored(train[(train["days"] <= FINAL_DAYS) & (train["party_id"] != "")])
    g = f.groupby(["pollster_key", "party_id", "election_id"]).agg(p=("pct", "mean"), r=("result", "first")).reset_index()
    out = {}
    for (pk, pid), h in g.groupby(["pollster_key", "party_id"]):
        rel = (h["p"].sum() - h["r"].sum()) / h["r"].sum()
        out[(pk, pid)] = float(np.clip(rel * len(h) / (len(h) + k), -cap, cap))
    return out


def industry_bias(train, k=3):
    """Relative bias of the whole industry per party_id (mean over elections of final-poll average vs result), shrunk."""
    f = scored(train[(train["days"] <= FINAL_DAYS) & (train["party_id"] != "")])
    g = f.groupby(["party_id", "election_id"]).agg(p=("pct", "mean"), r=("result", "first")).reset_index()
    g["rel"] = (g["p"] - g["r"]) / g["r"]
    return {pid: float(h["rel"].sum() / (len(h) + k)) for pid, h in g.groupby("party_id")}


# ---------- averaging methods ----------
def average(polls, at, half_life=14, window=45, maes=None, house=None, industry=None, k_mae=2, flat=False):
    """Nowcast per party_raw from polls with fieldwork ended <= `at` (pd.Timestamp)."""
    w = polls[(polls["end"] <= at) & (polls["end"] > at - pd.Timedelta(days=window))].copy()
    if w.empty:
        return {}
    w["rec"] = 1.0 if flat else 0.5 ** ((at - w["end"]).dt.days / half_life)
    if house:
        w["pct"] = [r.pct / (1 + house.get((r.pollster_key, r.party_id), 0.0)) for r in w.itertuples()]
    allm = [m for v in (maes or {}).values() for _, m in v] or [2.0]
    gm = float(np.mean(allm))
    est, wt = {}, {}
    for pk, g in w.groupby("pollster_key"):
        mine = [m for _, m in (maes or {}).get(pk, [])]
        mae = (sum(mine) + k_mae * gm) / (len(mine) + k_mae) if maes is not None else 1.0
        wt[pk] = float(g["rec"].max()) / mae ** 2
        est[pk] = {pr: float(np.average(h["pct"], weights=h["rec"])) for pr, h in g.groupby("party_raw")}
    out = {}
    pid = w.groupby("party_raw")["party_id"].first()
    for pr in pid.index:
        ks = [k for k in est if pr in est[k]]
        v = sum(wt[k] * est[k][pr] for k in ks) / sum(wt[k] for k in ks)
        if industry and pid[pr]:
            v /= 1 + industry.get(pid[pr], 0.0)
        out[pr] = v
    return out


METHODS = {
    "media simple (30 d, sin pesos)": dict(flat=True, window=30, use_maes=False, use_house=False, use_ind=False),
    "actual: recencia+fiabilidad+casa": dict(use_maes=True, use_house=True, use_ind=False),
    "actual + sesgo de la industria": dict(use_maes=True, use_house=True, use_ind=True),
    "fiabilidad+industria (sin casa)": dict(use_maes=True, use_house=False, use_ind=True),
    "actual, semivida 7 d": dict(use_maes=True, use_house=True, use_ind=False, half_life=7),
    "actual, semivida 28 d": dict(use_maes=True, use_house=True, use_ind=False, half_life=28),
}


BEST = "fiabilidad+industria (sin casa)"


K_FOLDS, REPEATS = 5, 3
ERAS = [pd.Timestamp("2021-01-01"), pd.Timestamp("2024-01-01")]  # 2018-20 | 2021-23 | 2024-26
ROLLING_YEARS = range(2021, 2027)


def group_kfold(polls, k=K_FOLDS, seed=0):
    """Grouped (a whole election per fold: its polls are correlated) and stratified by election type x era (each fold
    mixes generales/europeas/autonómicas/municipales and early/late years). Returns [(tag, train_ids, test_ids)]."""
    e = polls.groupby("election_id").agg(edate=("edate", "first"), etype=("etype", "first")).reset_index()
    e["era"] = np.searchsorted(ERAS, e["edate"].values, side="right")
    rng = np.random.default_rng(seed)
    fold = {}
    for _, g in e.groupby(["etype", "era"]):
        ids = rng.permutation(g["election_id"].to_numpy())
        start = rng.integers(k)  # random offset so small strata do not always land in fold 0
        fold.update({eid: (start + i) % k for i, eid in enumerate(ids)})
    every = set(e["election_id"])
    return [(f"r{seed}f{f}", every - {x for x, v in fold.items() if v == f}, {x for x, v in fold.items() if v == f}) for f in range(k)]


def rolling(polls):
    """Forward chaining: learn on every election before 1 January of year Y, test on year Y's elections."""
    e = polls.groupby("election_id")["edate"].first()
    return [(str(y), set(e[e < pd.Timestamp(f"{y}-01-01")].index), set(e[e.dt.year == y].index)) for y in ROLLING_YEARS]


def evaluate(polls, splits):
    """MAE (pts) of each method's nowcast vs result per (split, test election, horizon), plus per-party errors.
    splits: [(tag, train election ids, test election ids)]; the learned parts only ever see the train ids."""
    rows, errs = [], []
    for tag, train_ids, test_ids in splits:
        train = polls[polls["election_id"].isin(train_ids)]
        if train.empty:
            continue
        maes, house, ind = pollster_mae(train), house_effects(train), industry_bias(train)
        for eid, test in polls[polls["election_id"].isin(test_ids)].groupby("election_id"):
            rows_e, errs_e = score_election(eid, test, maes, house, ind)
            rows += [{**r, "split": tag} for r in rows_e]
            errs += errs_e
    return pd.DataFrame(rows), pd.DataFrame(errs)


def score_election(eid, test, maes, house, ind):
    """Rows {election, etype, h, method, mae} and BEST's per-party errors for one test election."""
    rows, errs = [], []
    edate = test["edate"].iloc[0]
    result = test.groupby("party_raw")["result"].first()
    pid = test.groupby("party_raw")["party_id"].first()
    for h in HORIZONS:
        at = edate - pd.Timedelta(days=h)
        for name, m in METHODS.items():
            kw = {k: v for k, v in m.items() if not k.startswith("use_")}
            est = average(test, at, maes=maes if m["use_maes"] else None, house=house if m["use_house"] else None,
                          industry=ind if m["use_ind"] else None, **kw)
            common = [p for p in est if result[p] >= MIN_PCT]
            if len(common) < 3:
                continue
            e = [est[p] - result[p] for p in common]
            rows.append({"election": eid, "etype": eid[:3], "h": h, "method": name, "mae": float(np.mean(np.abs(e)))})
            if name == BEST:
                errs += [{"election": eid, "etype": eid[:3], "h": h, "party": p, "party_id": pid[p], "size": result[p],
                          "err": est[p] - result[p]} for p in common]
    return rows, errs


def main():
    wiki = load()
    em = load_em(wiki) if EM_FILE.exists() else wiki.iloc[:0].assign(source="")
    polls = pd.concat([wiki.assign(source="wikipedia"), em.assign(source="electomania-" + em["source"])], ignore_index=True)
    print("Electomanía:", em.groupby("etype")["poll"].nunique().to_dict(), "encuestas nuevas (además de Wikipedia)")
    print(f"{polls['election_id'].nunique()} elecciones, {polls['poll'].nunique()} encuestas, {polls['pollster_key'].nunique()} casas")
    sp = scored(polls)
    print("\nError absoluto medio de una encuesta suelta, por distancia a la elección (pts):")
    print(sp.assign(b=pd.cut(sp["days"], [0, 7, 30, 60, 120, 365, 2000])).groupby("b", observed=True)["err"].apply(lambda e: round(e.abs().mean(), 2)).to_string())
    natl = lambda r: r[r["etype"].isin(["gen", "eur"])]
    def table(r):
        # mean over elections inside each repeat, then mean +- sd across repeats
        per = r.groupby(["rep", "method", "h"])["mae"].mean().unstack("h")
        m, s = per.groupby(level="method").mean(), per.groupby(level="method").std().fillna(0)
        return (m.round(2).astype(str) + " ±" + s.round(2).astype(str)).to_string()
    cv = [s for seed in range(REPEATS) for s in group_kfold(polls, seed=seed)]
    res, errs = evaluate(polls, cv)
    res["rep"] = res["split"].str[:2]
    e = polls.groupby("election_id")["etype"].first().value_counts().to_dict()
    print(f"\nVALIDACIÓN CRUZADA agrupada por elección y estratificada por tipo x época ({K_FOLDS} pliegues x {REPEATS} repeticiones; "
          f"elecciones: {e}). MAE de la media en test (pts), media ± desviación entre repeticiones:")
    print(table(res))
    print("Solo generales + europeas:")
    print(table(natl(res)))
    roll, _ = evaluate(polls, rolling(polls))
    print("\nVALIDACIÓN TEMPORAL MÓVIL (aprende con todo lo anterior a cada año, predice ese año). MAE en test por año, horizonte 45 d:")
    print(roll[roll["h"] == 45].pivot_table(index="method", columns="split", values="mae", aggfunc="mean").round(2).to_string())
    print("Media de todos los años por horizonte:")
    print(roll.pivot_table(index="method", columns="h", values="mae", aggfunc="mean").round(2).to_string())

    full = polls
    ind = industry_bias(full)
    print("\nSesgo relativo de la industria por partido (+ = las encuestas lo sobreestiman):")
    print({k: round(v * 100, 1) for k, v in sorted(ind.items(), key=lambda x: -abs(x[1]))})
    # Relative error of the average, sd = r(h) * party %: proportional, so seat intervals scale with expected seats.
    # r(h) = 1.4826 x size-weighted median of |err / result| (robust: misses of tiny parties or odd regional elections do
    # not blow it up), made non-decreasing in h (an average cannot get more accurate further from the vote).
    errs = errs.drop_duplicates(["election", "h", "party"])  # one error per election x horizon x party (repeats agree)
    def robust_rel(g):
        o = np.argsort(np.abs(g["err"] / g["size"]).to_numpy())
        x, w = np.abs(g["err"] / g["size"]).to_numpy()[o], g["size"].to_numpy()[o]
        return float(1.4826 * x[np.searchsorted(np.cumsum(w), w.sum() / 2)])
    rel = errs.groupby("h").apply(robust_rel).cummax().to_dict()
    rel_nat = errs[errs["etype"].isin(["gen", "eur"])].groupby("h").apply(robust_rel).to_dict()
    print("\nError relativo de la media (sigma = r(h) x % del partido); r(h):", {h: round(a, 3) for h, a in rel.items()},
          "| solo generales+europeas (5 elecciones, informativo):", {h: round(a, 3) for h, a in rel_nat.items()})
    by_party = errs[errs["h"] == 45].groupby("party_id")["err"].agg(["mean", "count"]).round(2)
    print("\nError medio de la media a 45 días por partido (pts):\n", by_party[by_party["count"] >= 3].to_string())

    model = {
        "generated": str(pd.Timestamp.today().date()),
        "elections": sorted(polls["election_id"].unique().tolist()),
        "n_polls": int(polls["poll"].nunique()),
        "pollster_mae": {k: [[eid, round(x, 3)] for eid, x in v] for k, v in pollster_mae(full).items()},
        "house_effects": {f"{k[0]}|{k[1]}": round(v, 4) for k, v in house_effects(full).items() if v},
        "industry_bias": {k: round(v, 4) for k, v in ind.items()},
        "method": BEST,
        "sigma_relative": {str(h): round(a, 4) for h, a in rel.items()},
        "validation": f"grouped stratified {K_FOLDS}-fold x {REPEATS} + rolling origin {ROLLING_YEARS.start}-{ROLLING_YEARS.stop - 1}",
        "cv_mae": res.pivot_table(index="method", columns="h", values="mae", aggfunc="mean").round(3).to_dict(),
        "rolling_mae": roll.pivot_table(index="method", columns="h", values="mae", aggfunc="mean").round(3).to_dict(),
        "sources": polls.groupby("source")["poll"].nunique().to_dict(),
    }
    path = ROOT / "data" / "poll_model.json"
    if path.exists():  # keep the recipe chosen by model_lab.py (and its industry bias) across re-runs
        old = json.loads(path.read_text("utf-8"))
        model.update({k: old[k] for k in ("config", "lab") if k in old})
        if "config" in old:
            model["industry_bias"] = old["industry_bias"]
    path.write_text(json.dumps(model, ensure_ascii=False, indent=1), "utf-8")
    print("\n-> data/poll_model.json")


if __name__ == "__main__":
    main()
