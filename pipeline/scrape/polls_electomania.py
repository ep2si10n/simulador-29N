"""Adds to raw/polls/national_polls.csv the national polls Electomanía already lists and Wikipedia does not yet.

Source: Electomanía's poll registry API (api.em-data.es via its WordPress proxy): vote % and seat projections.
Its dates are publication dates, so a poll is new only when no Wikipedia poll of the same house ended its fieldwork in
the 10 days before; new polls get fieldwork_end = publication - 1 day and no sample size.
Run after polls_national.py (which rewrites the CSV):  .venv/Scripts/python pipeline/scrape/polls_electomania.py
"""
import csv, hashlib, sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from polls_national import HEADER, OUT, pollster_key  # noqa: E402

API = "https://electomania.es/wp-json/emchart/v1/fetch?url=https%3A%2F%2Fapi.em-data.es%2Fapi%2Fesp%2Fencuestas%2F"
UA = {"User-Agent": "Mozilla/5.0 Elecciones29N (personal research project)"}
SINCE = date(2023, 7, 24)
LAG = 10  # max days between fieldwork end and publication
# API column -> lineage id ("UP" only counts as Podemos when the poll has no Podemos column)
PARTY_IDS = {"PP": "pp", "PSOE": "psoe", "Vox": "vox", "Sumar": "fa", "Mas_Pais": "fa", "Podemos": "podemos",
             "Compromis": "fv", "ERC": "erc", "Junts": "junts", "EH_Bildu": "bildu", "PNV": "pnv", "BNG": "bng",
             "CC": "cc", "UPN": "upn", "AA": "aa", "AC": "ac", "SALF": "salf"}


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fetch(kind):
    rows = requests.get(API + kind, headers=UA, timeout=60).json()["results"]
    return [r for r in rows if r.get("fecha")]


def main():
    with OUT.open(encoding="utf-8") as f:
        existing = list(csv.DictReader(f))
    ends = {}
    for r in existing:
        if r["cycle"] == "next" and r["is_result"] == "0":
            ends.setdefault(r["pollster_key"], set()).add(date.fromisoformat(r["fieldwork_end"]))
    seats = {(r["encuestadora"], r["fecha"]): r for r in fetch("diputados")}
    added = []
    for p in fetch("voto"):
        pub = datetime.strptime(p["fecha"], "%d/%m/%Y").date()
        key = pollster_key(p["encuestadora"])
        if pub < SINCE or any(pub - timedelta(days=LAG) <= e <= pub for e in ends.get(key, ())):
            continue
        end = (pub - timedelta(days=1)).isoformat()
        cols = dict(PARTY_IDS, **({} if num(p.get("Podemos")) is not None else {"UP": "podemos"}))
        s = seats.get((p["encuestadora"], p["fecha"]), {})
        pid = "next-em" + hashlib.sha1(f"{key}|{p['fecha']}".encode()).hexdigest()[:8]
        rows = [[pid, "next", p["encuestadora"], key, p.get("medio") or "", end, end, "", 0, 0, col, ids, num(p[col]),
                 "" if num(s.get(col)) is None else num(s.get(col))]
                for col, ids in cols.items() if num(p.get(col)) is not None]
        if rows:
            added += rows
            ends.setdefault(key, set()).add(date.fromisoformat(end))
            print(f"new: {p['fecha']} {p['encuestadora']} ({p.get('medio')})")
    with OUT.open("a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(added)
    print(f"added {len({r[0] for r in added})} polls from Electomanía -> {OUT}")
    assert HEADER[-1] == "seats" and all(len(r) == len(HEADER) for r in added)


if __name__ == "__main__":
    main()
