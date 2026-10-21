"""Senate seat estimates for the 29N 2026 general election -> data/polls_senate.json.

Each SOURCES entry is transcribed by hand from the published article (seat ranges as (low, high) are stored as their
midpoint). The article page is cached under raw/sources/senate/ and downloaded again after 3 days, so re-running is
safe; a failed download only prints a warning. The weighted average is added by pipeline/build/build_polls.py.
Run from the project root:   .venv/Scripts/python pipeline/scrape/polls_senate.py [--fresh]
"""
import json, sys, time
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "raw" / "sources" / "senate"
OUT = ROOT / "data" / "polls_senate.json"
UA = {"User-Agent": "Elecciones29N/0.1 (personal research project)"}
MAX_AGE = 3 * 86400

# Sigma Dos for El Mundo, fieldwork 1-14 Oct 2026, published 19 Oct 2026. Joint PSOE + Frente Amplio list "Senado de
# Progreso" (89-96): PSOE = 92.5 - FA 1. UPN is printed as "1-0.03" by Electomanía; its low bound is used.
SOURCES = [
    {"pollster": "Sigma Dos", "pollster_key": "sigmados", "fieldwork_end": "2026-10-14",
     "url": "https://www.elmundo.es/espana/encuestas/2026/10/19/6ad65700fc6c837b168b4590.html",
     "seats": {"pp": (125, 130), "vox": (15, 22), "psoe": 91, "fa": 1, "bildu": (5, 10), "erc": (4, 7),
               "pnv": (4, 6), "junts": (3, 7), "bng": 1, "cc": 1, "upn": 1},
     "vote_pct": None},
]


def mid(v):
    return v if isinstance(v, (int, float)) else (v[0] + v[1]) / 2


def fetch(entry, fresh):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{entry['pollster_key']}_{entry['fieldwork_end']}.html"
    if fresh or not path.exists() or time.time() - path.stat().st_mtime > MAX_AGE:
        try:
            r = requests.get(entry["url"], headers=UA, timeout=30)
            r.raise_for_status()
            path.write_bytes(r.content)
        except requests.RequestException as e:
            print(f"warn: {entry['url']}: {e}", file=sys.stderr)
    return path


def main():
    fresh = "--fresh" in sys.argv
    polls = []
    for e in SOURCES:
        fetch(e, fresh)
        polls.append({"pollster": e["pollster"], "pollster_key": e["pollster_key"], "fieldwork_end": e["fieldwork_end"],
                      "source_url": e["url"], "seats": {k: mid(v) for k, v in e["seats"].items()},
                      "vote_pct": e["vote_pct"]})
    polls.sort(key=lambda p: p["fieldwork_end"], reverse=True)
    OUT.write_text(json.dumps({"generated": str(date.today()), "polls": polls}, ensure_ascii=False, indent=1), "utf-8")
    print(f"{len(polls)} senate polls -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
