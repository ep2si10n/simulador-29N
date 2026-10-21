"""Model lab: ~100 epochs searching for the poll model that best predicts real results.

Algorithm families, from the simplest to the most advanced:
  last    latest poll of each pollster
  avg     moving / exponentially weighted average, one vote per pollster, reliability weights (1/MAE^p)
  loess   local (linear) regression in time, tricube kernel
  holt    double exponential smoothing (damped Holt) of the daily consensus
  kalman  state-space filter: local level or local linear trend, measurement noise from sample size
  gp      Gaussian-process regression in time (RBF kernel)
On top of any family: in-cycle house effects relative to the consensus, outlier removal, logit space, trend projection,
reversion to the yearly mean, learned industry bias, and a machine-learning residual correction (ridge, kNN, random
forest, gradient boosting, neural net; features include turnout) trained on the training elections. Ensembles combine
recipes (mean, median, 1/MAE^2, or NNLS stacking learned per fold). The last 40 epochs are genetic / random /
coordinate search. A turnout analysis measures how each party's polling error moves with the change in turnout.

Validation: 20 % of the elections (stratified by type x era) are held out and never used to choose anything. On the
rest, grouped stratified 5-fold CV (whole elections per fold) ranks candidates; each epoch winner is confirmed with 3
CV repetitions and rolling origin (learn the past, predict each year), and its held-out error is shown. Error = MAE in
points of the nowcast 30 and 45 days before each election, parties >= 1 %. Live state: data/lab/epochs.json (lab.html).
At the end the winning recipe goes to data/poll_model.json ("config"); build_polls.py applies it through predict().

Run from the project root:  .venv/Scripts/python pipeline/model/model_lab.py
"""
import itertools, json, os, random, time, warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

import poll_accuracy as pa

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "lab" / "epochs.json"
MODEL_FILE = ROOT / "data" / "poll_model.json"
H = [30, 45]                 # horizons scored (days before the vote): the 29N is 40 days away
ML_H = [15, 30, 45, 60]      # horizons whose training-election errors teach the ML correction
HOLD_SEED = 2026
WORKERS = max(1, (os.cpu_count() or 2) * 3 // 4)  # hardware budget: <= 75 % of the CPU threads

COMMON = dict(space="pct", outlier=None, house=0, house_rel=0, industry="pooled", k_ind=3, trend=0.0, revert=0.0,
              revert_win=365, ml=None, ml_p=None, ml_frac=1.0, ml_target="abs")
METHODS = {  # family: (display name, default parameters)
    "last": ("Última encuesta", dict(window=30, w_exp=0, k_mae=2)),
    "avg": ("Media ponderada", dict(half_life=14, window=45, w_exp=2, k_mae=2, agg="wmean", sample=False, per_pollster=True)),
    "loess": ("LOESS", dict(bw=45, degree=1, balance=True)),
    "holt": ("Holt", dict(alpha=0.3, beta=0.1, phi=0.9, kwin=180)),
    "kalman": ("Kalman", dict(q=0.05, deff=2.0, r_extra=1.0, kwin=180, slope_q=0.0, balance=False)),
    "gp": ("Proceso gaussiano", dict(ell=30, gp_sig=4.0, gp_noise=1.0, balance=True, kwin=120)),
}
NO_SPACE = {"kalman", "gp"}  # their noise parameters are in points: they always work in % space
ML_P = {"ridge": [0.1, 1, 10, 100], "knn": [5, 10, 20, 40], "rf": [3, 5, 8], "gbm": [2, 3, 4], "hgb": [2, 3, 5], "mlp": [8, 16, 32]}
COMB = {"mean": "media", "median": "mediana", "inv": "1/MAE²", "stack": "stacking NNLS"}
DOMAIN = dict(
    half_life=[3, 5, 7, 10, 14, 21, 28, 45, 60, None], window=[14, 21, 30, 45, 60, 90, 120, 180],
    w_exp=[0, 0.5, 1, 2, 3], k_mae=[0, 1, 2, 4, 8], agg=["wmean", "wmedian", "trim"], sample=[False, True],
    per_pollster=[True, False], bw=[14, 21, 30, 45, 60, 90, 120], degree=[0, 1], balance=[False, True],
    alpha=[0.05, 0.1, 0.2, 0.3, 0.5, 0.7], beta=[0, 0.02, 0.05, 0.1, 0.2, 0.4], phi=[0.5, 0.8, 0.9, 0.95, 1.0],
    q=[0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2], deff=[1, 1.5, 2, 3, 5, 8], r_extra=[0, 0.5, 1, 2, 4, 8],
    kwin=[60, 90, 120, 180, 365], slope_q=[0, 1e-5, 1e-4, 1e-3], ell=[7, 14, 21, 30, 45, 60, 90],
    gp_sig=[1, 2, 4, 8, 16], gp_noise=[0.25, 0.5, 1, 2, 4, 8], space=["pct", "logit"], outlier=[None, 2, 2.5, 3, 4],
    house=[0, 2, 5, 10, 20], house_rel=[0, 1, 2, 5, 10, 20], industry=[None, "pooled", "by_type"],
    k_ind=[0.5, 1, 2, 3, 6, 10], trend=[0, 0.1, 0.15, 0.25, 0.5, 1.0], revert=[0, 0.1, 0.2, 0.3, 0.4, 0.5],
    revert_win=[180, 365, 730], ml=[None, *ML_P], ml_frac=[0.25, 0.5, 0.75, 1.0], ml_target=["abs", "rel"])


# ---------- recipes ----------
def full(cfg):
    """Canonical recipe: family defaults filled in, keys that do not apply to the family dropped."""
    if "members" in cfg:
        return {"members": [full(m) for m in cfg["members"]], "combine": cfg.get("combine", "mean"),
                **({"wts": cfg["wts"]} if "wts" in cfg else {})}
    m = cfg.get("method", "avg")
    c = {**COMMON, **METHODS[m][1], **cfg, "method": m}
    keys = ["method", *COMMON, *METHODS[m][1]]
    if m in NO_SPACE:
        keys.remove("space")
    if c["ml"]:
        c["ml_p"] = ML_P[c["ml"]][1] if c["ml_p"] is None else c["ml_p"]
    else:
        keys = [k for k in keys if not k.startswith("ml_")]
    return {k: c[k] for k in keys}


def fmt(v):
    return "no" if v is None or v is False else "sí" if v is True else str(v)


def label(cfg):
    if "members" in cfg:
        return f"Ensemble ({COMB[cfg['combine']]}) de {len(cfg['members'])}: " + " ‖ ".join(label(m) for m in cfg["members"])
    d = full({"method": cfg["method"]})
    diff = [f"{k}={fmt(v)}" for k, v in cfg.items() if k != "method" and d.get(k, "·") != v]
    return f"{METHODS[cfg['method']][0]}: " + (", ".join(diff) or "por defecto")


def key(cfg):
    return json.dumps(cfg, sort_keys=True)


def family(cfg):
    return "ensemble" if "members" in cfg else cfg["method"]


def make_model(kind, p):
    from sklearn.ensemble import GradientBoostingRegressor, HistGradientBoostingRegressor, RandomForestRegressor
    from sklearn.linear_model import Ridge
    from sklearn.neighbors import KNeighborsRegressor
    from sklearn.neural_network import MLPRegressor
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return {
        "ridge": lambda: make_pipeline(StandardScaler(), Ridge(alpha=p)),
        "knn": lambda: make_pipeline(StandardScaler(), KNeighborsRegressor(n_neighbors=int(p), weights="distance")),
        "rf": lambda: RandomForestRegressor(n_estimators=200, max_depth=int(p), min_samples_leaf=5, random_state=0),
        "gbm": lambda: GradientBoostingRegressor(n_estimators=150, max_depth=int(p), learning_rate=0.05, subsample=0.8, random_state=0),
        "hgb": lambda: HistGradientBoostingRegressor(max_depth=int(p), learning_rate=0.05, max_iter=200, min_samples_leaf=10, random_state=0),
        "mlp": lambda: make_pipeline(StandardScaler(), MLPRegressor(hidden_layer_sizes=(int(p), int(p)), alpha=0.01, max_iter=3000,
                                                                    early_stopping=True, random_state=0)),
    }[kind]()


# ---------- turnout ----------
def turnout():
    """{election_id: (turnout %, turnout % of the previous election of the same type and territory or None)}."""
    rows = []
    for f in (ROOT / "raw" / "results").glob("*-*_totals.csv"):  # per-election files (not national_totals.csv)
        stem = f.stem[:-7]
        d = pd.read_csv(f, dtype={"province_id": str})
        eid = pa.GEN_IDS.get(stem[4:], stem) if stem.startswith("gen") else stem
        rows.append((eid, stem[:-11], stem[-10:], 100 * d["voters"].sum() / d["census"].sum()))
    t = pd.DataFrame(rows, columns=["eid", "terr", "date", "t"]).sort_values("date")
    t["prev"] = t.groupby("terr")["t"].shift()
    return {r.eid: (float(r.t), None if pd.isna(r.prev) else float(r.prev)) for r in t.itertuples()}


TURNOUT = turnout()
T_MEAN = float(np.mean([t for t, _ in TURNOUT.values()]))


# ---------- learned components (always fitted on a training subset) ----------
class Learned:
    """Per training set: pollster MAEs, industry bias, house effects; caches ML models and stacking weights."""
    def __init__(self, train):
        self.train, self.maes = train, pa.pollster_mae(train)
        self._ind, self._house, self._ml, self._base, self._stack = {}, {}, {}, {}, {}
        self.gm = float(np.mean([m for v in self.maes.values() for _, m in v] or [2.0]))

    def mae(self, pk, k):
        mine = [m for _, m in self.maes.get(pk, [])]
        return max((sum(mine) + k * self.gm) / (len(mine) + k) if mine or k else self.gm, 0.1)

    def industry(self, mode, k, etype):
        key_ = (mode, k, etype if mode == "by_type" else "")
        if key_ not in self._ind:
            pooled = pa.industry_bias(self.train, k=k)
            if mode == "by_type":  # type-specific bias, shrunk toward the pooled one
                own = self.train[self.train["etype"] == etype]
                f = pa.scored(own[(own["days"] <= pa.FINAL_DAYS) & (own["party_id"] != "")])
                g = f.groupby(["party_id", "election_id"]).agg(p=("pct", "mean"), r=("result", "first")).reset_index()
                g["rel"] = (g["p"] - g["r"]) / g["r"]
                pooled = {pid: float((h["rel"].sum() + k * pooled.get(pid, 0.0)) / (len(h) + k)) for pid, h in g.groupby("party_id")} | \
                         {pid: v for pid, v in pooled.items() if pid not in set(g["party_id"])}
            self._ind[key_] = pooled
        return self._ind[key_]

    def house(self, k):
        if k not in self._house:
            self._house[k] = pa.house_effects(self.train, k=k)
        return self._house[k]

    def base(self, g, eid, h, cfg):
        k = (key(cfg), eid, h)
        if k not in self._base:
            self._base[k] = estimate(g, g["edate"].iloc[0] - pd.Timedelta(days=h), cfg, self)
        return self._base[k]

    def ml_model(self, cfg):
        k = key(cfg)
        if k not in self._ml:
            base = full({**cfg, "ml": None})
            X, y = [], []
            for eid, g in self.train.groupby("election_id"):
                res = g.groupby("party_raw")["result"].first()
                for h in ML_H:
                    for pr, (est, f) in self.base(g, eid, h, base).items():
                        if res[pr] >= pa.MIN_PCT and est > 0.1:
                            X.append(f)
                            y.append(res[pr] - est if cfg["ml_target"] == "abs" else res[pr] / est - 1)
            self._ml[k] = make_model(cfg["ml"], cfg["ml_p"]).fit(np.array(X, float), np.array(y))
        return self._ml[k]

    def stack(self, cfg):
        k = key(cfg)
        if k not in self._stack:
            from scipy.optimize import nnls
            X, y = [], []
            for _, g in self.train.groupby("election_id"):
                res = g.groupby("party_raw")["result"].first()
                for h in H:
                    at = g["edate"].iloc[0] - pd.Timedelta(days=h)
                    ps = [predict(g, at, m, self) for m in cfg["members"]]
                    for pr in set.intersection(*map(set, ps)):
                        if res[pr] >= pa.MIN_PCT:
                            X.append([p[pr] for p in ps])
                            y.append(res[pr])
            w = nnls(np.array(X), np.array(y))[0] if X else np.ones(len(cfg["members"]))
            self._stack[k] = w / w.sum() if w.sum() > 0 else np.full(len(w), 1 / len(w))
        return self._stack[k]


# ---------- algorithms ----------
def logit(p):
    return np.log(p / (100 - p))


def expit(x):
    return 100 / (1 + np.exp(-x))


def lin_slope(g, wts=None):
    """Slope (units/day) of a weighted straight line through the polls; None with < 3 distinct dates."""
    if g["end"].nunique() < 3:
        return None
    return float(np.polyfit(-g["age"].to_numpy(float), g["pct"].to_numpy(float), 1, w=wts)[0])


def balance(g, c):
    """Weight 1 / (polls of the same pollster within +-7 days): a daily tracker counts like one poll."""
    if not c.get("balance"):
        return np.ones(len(g))
    d = g["end"].to_numpy("datetime64[D]").astype(float)
    pk = g["pollster_key"].to_numpy()
    return 1 / ((np.abs(d[:, None] - d[None, :]) <= 7) & (pk[:, None] == pk[None, :])).sum(1)


def house_rel(test, at, k, days=180, near=14):
    """In-cycle house effect (pts) per (pollster, party): mean gap to the other pollsters' polls within +-14 days,
    shrunk toward 0 with k pseudo-polls. Needs no result, only the consensus."""
    h = test[(test["end"] <= at) & (test["end"] > at - pd.Timedelta(days=days))]
    out = {}
    for pr, g in h.groupby("party_raw"):
        d = g["end"].to_numpy("datetime64[D]").astype(float)
        y, pk = g["pct"].to_numpy(float), g["pollster_key"].to_numpy()
        m = (np.abs(d[:, None] - d[None, :]) <= near) & (pk[:, None] != pk[None, :])
        cnt = m.sum(1)
        ok = cnt > 0
        dev = pd.Series(y[ok] - (m[ok] @ y) / cnt[ok]).groupby(pk[ok]).agg(["sum", "count"])
        out.update({(p, pr): r["sum"] / (r["count"] + k) for p, r in dev.iterrows()})
    return out


def agg(pairs, how):
    x, wt = np.array(sorted(pairs), float).T
    if how == "wmedian":
        cum = np.cumsum(wt)
        return float(x[np.searchsorted(cum, cum[-1] / 2)])
    if how == "trim" and len(x) >= 5:
        x, wt = x[1:-1], wt[1:-1]
    return float(np.average(x, weights=wt))


def fam_last(w, c, L):
    g = w.sort_values("end").groupby(["party_raw", "pollster_key"])["pct"].last().reset_index()
    g["wt"] = [1 / L.mae(pk, c["k_mae"]) ** c["w_exp"] for pk in g["pollster_key"]]
    return {pr: (float(np.average(h["pct"], weights=h["wt"])), None) for pr, h in g.groupby("party_raw")}


def fam_avg(w, c, L):
    w["rec"] = 1.0 if c["half_life"] is None else 0.5 ** (w["age"] / c["half_life"])
    w["wt"] = w["rec"] * (np.sqrt(w["sample"].clip(300, 5000).fillna(1000) / 1000) if c["sample"] else 1.0)
    rel = {pk: 1 / L.mae(pk, c["k_mae"]) ** c["w_exp"] for pk in w["pollster_key"].unique()}
    out = {}
    for pr, g in w.groupby("party_raw"):
        if c["per_pollster"]:
            pairs = [(float(np.average(h["pct"], weights=h["wt"])), float(h["wt"].max()) * rel[pk]) for pk, h in g.groupby("pollster_key")]
        else:
            pairs = list(zip(g["pct"], g["wt"] * g["pollster_key"].map(rel)))
        out[pr] = (agg(pairs, c["agg"]), lin_slope(g, np.sqrt(g["rec"].to_numpy(float))) if c["trend"] else None)
    return out


def fam_loess(w, c, L):
    out = {}
    for pr, g in w.groupby("party_raw"):
        t, y = -g["age"].to_numpy(float), g["pct"].to_numpy(float)
        k = (1 - (np.abs(t) / (c["bw"] + 1)) ** 3) ** 3 * balance(g, c)
        if c["degree"] == 1 and len(np.unique(t)) >= 3:
            b1, b0 = np.polyfit(t, y, 1, w=np.sqrt(k))
            out[pr] = (float(b0), float(b1))
        else:
            out[pr] = (float(np.average(y, weights=k)), None)
    return out


def fam_holt(w, c, L):
    out = {}
    for pr, g in w.groupby("party_raw"):
        s = g.groupby(["age", "pollster_key"])["pct"].mean().groupby(level=0).mean().sort_index(ascending=False)
        lvl, b, prev = float(s.iloc[0]), 0.0, float(s.index[0])
        for age, y in s.iloc[1:].items():
            dt = prev - age
            new = c["alpha"] * y + (1 - c["alpha"]) * (lvl + c["phi"] * b * dt)
            b = c["beta"] * (new - lvl) / dt + (1 - c["beta"]) * c["phi"] * b
            lvl, prev = new, age
        out[pr] = (lvl + c["phi"] * b * prev, b)  # level carried from the last poll date to `at`
    return out


def fam_kalman(w, c, L):
    out = {}
    for pr, g in w.sort_values("end", kind="stable").groupby("party_raw"):
        t, y = -g["age"].to_numpy(float), g["pct"].to_numpy(float)
        p = np.clip(y, 0.5, 99.5) / 100
        R = (c["deff"] * p * (1 - p) / g["sample"].fillna(1000).clip(300, 20000).to_numpy() * 1e4 + c["r_extra"]) / balance(g, c)
        if c["slope_q"]:  # local linear trend: state = (level, slope)
            x, P = np.array([y[0], 0.0]), np.diag([R[0], 0.01])
            for i in range(1, len(y)):
                dt = t[i] - t[i - 1]
                F = np.array([[1, dt], [0, 1]])
                x, P = F @ x, F @ P @ F.T + dt * np.diag([c["q"], c["slope_q"]])
                K = P[:, 0] / (P[0, 0] + R[i])
                x, P = x + K * (y[i] - x[0]), P - np.outer(K, P[0])
            out[pr] = (float(x[0] - x[1] * t[-1]), float(x[1]))
        else:  # local level (random walk)
            x, P = y[0], R[0]
            for i in range(1, len(y)):
                P += c["q"] * (t[i] - t[i - 1])
                K = P / (P + R[i])
                x, P = x + K * (y[i] - x), P * (1 - K)
            out[pr] = (float(x), None)
    return out


def fam_gp(w, c, L):
    out = {}
    for pr, g in w.groupby("party_raw"):
        g = g.nsmallest(300, "age")  # ponytail: O(n^3) solve, capped at the 300 latest polls
        t, y, bal = -g["age"].to_numpy(float), g["pct"].to_numpy(float), balance(g, c)
        mu = float(np.average(y, weights=bal))
        K = c["gp_sig"] * np.exp(-((t[:, None] - t[None, :]) ** 2) / (2 * c["ell"] ** 2)) + np.diag(c["gp_noise"] / bal)
        ks = c["gp_sig"] * np.exp(-(t ** 2) / (2 * c["ell"] ** 2))
        a = np.linalg.solve(K, y - mu)
        out[pr] = (mu + float(ks @ a), float((ks * t / c["ell"] ** 2) @ a))
    return out


FAMILY = {"last": fam_last, "avg": fam_avg, "loess": fam_loess, "holt": fam_holt, "kalman": fam_kalman, "gp": fam_gp}


def estimate(test, at, cfg, L):
    """{party_raw: (estimate %, ML features)} for one election from polls with fieldwork ended <= at (before ML)."""
    m = cfg["method"]
    days = cfg["window"] if m in ("last", "avg") else cfg["bw"] if m == "loess" else cfg["kwin"]
    w = test[(test["end"] <= at) & (test["end"] > at - pd.Timedelta(days=days))].copy()
    if w.empty:
        return {}
    if cfg["house"]:
        hs = L.house(cfg["house"])
        w["pct"] = w["pct"] / (1 + np.array([hs.get(k, 0.0) for k in zip(w["pollster_key"], w["party_id"])]))
    if cfg["house_rel"]:
        hr = house_rel(test, at, cfg["house_rel"])
        w["pct"] = w["pct"] - np.array([hr.get(k, 0.0) for k in zip(w["pollster_key"], w["party_raw"])])
    if cfg["outlier"]:
        dev = w["pct"] - w.groupby("party_raw")["pct"].transform("median")
        mad = 1.4826 * dev.abs().groupby(w["party_raw"]).transform("median")
        w = w[dev.abs() <= cfg["outlier"] * np.maximum(mad, 0.3)].copy()
    w["pct"] = w["pct"].clip(0.1, 99)
    w["age"] = (at - w["end"]).dt.days.astype(float)
    disp = w.groupby(["party_raw", "pollster_key"])["pct"].mean().groupby(level=0).agg(["std", "count"]).fillna(0.0)
    lg = cfg.get("space") == "logit"
    if lg:
        w["pct"] = logit(w["pct"])
    raw = FAMILY[m](w, cfg, L)

    edate, etype = test["edate"].iloc[0], test["etype"].iloc[0]
    h = (edate - at).days
    yr = test[(test["end"] <= at) & (test["end"] > at - pd.Timedelta(days=cfg["revert_win"]))]
    year = yr.groupby(["party_raw", "pollster_key"])["pct"].mean().groupby(level=0).mean()
    pid = w.groupby("party_raw")["party_id"].first()
    ind = L.industry(cfg["industry"], cfg["k_ind"], etype) if cfg["industry"] else {}
    t_prev = TURNOUT.get(test["election_id"].iloc[0], (None, None))[1] if "election_id" in test else None
    t_prev = T_MEAN if t_prev is None else t_prev
    out = {}
    for pr, (v, slope) in raw.items():
        if cfg["trend"] and slope is None:
            slope = lin_slope(w[w["party_raw"] == pr])
        vt = v + cfg["trend"] * slope * h if cfg["trend"] and slope is not None else v
        lvl, est = (float(expit(v)), float(expit(vt))) if lg else (v, vt)
        trend_part, ym = est - lvl, year.get(pr)
        if cfg["revert"] and ym is not None:
            est = (1 - cfg["revert"]) * est + cfg["revert"] * ym
        b = ind.get(pid[pr], 0.0) if pid[pr] else 0.0
        est /= 1 + b
        sd, n = disp.loc[pr] if pr in disp.index else (0.0, 0)
        out[pr] = (float(est), [est, np.log(max(est, 0.1)), trend_part, 0.0 if ym is None else ym - est, sd, n, h / 30,
                                etype == "gen", etype == "eur", b, t_prev, t_prev * est / 100])
    return out


def predict(test, at, cfg, L):
    """Nowcast {party_raw: %} for one election from polls with fieldwork ended <= at."""
    if "members" in cfg:
        ps = [predict(test, at, m, L) for m in cfg["members"]]
        common = set.intersection(*map(set, ps))
        if cfg["combine"] == "median":
            return {p: float(np.median([x[p] for x in ps])) for p in common}
        wt = L.stack(cfg) if cfg["combine"] == "stack" else cfg.get("wts") or np.ones(len(ps))
        return {p: float(np.average([x[p] for x in ps], weights=wt)) for p in common}
    res = estimate(test, at, cfg, L)
    if not cfg["ml"] or not res:
        return {pr: e for pr, (e, _) in res.items()}
    corr = L.ml_model(cfg).predict(np.array([f for _, f in res.values()], float)) * cfg["ml_frac"]
    return {pr: e + d if cfg["ml_target"] == "abs" else e * (1 + d) for (pr, (e, _)), d in zip(res.items(), corr)}


def score(polls, cfg, splits, learned):
    rows = []
    for tag, _, test_ids in splits:
        L = learned[tag]
        for eid, test in polls[polls["election_id"].isin(test_ids)].groupby("election_id"):
            result = test.groupby("party_raw")["result"].first()
            for h in H:
                est = predict(test, test["edate"].iloc[0] - pd.Timedelta(days=h), cfg, L)
                common = [p for p in est if result[p] >= pa.MIN_PCT]
                if len(common) >= 3:
                    rows.append((tag[:2], eid[:3], h, float(np.mean([abs(est[p] - result[p]) for p in common]))))
    d = pd.DataFrame(rows, columns=["rep", "etype", "h", "mae"])
    nat = d[d["etype"].isin(["gen", "eur"])]
    return {"mae": round(float(d["mae"].mean()), 4), "mae_nat": round(float(nat["mae"].mean()), 4) if len(nat) else None,
            "by_h": {str(h): round(float(g["mae"].mean()), 4) for h, g in d.groupby("h")}}


def turnout_effect(polls, cfg, learned):
    """Out-of-fold error (result - nowcast at 30 d) per party vs change in turnout: slope pts per +1 pt of turnout."""
    rows = []
    for tag, _, test_ids in SPLITS["cv0"]:
        for eid, test in polls[polls["election_id"].isin(test_ids)].groupby("election_id"):
            t, prev = TURNOUT.get(eid, (None, None))
            if prev is None:
                continue
            result = test.groupby("party_raw")["result"].first()
            pid = test.groupby("party_raw")["party_id"].first()
            for pr, v in predict(test, test["edate"].iloc[0] - pd.Timedelta(days=30), cfg, learned[tag]).items():
                if result[pr] >= pa.MIN_PCT and pid[pr]:
                    rows.append((eid, pid[pr], t - prev, result[pr] - v))
    d = pd.DataFrame(rows, columns=["eid", "party", "dt", "err"])
    out = []
    for p, g in d.groupby("party"):
        if len(g) >= 6 and g["dt"].std() > 0:
            slope = np.polyfit(g["dt"], g["err"], 1)[0]
            out.append({"party": p, "n": len(g), "slope": round(float(slope), 3), "r": round(float(g["dt"].corr(g["err"])), 2)})
    big = d.assign(a=d["err"].abs(), ad=d["dt"].abs())
    return {"by_party": sorted(out, key=lambda x: -abs(x["slope"])), "n_elections": int(d["eid"].nunique()),
            "abs_corr": round(float(big["ad"].corr(big["a"])), 2) if len(big) > 2 else None,
            "dt_range": [round(float(d["dt"].min()), 1), round(float(d["dt"].max()), 1)] if len(d) else None}


# ---------- data / workers ----------
SPLITS = {}


def setup():
    warnings.filterwarnings("ignore")
    wiki = pa.load()
    em = pa.load_em(wiki) if pa.EM_FILE.exists() else wiki.iloc[:0]
    polls = pd.concat([wiki, em], ignore_index=True)
    hold = pa.group_kfold(polls, seed=HOLD_SEED)[0][2]
    dev = polls[~polls["election_id"].isin(hold)]
    SPLITS.update({f"cv{s}": pa.group_kfold(dev, seed=s) for s in range(3)})
    SPLITS["roll"] = [s for s in pa.rolling(dev) if s[1] and s[2]]
    SPLITS["hold"] = [("hold", set(dev["election_id"]), hold)]
    learned = {tag: Learned(polls[polls["election_id"].isin(tr)]) for ss in SPLITS.values() for tag, tr, _ in ss}
    return polls, learned


G = None


def _init():
    global G
    G = setup()


def _job(cfg, which):
    try:
        if which == "turnout":
            return turnout_effect(G[0], cfg, G[1])
        return score(G[0], cfg, SPLITS[which], G[1])
    except Exception as e:  # a numerically broken candidate must not stop the lab
        return {"error": f"{type(e).__name__}: {e}"[:200]}


# ---------- epoch generators ----------
NAIVE = dict(method="avg", half_life=None, window=30, w_exp=0, industry=None)
BASE = dict(method="avg")  # the pre-lab recipe = the family defaults


def prev_config():
    return json.loads(MODEL_FILE.read_text("utf-8")).get("config", BASE) if MODEL_FILE.exists() else BASE


def pure(m, **kw):
    return {"method": m, "industry": None, **kw}


def grid(base, **axes):
    out = [dict(base)]
    for k, vals in axes.items():
        out += [dict(base, **{k: v}) for v in vals if v != base.get(k)]
    return out


def cart(base, **axes):
    return [dict(base, **dict(zip(axes, vs))) for vs in itertools.product(*axes.values())]


def near(k, v):
    """Neighbouring values of parameter k: x0.5..x1.5 around a number, the whole domain otherwise."""
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v:
        vals = {v * f for f in (0.5, 0.75, 1.25, 1.5)}
        return sorted({round(x) for x in vals} if isinstance(v, int) else {round(x, 4) for x in vals})
    return DOMAIN.get(k, [])


def refine(b, keys):
    return grid(b, **{k: near(k, b[k]) for k in keys if k in b})


class Ctx:
    """What the generators may look at: tested singles ranked by CV error, best per family, an RNG."""
    def __init__(self, tried, rng, best):
        uniq = {key(c): (c, m) for c, m in tried if "members" not in c}
        ranked = sorted(uniq.values(), key=lambda x: x[1])
        self.top, self.mae = [c for c, _ in ranked], {key(c): m for c, m in ranked}
        self.fam = {}
        for c in self.top:
            self.fam.setdefault(c["method"], c)
        self.ml = next((c for c in self.top if c.get("ml")), None)
        self.rng, self.best = rng, best

    def fb(self, b, m, **kw):
        """Best recipe of family m so far, with b's family-independent corrections."""
        src = self.fam.get(m, {"method": m})
        return {**{k: v for k, v in src.items() if k not in COMMON}, **{k: v for k, v in b.items() if k in COMMON}, "method": m, **kw}

    def distinct(self, n):
        """Top n singles, skipping near-copies (same family and same corrections)."""
        out, seen = [], set()
        for c in self.top:
            sig = (c["method"], c.get("house_rel"), c.get("ml"), c.get("space"), c.get("trend"))
            if sig not in seen:
                seen.add(sig)
                out.append(c)
            if len(out) == n:
                break
        return out

    def ens(self, members, comb):
        members = [full(m) for m in members]
        e = {"members": members, "combine": comb}
        if comb == "inv":
            e["wts"] = [round(1 / self.mae.get(key(m), 3.0) ** 2, 4) for m in members]
        return e


def mutate(c, rng):
    c = dict(c)
    if rng.random() < 0.15:  # jump to another family keeping the corrections
        c = {**{k: v for k, v in c.items() if k in COMMON}, "method": rng.choice([m for m in METHODS if m != c["method"]])}
        return full(c)
    keys = [k for k in full(c) if k in DOMAIN or k == "ml_p"]
    for k in rng.sample(keys, rng.randint(1, min(3, len(keys)))):
        if k == "ml_p":
            c[k] = rng.choice(ML_P[c["ml"]])
        else:
            c[k] = rng.choice(DOMAIN[k])
            if k == "ml" and c[k]:
                c["ml_p"] = rng.choice(ML_P[c[k]])
    return full(c)


def crossover(a, b, rng):
    if a["method"] == b["method"]:
        child = {k: (b.get(k, v) if rng.random() < 0.5 else v) for k, v in a.items()}
    else:
        child = {**a, **{k: b[k] for k in COMMON if k in b and rng.random() < 0.5}}
    if child.get("ml") and child.get("ml_p") not in ML_P[child["ml"]]:
        child["ml_p"] = ML_P[child["ml"]][1]
    return full(child)


def random_cfg(rng):
    m = rng.choice(list(METHODS))
    c = {"method": m, **{k: rng.choice(DOMAIN[k]) for k in [*COMMON, *METHODS[m][1]] if k in DOMAIN and k != "ml"}}
    c["ml"] = rng.choice(list(ML_P)) if rng.random() < 0.2 else None
    return full(c)


def genetic(b, x):
    par = x.top[:8] or [full(b)]
    pick = lambda: par[min(int(x.rng.expovariate(0.4)), len(par) - 1)]  # favour the best parents
    return [mutate(pick(), x.rng) for _ in range(6)] + [crossover(pick(), pick(), x.rng) for _ in range(6)]


def coordinate(b, x):
    out = [b]
    for k, v in b.items():
        if k in DOMAIN and v in DOMAIN[k]:
            i = DOMAIN[k].index(v)
            out += [dict(b, **{k: DOMAIN[k][j]}) for j in (i - 1, i + 1) if 0 <= j < len(DOMAIN[k])]
    return out


ML_NAMES = {"ridge": "Ridge (regresión lineal regularizada)", "knn": "k vecinos más cercanos", "rf": "random forest",
            "gbm": "gradient boosting", "hgb": "histogram gradient boosting", "mlp": "red neuronal (MLP 2 capas)"}

EPOCHS = [
    # --- simple algorithms, without learned corrections ---
    ("Punto de partida", "Referencias: media simple de 30 días, última encuesta de cada casa, mediana, receta inicial y receta del laboratorio anterior.",
     lambda b, x: [NAIVE, pure("last"), dict(NAIVE, agg="wmedian"), BASE, prev_config()]),
    ("Última encuesta de cada casa", "El método más sencillo: la encuesta más reciente de cada encuestadora, promediadas.",
     lambda b, x: [pure("last", window=w) for w in (7, 14, 21, 30, 45, 60, 90)]),
    ("Media móvil simple", "Media sin pesos de las encuestas de los últimos N días (una voz por casa).",
     lambda b, x: [dict(NAIVE, window=w) for w in (7, 14, 21, 30, 45, 60, 90, 120)]),
    ("Media por encuesta", "Cada encuesta cuenta igual (las casas con tracking diario pesan más).",
     lambda b, x: [dict(NAIVE, window=w, per_pollster=False) for w in (14, 30, 45, 60)]),
    ("Mediana y media recortada", "Estimadores robustos: mediana ponderada entre casas o media sin la casa más alta y la más baja.",
     lambda b, x: [dict(NAIVE, window=w, agg=a) for w in (30, 45, 60) for a in ("wmedian", "trim")]),
    ("Media exponencial (EWMA)", "Peso que se reduce a la mitad cada N días (semivida), sin más correcciones.",
     lambda b, x: [dict(NAIVE, window=120, half_life=hl) for hl in (3, 5, 7, 10, 14, 21, 28, 45)]),
    # --- weighted average ---
    ("Semivida de la recencia", "Media ponderada: cuánto pesan las encuestas recientes.",
     lambda b, x: grid(b, half_life=[5, 7, 10, 14, 21, 28, 45, None])),
    ("Ventana de encuestas", "Media ponderada: cuántos días atrás se miran.",
     lambda b, x: grid(b, window=[21, 30, 45, 60, 90, 120, 180])),
    ("Fiabilidad de las casas", "Peso = 1/MAE^p de cada encuestadora en elecciones pasadas (p = 0 la ignora).",
     lambda b, x: grid(b, w_exp=[0, 0.5, 1, 2, 3, 4])),
    ("Contracción del historial de cada casa", "Cuánto se acerca el MAE de una casa a la media global (k elecciones ficticias).",
     lambda b, x: [dict(b, w_exp=we, k_mae=k) for we in (1, 2) for k in (0, 1, 4, 8, 16)]),
    ("Tamaño de muestra y una voz por casa", "Peso √n por muestra; promediar por casa o por encuesta.",
     lambda b, x: cart(b, sample=[False, True], per_pollster=[True, False])),
    ("Agregación robusta", "Media, mediana ponderada o media recortada entre casas sobre la mejor receta.",
     lambda b, x: grid(b, agg=["wmean", "wmedian", "trim"])),
    # --- corrections ---
    ("Sesgo de la industria: tipo", "Corregir lo que las encuestas sobrestiman o infraestiman de cada partido: no, común, o por tipo de elección.",
     lambda b, x: grid(b, industry=[None, "pooled", "by_type"])),
    ("Sesgo de la industria: contracción", "Cuánto se fía del sesgo histórico (k bajo = corrección fuerte).",
     lambda b, x: grid(b, k_ind=[0.25, 0.5, 1, 2, 3, 6, 10, 20])),
    ("Efecto casa frente al resultado", "Sesgo propio de cada encuestadora medido contra resultados reales.",
     lambda b, x: grid(b, house=[2, 5, 10, 20, 40])),
    ("Efecto casa relativo al consenso", "Sesgo de cada casa frente a las demás casas en el mismo ciclo (no necesita resultados).",
     lambda b, x: grid(b, house_rel=[0.5, 1, 2, 5, 10, 20])),
    ("Encuestas atípicas", "Quitar encuestas a más de z desviaciones robustas de la mediana.",
     lambda b, x: grid(b, outlier=[1.5, 2, 2.5, 3, 4])),
    ("Espacio logit", "Promediar log-odds en vez de porcentajes (mejor para partidos pequeños).",
     lambda b, x: grid(b, space=["pct", "logit"])),
    ("Tendencia", "Proyectar la pendiente reciente hasta el día de la elección (fracción).",
     lambda b, x: grid(b, trend=[0.05, 0.1, 0.15, 0.25, 0.5, 1.0])),
    ("Reversión: fuerza", "Mezclar con la media del último año.",
     lambda b, x: grid(b, revert=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6])),
    ("Reversión: horizonte", "Media de referencia de 3, 6, 12 o 24 meses.",
     lambda b, x: grid(b, revert_win=[90, 180, 365, 730])),
    ("Combinación de correcciones", "Efecto casa relativo × atípicas × logit.",
     lambda b, x: cart(b, house_rel=[0, 5], outlier=[None, 3], space=["pct", "logit"])),
    ("Ronda fina: media ponderada", "Segunda pasada alrededor de la mejor receta.",
     lambda b, x: refine(b, ["half_life", "window", "k_ind", "trend", "revert"])),
    # --- LOESS ---
    ("LOESS: ancho de banda", "Regresión local en el tiempo (núcleo tricúbico): días de ancho.",
     lambda b, x: [x.fb(b, "loess", bw=v) for v in (14, 21, 30, 45, 60, 90, 120)]),
    ("LOESS: grado y trackers", "Media local (grado 0) o recta local (grado 1); trackers equilibrados o no.",
     lambda b, x: cart(x.fb(b, "loess"), degree=[0, 1], balance=[False, True])),
    ("LOESS + correcciones", "Casa relativa, atípicas, logit y tendencia sobre LOESS.",
     lambda b, x: grid(x.fb(b, "loess"), house_rel=[0, 5, 10], outlier=[None, 3], space=["pct", "logit"], trend=[0, 0.25, 0.5])),
    ("LOESS: ronda fina", "Segunda pasada sobre LOESS.",
     lambda b, x: refine(x.fb(b, "loess"), ["bw", "trend", "revert", "k_ind"])),
    # --- Holt ---
    ("Holt: suavizado del nivel (α)", "Suavizado exponencial doble del consenso diario.",
     lambda b, x: grid(x.fb(b, "holt"), alpha=DOMAIN["alpha"])),
    ("Holt: suavizado de la pendiente (β)", "Rapidez con la que Holt aprende la tendencia.",
     lambda b, x: grid(x.fb(b, "holt"), beta=DOMAIN["beta"])),
    ("Holt: amortiguación y proyección", "Tendencia amortiguada φ × fracción proyectada a la elección.",
     lambda b, x: cart(x.fb(b, "holt"), phi=[0.5, 0.8, 0.95, 1.0], trend=[0, 0.25, 0.5])),
    ("Holt: historia y correcciones", "Días de historia, casa relativa y logit.",
     lambda b, x: grid(x.fb(b, "holt"), kwin=[60, 90, 180, 365], house_rel=[5, 10], space=["logit"])),
    ("Holt: ronda fina", "Segunda pasada sobre Holt.",
     lambda b, x: refine(x.fb(b, "holt"), ["alpha", "beta", "phi", "trend"])),
    # --- Kalman ---
    ("Kalman nivel local: ruido del proceso", "Filtro de Kalman (paseo aleatorio): cuánto puede moverse la intención de voto por día (q).",
     lambda b, x: grid(x.fb(b, "kalman"), q=DOMAIN["q"])),
    ("Kalman: ruido de medida", "Efecto de diseño sobre el error de muestreo p(1-p)/n.",
     lambda b, x: grid(x.fb(b, "kalman"), deff=DOMAIN["deff"])),
    ("Kalman: error no muestral", "Varianza extra de cada encuesta (método, casa).",
     lambda b, x: grid(x.fb(b, "kalman"), r_extra=DOMAIN["r_extra"])),
    ("Kalman: historia usada", "Días de historia del filtro.",
     lambda b, x: grid(x.fb(b, "kalman"), kwin=DOMAIN["kwin"])),
    ("Kalman: trackers y casas", "Equilibrar trackers y quitar el efecto casa relativo.",
     lambda b, x: cart(x.fb(b, "kalman"), balance=[False, True], house_rel=[0, 5, 10])),
    ("Kalman tendencia local: pendiente", "Estado = nivel + pendiente; ruido de la pendiente.",
     lambda b, x: grid(x.fb(b, "kalman"), slope_q=[1e-5, 3e-5, 1e-4, 3e-4, 1e-3])),
    ("Kalman tendencia local: proyección", "Fracción de la pendiente filtrada proyectada a la elección.",
     lambda b, x: cart(x.fb(b, "kalman", slope_q=x.fb(b, "kalman").get("slope_q") or 1e-4), trend=[0.1, 0.25, 0.5, 1.0])),
    ("Kalman: ronda fina", "Segunda pasada sobre Kalman.",
     lambda b, x: refine(x.fb(b, "kalman"), ["q", "deff", "r_extra", "trend"])),
    # --- Gaussian process ---
    ("Proceso gaussiano: escala temporal", "Regresión GP (núcleo RBF): ℓ = días en que la intención de voto cambia apreciablemente.",
     lambda b, x: grid(x.fb(b, "gp"), ell=DOMAIN["ell"])),
    ("Proceso gaussiano: señal", "Varianza a priori de los movimientos.",
     lambda b, x: grid(x.fb(b, "gp"), gp_sig=DOMAIN["gp_sig"])),
    ("Proceso gaussiano: ruido", "Ruido de cada encuesta.",
     lambda b, x: grid(x.fb(b, "gp"), gp_noise=DOMAIN["gp_noise"])),
    ("Proceso gaussiano: casas, trackers, proyección", "Correcciones sobre el GP.",
     lambda b, x: grid(x.fb(b, "gp"), balance=[False], house_rel=[5, 10], trend=[0.25, 0.5], kwin=[60, 180])),
    ("Proceso gaussiano: ronda fina", "Segunda pasada sobre el GP.",
     lambda b, x: refine(x.fb(b, "gp"), ["ell", "gp_sig", "gp_noise"])),
    # --- family duel ---
    ("Duelo de familias", "La mejor versión de cada familia con las correcciones de la mejor receta.",
     lambda b, x: [x.fb(b, m) for m in METHODS]),
    ("Duelo de familias sin correcciones", "Cada familia sola, sin sesgo de industria, tendencia ni reversión: potencia bruta del algoritmo.",
     lambda b, x: [{**{k: v for k, v in x.fam.get(m, {"method": m}).items() if k not in COMMON}, "method": m, "industry": None} for m in METHODS]),
    # --- machine learning on top ---
    *[(f"Corrección ML: {ML_NAMES[k]}", "Modelo que aprende, en las elecciones de entrenamiento, el error residual de la mejor receta a partir de: "
       "estimación, tendencia, distancia a la media anual, dispersión entre casas, nº de casas, horizonte, tipo de elección, "
       "sesgo de industria y participación de la elección anterior.",
       (lambda kk: lambda b, x: [dict(b, ml=kk, ml_p=p) for p in ML_P[kk]])(k)) for k in ML_P],
    ("Corrección ML: objetivo relativo", "Aprender el error relativo (%) en vez de en puntos.",
     lambda b, x: [dict(c, ml_target="rel") for c in [c for c in x.top if c.get("ml")][:4]]),
    ("Corrección ML: fracción aplicada", "Aplicar solo parte de la corrección aprendida (contracción).",
     lambda b, x: grid(x.ml or dict(b, ml="ridge"), ml_frac=DOMAIN["ml_frac"])),
    ("Corrección ML sobre otras familias", "El mejor corrector ML aplicado a la mejor versión de cada familia.",
     lambda b, x: [x.fb(b, m, **{k: v for k, v in (x.ml or {"ml": "ridge"}).items() if k.startswith("ml")}) for m in METHODS]),
    # --- ensembles ---
    ("Ensemble: media", "Promedio de las mejores recetas distintas.",
     lambda b, x: [x.ens(x.distinct(n), "mean") for n in (2, 3, 5, 8)]),
    ("Ensemble: mediana", "Mediana de las mejores recetas distintas.",
     lambda b, x: [x.ens(x.distinct(n), "median") for n in (3, 5, 7)]),
    ("Ensemble: ponderado 1/MAE²", "Media ponderada por la precisión de cada receta.",
     lambda b, x: [x.ens(x.distinct(n), "inv") for n in (3, 5, 8)]),
    ("Ensemble diverso", "La mejor receta de cada familia, combinadas.",
     lambda b, x: [x.ens(list(x.fam.values()), c) for c in ("mean", "median", "inv")]),
    ("Stacking NNLS", "Pesos de cada receta aprendidos en las elecciones de entrenamiento (mínimos cuadrados no negativos).",
     lambda b, x: [x.ens(x.distinct(3), "stack"), x.ens(x.distinct(5), "stack"), x.ens(list(x.fam.values()), "stack")]),
    # --- automatic search ---
    *[(f"Búsqueda {kind} {i + 1}", idea, gen) for i in range(10) for kind, idea, gen in [
        ("genética", "Algoritmo genético: 6 mutaciones y 6 cruces de las 8 mejores recetas.", genetic),
        ("genética", "Algoritmo genético: 6 mutaciones y 6 cruces de las 8 mejores recetas.", genetic),
        ("aleatoria", "12 recetas al azar en todo el espacio (cualquier familia y corrección): explora zonas no visitadas.",
         lambda b, x: [random_cfg(x.rng) for _ in range(12)]),
        ("por coordenadas", "Cada parámetro de la mejor receta un paso arriba y abajo.", coordinate)]],
    # --- final ---
    ("Ronda final: ajuste fino global", "Todos los parámetros numéricos de la mejor receta ±25-50 %.",
     lambda b, x: refine(b, [k for k, v in b.items() if isinstance(v, (int, float)) and not isinstance(v, bool)])),
    ("Ensembles finales", "Combinaciones finales de las mejores recetas.",
     lambda b, x: [x.ens(x.distinct(n), c) for n in (3, 5) for c in ("mean", "median", "inv", "stack")]),
    ("Comparación final", "Media simple, receta inicial, receta del laboratorio anterior, mejor receta única y mejor global.",
     lambda b, x: [NAIVE, BASE, prev_config(), b, x.best]),
]


# ---------- main ----------
def main():
    polls, _ = setup()
    hold = sorted(SPLITS["hold"][0][2])
    state = {"status": "corriendo", "started": time.strftime("%Y-%m-%d %H:%M"), "total": len(EPOCHS), "workers": WORKERS,
             "metric": "MAE (puntos) de la media de encuestas a 30 y 45 días de cada elección. Selección: validación cruzada agrupada por "
                       "elección y estratificada por tipo × época (5 pliegues; el ganador se confirma con 3 repeticiones y con validación "
                       f"temporal 2021-2026). Prueba final: {len(hold)} elecciones apartadas desde el principio que nunca se usan para elegir.",
             "n_elections": int(polls["election_id"].nunique()), "n_polls": int(polls["poll"].nunique()), "hold": hold,
             "n_tested": 0, "epochs": []}
    save = lambda: (OUT.parent.mkdir(parents=True, exist_ok=True), OUT.write_text(json.dumps(state, ensure_ascii=False, indent=1), "utf-8"))
    seen, tried, rng = {}, [], random.Random(29)
    best = {"single": (full(BASE), None), "any": (full(BASE), None)}
    with ProcessPoolExecutor(WORKERS, initializer=_init) as ex:
        for n, (name, idea, gen) in enumerate(EPOCHS, 1):
            t0 = time.time()
            ctx = Ctx(tried, rng, best["any"][0])
            cands = list({key(c): c for c in map(full, gen(best["single"][0], ctx))}.values())
            ep = {"n": n, "name": name, "idea": idea, "tested": [], "status": "corriendo"}
            state["epochs"].append(ep)
            futs = {ex.submit(_job, c, "cv0"): c for c in cands if key(c) not in seen}
            for c in cands:
                if key(c) in seen:
                    ep["tested"].append({"label": label(c), "family": family(c), "repeat": True, **seen[key(c)]})
            save()
            for f in as_completed(futs):
                c, s = futs[f], f.result()
                seen[key(c)] = s
                state["n_tested"] += 1
                ep["tested"].append({"label": label(c), "family": family(c), **s})
                if "error" not in s:
                    tried.append((c, s["mae"]))
                save()
            ok = [c for c in cands if "error" not in seen[key(c)]]
            if not ok:
                ep.update({"status": "hecho", "winner": None})
                continue
            cfg = min(ok, key=lambda c: seen[key(c)]["mae"])
            jobs = {w: ex.submit(_job, cfg, w) for w in ("cv1", "cv2", "roll", "hold")}
            r = {w: j.result() for w, j in jobs.items()}
            reps = [seen[key(cfg)], r["cv1"], r["cv2"]]
            conf = {"mae": round(float(np.mean([x["mae"] for x in reps])), 4), "sd": round(float(np.std([x["mae"] for x in reps], ddof=1)), 4)}
            slot = "any" if "members" in cfg else "single"
            adopted = []
            for s_ in ([slot, "any"] if slot == "single" else ["any"]):
                if best[s_][1] is None or conf["mae"] < best[s_][1] - 0.002:  # ignore gains below 0.002 pts (noise)
                    best[s_] = (cfg, conf["mae"])
                    adopted.append(s_)
            ep.update({"status": "hecho", "winner": label(cfg), "family": family(cfg), "cv3": conf, "rolling": r["roll"],
                       "hold": r["hold"], "adopted": bool(adopted), "best_so_far": label(best["any"][0]),
                       "best_mae": best["any"][1], "secs": round(time.time() - t0)})
            if "any" in adopted:
                state["best_hold"] = r["hold"].get("mae")
            ep["best_hold"] = state.get("best_hold")
            state["naive"] = seen.get(key(full(NAIVE)), {}).get("mae")
            save()
        final = best["any"][0]
        state["turnout"] = ex.submit(_job, final, "turnout").result()
    state["status"] = "terminado"
    state["best"] = {"label": label(final), "mae": best["any"][1], "hold": state.get("best_hold"), "config": final}
    save()
    model = json.loads(MODEL_FILE.read_text("utf-8"))
    model["config"] = final
    full_L = Learned(polls)
    model["industry_bias"] = {k: round(v, 4) for k, v in full_L.industry("pooled", final.get("k_ind", 3), "gen").items()}
    model["lab"] = {"best": label(final), "cv_mae": best["any"][1], "hold_mae": state.get("best_hold"),
                    "naive_mae": state.get("naive"), "epochs": len(EPOCHS), "tested": state["n_tested"],
                    "turnout": state["turnout"]}
    MODEL_FILE.write_text(json.dumps(model, ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    main()
