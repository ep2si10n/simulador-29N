"""Polls of the 2018-2022 regional and European elections -> raw/polls/history_polls.csv (same schema as
election_polls.csv), to measure poll accuracy over 8 years. Same Wikipedia parser as polls_elections.py, but the column
map is tolerant: a label it does not know keeps the row with no lineage id (accuracy is measured column vs the result
row of the same table, so lineage is only needed for party-level bias).
Run from the project root:  .venv/Scripts/python pipeline/scrape/polls_history.py
"""
import csv, sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from polls_elections import HEADER, parse_election  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "raw" / "polls" / "history_polls.csv"

LINEAGE = {"PP": "pp", "Vox": "vox", "PSOE": "psoe", "PSC": "psoe", "PSPV": "psoe", "PSIB–PSOE": "psoe", "PSdeG": "psoe",
           "PSE–EE": "psoe", "PSE-EE": "psoe", "PSN": "psoe", "Podemos": "podemos", "Unidas Podemos": "fa;podemos",
           "UP": "fa;podemos", "Más Madrid": "fa", "Más País": "fa", "IU": "fa", "Compromís": "fv", "ERC": "erc",
           "JxCat": "junts", "Junts": "junts", "PDeCAT": "junts", "PNV": "pnv", "EAJ-PNV": "pnv", "EH Bildu": "bildu",
           "BNG": "bng", "CCa": "cc", "CC": "cc", "NA+": "upn", "UPN": "upn", "AA": "aa", "Adelante": "aa"}


class Tolerant(dict):
    """Every label is 'known'; unknown ones map to '' (no lineage)."""
    def __contains__(self, k):
        return True

    def __getitem__(self, k):
        return LINEAGE.get(k, "")


ELECTIONS = [
    ("aut-AN-2018-12-02", "2018-12-02", "2018 Andalusian regional election"),
    ("aut-VC-2019-04-28", "2019-04-28", "2019 Valencian regional election"),
    ("eur-2019-05-26", "2019-05-26", "2019 European Parliament election in Spain"),
    *[(f"aut-{c}-2019-05-26", "2019-05-26", f"2019 {n} regional election") for c, n in [
        ("AR", "Aragonese"), ("AS", "Asturian"), ("CB", "Cantabrian"), ("CL", "Castilian-Leonese"),
        ("CM", "Castilian-Manchegan"), ("CN", "Canarian"), ("EX", "Extremaduran"), ("IB", "Balearic"),
        ("MC", "Murcian"), ("MD", "Madrilenian"), ("NC", "Navarrese"), ("RI", "Riojan")]],
    ("aut-GA-2020-07-12", "2020-07-12", "2020 Galician regional election"),
    ("aut-PV-2020-07-12", "2020-07-12", "2020 Basque regional election"),
    ("aut-CT-2021-02-14", "2021-02-14", "2021 Catalan regional election"),
    ("aut-MD-2021-05-04", "2021-05-04", "2021 Madrilenian regional election"),
    ("aut-CL-2022-02-13", "2022-02-13", "2022 Castilian-Leonese regional election"),
    ("aut-AN-2022-06-19", "2022-06-19", "2022 Andalusian regional election"),
]


def main():
    stats, rows, failed = Counter(), [], []
    for eid, date, title in ELECTIONS:
        try:
            rows += parse_election(eid, date, title, Tolerant(), stats)
        except (SystemExit, Exception) as e:  # one odd article must not sink the rest
            failed.append((eid, str(e)[:120]))
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)
    print("stats:", dict(stats))
    print(f"wrote {len(rows)} rows -> {OUT}; failed: {failed}")


if __name__ == "__main__":
    main()
