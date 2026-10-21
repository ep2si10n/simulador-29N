"""Polls for non-general Spanish elections, 2022-10-19 .. 2026-10-19.

Source: the "Voting intention estimates" table of each English Wikipedia election article
(the polls are embedded in the article; there are no separate "Opinion polling for ..." pages).
Cached HTML: raw/sources/polls_elections/. Output: raw/polls/election_polls.csv (long format).

Column label -> party_ids maps are explicit per election: an unmapped label stops the run.
Run from the project root:  .venv/Scripts/python pipeline/scrape/polls_elections.py
"""
import csv, datetime as dt, re, time, unicodedata
from collections import Counter
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "raw" / "sources" / "polls_elections"
OUT = ROOT / "raw" / "polls" / "election_polls.csv"
UA = {"User-Agent": "Elecciones29N/0.1 (personal research project)"}
HEADER = ["election_id", "election_date", "pollster", "pollster_key", "commissioner", "fieldwork_start",
          "fieldwork_end", "sample_size", "is_result", "is_aggregate", "party_raw", "party_ids", "pct"]
MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}

UP_FAMILY = "fa;podemos"  # Unidas Podemos joint list (IU + Podemos), confirmed by results files
UP_PODEMOS = "podemos"    # "Unidas Podemos" column that is Podemos alone (AR 2023, AS 2023, CL 2026)
UP_AR23, UP_AS23, UP_CN23, UP_CL26 = UP_PODEMOS, UP_PODEMOS, UP_FAMILY, UP_PODEMOS

# election_id, date, Wikipedia article, column label -> party_ids ("" = keep the row, no lineage)
ELECTIONS = [
    ("aut-AR-2023-05-28", "2023-05-28", "2023 Aragonese regional election", {
        "PSOE": "psoe", "PP": "pp", "CS": "", "Podemos": "podemos", "CHA": "", "Vox": "vox", "PAR": "",
        "IU": "fa", "Unidas Podemos": UP_AR23, "Mas País–CHA–Equo": "fa", "Existe": "", "ZGZ": ""}),
    ("aut-AS-2023-05-28", "2023-05-28", "2023 Asturian regional election", {
        "PSOE": "psoe", "PP": "pp", "CS": "", "Podemos": "podemos", "CxAst": "", "Foro": "", "Vox": "vox",
        "Unidas Podemos": UP_AS23, "SOS": ""}),
    ("aut-IB-2023-05-28", "2023-05-28", "2023 Balearic regional election", {
        "PSIB–PSOE": "psoe", "PP": "pp", "CS": "", "Unidas Podemos": UP_FAMILY, "Més": "", "Vox": "vox",
        "El Pi": "", "MxMe": "", "GxF": "", "Sa Unió": "pp"}),
    ("aut-CN-2023-05-28", "2023-05-28", "2023 Canarian regional election", {
        "PSOE": "psoe", "CCa": "cc", "PP": "pp", "NCa": "", "Podemos": "podemos", "CS": "", "Vox": "vox",
        "IUC": "fa", "ASG": "", "UxGC": "", "AHI": "", "USP": UP_CN23, "DVC": ""}),
    ("aut-CB-2023-05-28", "2023-05-28", "2023 Cantabrian regional election", {
        "PRC": "", "PP": "pp", "PSOE": "psoe", "CS": "", "Vox": "vox", "Podemos": "podemos", "IU": "fa",
        "Unidas Podemos": UP_FAMILY, "EV": ""}),
    ("aut-CM-2023-05-28", "2023-05-28", "2023 Castilian-Manchegan regional election", {
        "PSOE": "psoe", "PP": "pp", "CS": "", "Vox": "vox", "Unidas Podemos": UP_FAMILY, "EV": ""}),
    ("aut-VC-2023-05-28", "2023-05-28", "2023 Valencian regional election", {
        "PSPV": "psoe", "PP": "pp", "CS": "", "Compromís": "fv", "Vox": "vox", "Unidas Podemos": UP_FAMILY}),
    ("aut-EX-2023-05-28", "2023-05-28", "2023 Extremaduran regional election", {
        "PSOE": "psoe", "PP": "pp", "CS": "", "UxE": UP_FAMILY, "Vox": "vox", "Más País": "fa",
        "JUEx": "", "L": ""}),
    ("aut-MD-2023-05-28", "2023-05-28", "2023 Madrilenian regional election", {
        "PP": "pp", "Más Madrid": "fa", "PSOE": "psoe", "Vox": "vox", "Unidas Podemos": UP_FAMILY, "CS": ""}),
    ("aut-MC-2023-05-28", "2023-05-28", "2023 Murcian regional election", {
        "PSOE": "psoe", "PP": "pp", "CS": "", "Vox": "vox", "Podemos": "podemos", "MCC": "", "xMR": "",
        "IU–V–RM": "fa", "Unidas Podemos": UP_FAMILY, "Más País": "fa"}),
    ("aut-RI-2023-05-28", "2023-05-28", "2023 Riojan regional election", {
        "PSOE": "psoe", "PP": "pp", "CS": "", "Unidas Podemos": UP_FAMILY, "PR+": "", "Vox": "vox", "PLRi": ""}),
    ("aut-NC-2023-05-28", "2023-05-28", "2023 Navarrese regional election", {
        "NA+": "upn", "PSN–PSOE": "psoe", "GBai": "", "EH Bildu": "bildu", "Podemos": "podemos", "I–E (n)": "",
        "Vox": "vox", "UPN": "upn", "PP": "pp", "CS": "", "C/Z": UP_FAMILY, "PN": ""}),
    ("aut-GA-2024-02-18", "2024-02-18", "2024 Galician regional election", {
        "PP": "pp", "BNG": "bng", "PSdeG–PSOE": "psoe", "Galicia en Común": "", "Vox": "vox", "CS": "",
        "Sumar": "fa", "DO": ""}),
    ("aut-PV-2024-04-21", "2024-04-21", "2024 Basque regional election", {
        "PNV": "pnv", "EH Bildu": "bildu", "PSE–EE (PSOE)": "psoe", "Podemos": "podemos", "PP+Cs": "pp",
        "Vox": "vox", "PP": "pp", "CS": "", "Sumar": "fa"}),
    ("aut-CT-2024-05-12", "2024-05-12", "2024 Catalan regional election", {
        "PSC": "psoe", "ERC": "erc", "Junts": "junts", "Vox": "vox", "ECP": "fa", "CUP": "", "Cs": "",
        "PP": "pp", "PDeCAT": "", "Podem": "podemos", "Valents": "", "Aliança.cat": "ac", "Alhora": ""}),
    ("eur-2024-06-09", "2024-06-09", "2024 European Parliament election in Spain", {
        "PSOE": "psoe", "PP": "pp", "Cs": "", "Unidas Podemos": "podemos", "Vox": "vox", "AR": "", "JxCat": "junts",
        "CEUS": "", "Más País": "fa", "PACMA": "", "Sumar": "fa", "SALF": "salf"}),
    ("aut-EX-2025-12-21", "2025-12-21", "2025 Extremaduran regional election", {
        "PSOE": "psoe", "PP": "pp", "Vox": "vox", "UxE": UP_FAMILY, "JUEx": "", "Cs": "", "Podemos": "podemos",
        "Sumar": "fa"}),
    ("aut-AR-2026-02-08", "2026-02-08", "2026 Aragonese regional election", {
        "PP": "pp", "PSOE": "psoe", "Vox": "vox", "CHA": "", "Existe": "", "Podemos": "podemos", "IU": "fa",
        "PAR": "", "Sumar": "fa", "SALF": "salf"}),
    ("aut-CL-2026-03-15", "2026-03-15", "2026 Castilian-Leonese regional election", {
        "PP": "pp", "PSOE": "psoe", "Vox": "vox", "Unidas Podemos": UP_CL26, "Cs": "", "UPL": "", "SY": "",
        "XAV": "", "IUCyL": "fa", "Sumar": "fa", "SALF": "salf"}),
    ("aut-AN-2026-05-17", "2026-05-17", "2026 Andalusian regional election", {
        "PP": "pp", "PSOE–A": "psoe", "Vox": "vox", "PorA": UP_FAMILY, "Adelante Andalucía (2021)": "aa",
        "CS": "", "Podemos Andalusia": "podemos", "Sumar": "fa", "SALF": "salf"}),
]

AGGREGATE_KEYS = {"keydata", "electocracia"}
ALIASES = {"electomania": "emanalytics", "electopanel": "emanalytics", "emanalytics": "emanalytics",
           "hamalgamametrica": "hamalgama", "imopinsights": "imop", "imop": "imop"}


def fetch(title):
    """Cache the article HTML once (UA set, small delay after each Wikipedia request)."""
    path = SRC / (title.replace(" ", "_") + ".html")
    if not path.exists():
        SRC.mkdir(parents=True, exist_ok=True)
        r = requests.get("https://en.wikipedia.org/wiki/" + title.replace(" ", "_"), headers=UA, timeout=120)
        r.raise_for_status()
        path.write_text(r.text, encoding="utf-8")
        time.sleep(2)
    return path


def pollster_key(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]", "", s)
    return ALIASES.get(s, s)


def clean(cell):
    for sup in cell.find_all("sup"):  # reference markers
        sup.decompose()
    return re.sub(r"\s+", " ", cell.get_text(" ", strip=True))


def no_refs(text):
    return re.sub(r"\[.*?\]", "", text).strip()


def label_of(cell):
    t = clean(cell)
    if t:
        return t
    img, a = cell.find("img"), cell.find("a")
    if img and img.get("alt"):
        return img["alt"]
    if a and a.get("title"):
        return a["title"]
    return "?"


def grid(table):
    """Expand colspan/rowspan into a rectangular grid of cell objects (same object = same cell)."""
    out, spans = [], {}
    for tr in table.find_all("tr"):
        own = tr.find_all(["th", "td"], recursive=False)
        row, col, k = [], 0, 0
        while True:
            if col in spans:
                cell, left = spans[col]
                row.append(cell)
                if left == 1:
                    del spans[col]
                else:
                    spans[col] = (cell, left - 1)
                col += 1
            elif k < len(own):
                cell = own[k]
                k += 1
                cs, rs = int(cell.get("colspan", 1)), int(cell.get("rowspan", 1))
                for j in range(cs):
                    row.append(cell)
                    if rs > 1:
                        spans[col + j] = (cell, rs - 1)
                col += cs
            else:
                break
        out.append(row)
    return out


def parse_dates(s):
    """'23 Jan–6 Feb 2026' -> (date, date); '8 Feb 2026' -> (date, date); None if not parseable."""
    s = no_refs(s).replace("—", "–").replace("-", "–")
    parts = [p.strip() for p in s.split("–")]
    end = re.search(r"(\d{1,2})\s*([A-Za-z]{3,})\.?\s*(\d{4})", parts[-1])
    if not end or end.group(2)[:3].lower() not in MONTHS:
        return None
    em, ey, ed = MONTHS[end.group(2)[:3].lower()], int(end.group(3)), int(end.group(1))
    try:
        e = dt.date(ey, em, ed)
        if len(parts) == 1:
            return e, e
        st = re.search(r"(\d{1,2})\s*([A-Za-z]{3,})?\s*(\d{4})?", parts[0])
        sm = MONTHS[st.group(2)[:3].lower()] if st.group(2) and st.group(2)[:3].lower() in MONTHS else em
        sy = int(st.group(3)) if st.group(3) else ey
        return dt.date(sy, sm, int(st.group(1))), e
    except (AttributeError, ValueError):
        return None


def pct_of(cell):
    """Leading number in the cell (later numbers are seat counts). '? 25/26', '–', '<1' or '[ e ]' give None."""
    m = re.match(r"\s*(\d+(?:\.\d+)?)(?![\d/])", no_refs(clean(cell)))
    return m.group(1) if m else None


def sample_of(cell):
    t = no_refs(clean(cell))
    return re.sub(r"\D", "", t) if re.search(r"\d", t) else ""


def firm_of(text):
    """'Sigma Dos/El Mundo' -> ('Sigma Dos', 'El Mundo'); 'CIS (Target Point)' -> ('CIS', 'Target Point')."""
    firm, _, comm = text.partition("/")
    m = re.search(r"\((.*?)\)", firm)
    paren = m.group(1).strip() if m else ""
    firm = re.sub(r"\(.*?\)", "", firm).strip()
    return firm, (comm.strip() or paren)


def parse_election(eid, date, title, labels_map, stats):
    soup = BeautifulSoup(fetch(title).read_text(encoding="utf-8"), "lxml")
    heads = [h for h in soup.find_all(["h2", "h3", "h4", "h5"])
             if "voting intention" in h.get_text(" ", strip=True).lower()]
    g = grid(heads[0].find_next("table"))
    cols = [label_of(c) for c in g[0][4:-1]]  # firm, date, sample, turnout | parties | Lead
    if clean(g[0][-1]) != "Lead":
        raise SystemExit(f"{eid}: unexpected header layout")
    unknown = [c for c in cols if c not in labels_map]
    if unknown:
        raise SystemExit(f"{eid}: unmapped column labels {unknown}")
    d_elec = dt.date.fromisoformat(date)
    polls = []  # each: dict(result, pollster, key, comm, start, end, sample, vals={col: pct str or None})
    prev_firm = None  # firm cell of the previous row when that row produced a poll, else None
    for row in g[2:]:
        if len({id(c) for c in row}) <= 2:  # colspan separator row
            prev_firm = None
            continue
        if len(row) != len(g[0]):
            stats["row width mismatch (dropped)"] += 1
            prev_firm = None
            continue
        firm_cell, date_cell, samp_cell = row[0], row[1], row[2]
        vals = {col: pct_of(c) for col, c in zip(cols, row[4:-1])}
        firm_raw = clean(firm_cell)
        if prev_firm is not None and firm_cell is prev_firm:  # rowspan continuation: fill gaps only
            p = polls[-1]
            for col, v in vals.items():
                if v is None or col in p["dropped"]:
                    continue
                if p["vals"][col] is None:
                    p["vals"][col] = v
                elif p["vals"][col] != v:  # two estimates for one poll: leave the cell out
                    print(f"  conflict in {eid}: {p['pollster']} {p['start']} {col}: {p['vals'][col]} vs {v}")
                    p["vals"][col] = None
                    p["dropped"].add(col)
                    stats["continuation conflicts (cell dropped)"] += 1
            stats["continuation rows merged"] += 1
            continue
        prev_firm = None
        if "election" in firm_raw.lower():  # the election's own result row, or a historical reference row
            if parse_dates(clean(date_cell)) != (d_elec, d_elec):
                stats["historical reference row"] += 1
                continue
            polls.append(dict(result=True, pollster="Election result", key="result", comm="", start=date,
                              end=date, sample="", vals=vals, dropped=set()))
            prev_firm = firm_cell
            continue
        d = parse_dates(clean(date_cell))
        if d is None:
            stats["unparseable fieldwork date (poll dropped)"] += 1
            print(f"  unparsed fieldwork date in {eid}: {firm_raw!r} -> {clean(date_cell)!r}")
            continue
        prev_firm = firm_cell
        firm, comm = firm_of(no_refs(firm_raw))
        polls.append(dict(result=False, pollster=firm, key=pollster_key(firm), comm=comm, start=d[0].isoformat(),
                          end=d[1].isoformat(), sample=sample_of(samp_cell), vals=vals, dropped=set()))
    out = []
    for p in polls:
        is_res = "1" if p["result"] else "0"
        is_agg = "1" if p["key"] in AGGREGATE_KEYS else "0"
        for col in cols:
            if p["vals"][col] is not None:
                out.append([eid, date, p["pollster"], p["key"], p["comm"], p["start"], p["end"], p["sample"],
                            is_res, is_agg, col, labels_map[col], p["vals"][col]])
    n_res = sum(1 for x in out if x[8] == "1")
    if n_res == 0:
        raise SystemExit(f"{eid}: no result row found")
    n_polls = sum(1 for p in polls if not p["result"])
    print(f"{eid}: {n_polls} polls, {len(out) - n_res} poll cells, {n_res} result cells")
    return out


def main():
    stats, rows = Counter(), []
    for eid, date, title, labels_map in ELECTIONS:
        rows += parse_election(eid, date, title, labels_map, stats)
    print("stats:", dict(stats))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)
    print("pollster keys:", dict(sorted(Counter(x[3] for x in rows).items())))
    print(f"wrote {len(rows)} rows -> {OUT}")


if __name__ == "__main__":
    main()
