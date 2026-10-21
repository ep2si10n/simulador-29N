"""Nationwide voting-intention polls since mid-2016 -> raw/polls/national_polls.csv.

Source: English Wikipedia "Voting intention estimates" tables (one row per poll x party).
Run from the project root:   .venv/Scripts/python pipeline/scrape/polls_national.py [--fresh]
Pages older than 3 days are downloaded again (all of them with --fresh); the CSV is rewritten
in full, so re-running is safe.
"""
import csv, hashlib, re, sys, time, unicodedata
from collections import Counter
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "raw" / "sources" / "polls_national"
OUT = ROOT / "raw" / "polls" / "national_polls.csv"
UA = {"User-Agent": "Elecciones29N/0.1 (personal research project)"}
MAX_AGE = 3 * 86400
HEADER = ["poll_id", "cycle", "pollster", "pollster_key", "commissioner", "fieldwork_start", "fieldwork_end",
          "sample_size", "is_result", "is_aggregate", "party_raw", "party_ids", "pct", "seats"]

# cycle, English Wikipedia title, cache file, year for headings that carry none, election day
PAGES = [
    ("2016", "Opinion polling for the 2016 Spanish general election", "2016.html", 2016, "2016-06-26"),
    ("2019A", "Opinion polling for the April 2019 Spanish general election", "2019A.html", 2019, "2019-04-28"),
    ("2019N", "Opinion polling for the November 2019 Spanish general election", "2019N.html", 2019, "2019-11-10"),
    ("2023", "Opinion polling for the 2023 Spanish general election", "2023.html", 2023, "2023-07-23"),
    ("2023", "Nationwide opinion polling for the 2023 Spanish general election (2019–2021)", "2023_2019-2021.html", 2019, "2023-07-23"),
    ("2023", "Nationwide opinion polling for the 2023 Spanish general election (2022)", "2023_2022.html", 2022, "2023-07-23"),
    ("next", "Opinion polling for the next Spanish general election", "next.html", 2026, "2026-11-29"),
]
ELECTION = {c: date.fromisoformat(d) for c, _, _, _, d in PAGES}
# poll fieldwork_end window per cycle (after the previous election, before this one);
# 2016 is only needed for its result row, so it has no poll window
WINDOW = {"2016": None,
          "2019A": ("2016-06-27", "2019-04-27"), "2019N": ("2019-04-29", "2019-11-09"),
          "2023": ("2019-11-11", "2023-07-22"), "next": ("2023-07-24", "2026-11-28")}

# party column label (Wikipedia logo title) -> lineage ids; "" = no lineage id
PARTY_IDS = {
    "PP": "pp", "PSOE": "psoe", "Vox": "vox",
    "Sumar": "fa", "FA": "fa", "Más País": "fa",
    "Podemos": "podemos", "Unidas Podemos": "fa;podemos", "Unidos Podemos": "fa;podemos",
    "Valencian Front": "fv", "Compromís": "fv",
    "ERC": "erc", "ERC–CatSí": "erc", "ERC–Sobiranistes": "erc",
    "Junts": "junts", "JxCat": "junts", "PDeCAT": "junts", "DiL": "junts",
    "EH Bildu": "bildu", "PNV": "pnv", "BNG": "bng",
    "CC": "cc", "CCa": "cc", "CC–NCa": "cc",
    "UPN": "upn", "NA+": "upn",
    "Adelante Andalucía (2021)": "aa", "Aliança.cat": "ac", "Se Acabó La Fiesta": "salf",
    "Cs": "", "C's": "", "CS": "", "CUP": "", "PACMA": "", "Teruel Existe": "", "EV": "",
    "Regionalist Party of Cantabria": "", "IU–UPeC": "",
}
NOT_PARTY = {"Polling firm/Commissioner", "Fieldwork date", "Sample size", "Turnout", "Lead"}
# pollster key aliases, applied to the folded name (lowercase a-z0-9 only)
ALIAS = {"electomania": "emanalytics", "electopanel": "emanalytics", "hamalgamametrica": "hamalgama",
         "imopinsights": "imop", "demoscopiaservicios": "demoscopiayservicios"}
AGGREGATES = {"keydata", "electocracia"}
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
RANGE = re.compile(r"^(\d{1,2})(?:\s+([a-z]+))?(?:\s+(\d{4}))?\s*[-–—]\s*(\d{1,2})\s+([a-z]+)\.?(?:\s+(\d{4}))?$", re.I)
SINGLE = re.compile(r"^(\d{1,2})\s+([a-z]+)\.?(?:\s+(\d{4}))?$", re.I)
SKIP = Counter()


def clean(cell, drop=("sup",)):
    for x in cell.select(", ".join(drop)):
        x.decompose()
    s = cell.get_text(" ", strip=True).replace("\xa0", " ")
    return re.sub(r"\s+", " ", re.sub(r"\[[^\]]*\]", "", s)).strip()


def label(th):
    a, img = th.find("a"), th.find("img")
    if a and a.get("title"):
        return a["title"].strip()
    if img and img.get("alt"):
        return img["alt"].strip()
    return clean(th)


def parse_dates(text, year):
    """'15–17 Oct' / '28 Dec–3 Jan' / '23 Apr–2 May 2016' / '28 Apr' -> (start, end), None if unparsable."""
    m = RANGE.match(text)
    if m:
        d1, mo1, y1, d2, mo2, y2 = m.groups()
    else:
        m = SINGLE.match(text)
        if not m:
            return None
        d1, mo1, y1 = m.group(1), m.group(2), m.group(3)
        d2, mo2, y2 = d1, mo1, y1
    if (mo2 or "")[:3].lower() not in MONTHS or (mo1 and (mo1 or "")[:3].lower() not in MONTHS):
        return None
    m2 = MONTHS[mo2[:3].lower()]
    m1 = MONTHS[(mo1 or mo2)[:3].lower()]
    ye = int(y2) if y2 else year
    ys = int(y1) if y1 else (ye if m1 <= m2 else ye - 1)
    return date(ys, m1, int(d1)), date(ye, m2, int(d2))


def parse_pct(cell):
    """First number of the cell as written ('27.1', seat counts and small print dropped); None if not a number."""
    m = re.match(r"\s*(\d+(?:\.\d+)?)", clean(cell, ("sup", "small", "span[style*=font-size]")))
    return m.group(1) if m else None


def parse_seats(cell):
    """Published seat projection in the small print ('111' or a range '101/106' -> midpoint); None if absent."""
    small = cell.select_one("span[style*=font-size], small")
    nums = [int(x) for x in re.findall(r"\d+", small.get_text()) if int(x) <= 350] if small else []
    return sum(nums[:2]) / len(nums[:2]) if nums else None


def parse_int(cell):
    m = re.match(r"\s*(\d[\d,]*)", clean(cell))
    return int(m.group(1).replace(",", "")) if m else None


def pollster_key(firm):
    k = unicodedata.normalize("NFKD", firm).encode("ascii", "ignore").decode().lower()
    k = re.sub(r"[^a-z0-9]", "", k)
    return ALIAS.get(k, k)


def page_html(title, cache, fresh):
    path = SRC / cache
    if not fresh and path.exists() and time.time() - path.stat().st_mtime < MAX_AGE:
        return path.read_text(encoding="utf-8")
    r = requests.get("https://en.wikipedia.org/wiki/" + title.replace(" ", "_"), headers=UA, timeout=60)
    r.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(r.text, encoding="utf-8")
    time.sleep(2)
    return r.text


def grid_rows(rows):
    """Yields (own_cells, grid) per <tr>; grid[col] = (cell, starts_in_this_row), rowspans/colspans expanded."""
    pending = {}  # col -> [cell, rows still covered after this one]
    for tr in rows:
        cells = tr.find_all(["td", "th"], recursive=False)
        grid = {col: (cell, False) for col, (cell, _) in pending.items()}
        nxt, col = {}, 0
        for cell in cells:
            while col in grid:
                col += 1
            span, rs = int(cell.get("colspan", 1)), int(cell.get("rowspan", 1))
            for k in range(span):
                grid[col + k] = (cell, k == 0)
                if rs > 1:
                    nxt[col + k] = [cell, rs - 1]
            col += span
        pending = {c: [cell, left - 1] for c, (cell, left) in pending.items() if left > 1}
        pending.update(nxt)
        yield cells, grid


def parse_page(html, cycle, year0, election):
    """Yields poll dicts: firm, commissioner, start, end, sample, result, parties [(label, pct)]."""
    soup = BeautifulSoup(html, "lxml")
    body = max(soup.select("div.mw-parser-output"), key=lambda d: len(d.find_all("table")))
    year, scope = year0, False
    for el in body.find_all(["h2", "h3", "h4", "h5", "table"]):
        text = el.get_text(" ", strip=True)
        years = re.findall(r"\b\d{4}\b", text)
        if el.name == "h2":
            scope = text == "Polling"
        elif el.name == "h3" and not years:
            scope = False
        elif el.name == "h4":
            scope = text == "Voting intention estimates"
        if el.name in ("h3", "h5") and years:
            year = int(years[0])
        if el.name != "table" or not scope or "wikitable" not in (el.get("class") or []):
            continue
        rows = el.find_all("tr")
        labels = [label(c) for c in rows[0].find_all(["th", "td"])]
        unknown = [l for l in labels if l not in PARTY_IDS and l not in NOT_PARTY]
        if unknown:
            sys.exit(f"unknown party column in {cycle}: {unknown}")
        firm_i, date_i, sample_i = labels.index("Polling firm/Commissioner"), labels.index("Fieldwork date"), labels.index("Sample size")
        cols = [(i, l) for i, l in enumerate(labels) if l in PARTY_IDS]
        for cells, grid in grid_rows(rows):
            if len(cells) <= 1 or any(c.name == "th" for c in cells):
                SKIP[f"{cycle}: header/year row"] += 1
                continue
            if not grid[firm_i][1]:
                # firm cell spanned from the row above: a turnout variant of that poll, keep the first only
                SKIP[f"{cycle}: variant row (same poll as row above)"] += 1
                continue
            firm_raw = clean(grid[firm_i][0])
            is_result = bool(re.search(r"general election", firm_raw, re.I))
            if not is_result and re.search(r"\belection\b", firm_raw, re.I):
                SKIP[f"{cycle}: other election reference {firm_raw!r}"] += 1
                continue
            dates = parse_dates(clean(grid[date_i][0]).lower(), year)
            if dates is None:
                SKIP[f"{cycle}: unparsable date {clean(grid[date_i][0])!r}"] += 1
                continue
            start, end = dates
            if is_result:
                if end != election:
                    SKIP[f"{cycle}: result row of another election ({end})"] += 1
                    continue
                firm, comm = "Election", ""
            else:
                firm, _, comm = firm_raw.partition("/")
                firm, comm = firm.strip(), comm.strip()
            parties = []
            for i, l in cols:
                cell, own = grid.get(i, (None, False))
                if cell is None:
                    continue
                if not own:
                    SKIP[f"{cycle}: party cell spanned from above"] += 1
                    continue
                seats = parse_seats(cell)  # before parse_pct, which strips the small print
                v = parse_pct(cell)
                if v is not None:
                    parties.append((l, v, seats))
            yield {"firm": firm, "commissioner": comm, "start": start, "end": end,
                   "sample": parse_int(grid[sample_i][0]), "result": is_result, "parties": parties}


def main():
    fresh = "--fresh" in sys.argv
    polls = []
    for cycle, title, cache, year0, election in PAGES:
        html = page_html(title, cache, fresh)
        for p in parse_page(html, cycle, year0, ELECTION[cycle]):
            if not p["result"]:
                w = WINDOW[cycle]
                if w is None or not (date.fromisoformat(w[0]) <= p["end"] <= date.fromisoformat(w[1])):
                    SKIP[f"{cycle}: poll outside window"] += 1
                    continue
            polls.append((cycle, p))
    seen, rows = Counter(), []
    for cycle, p in polls:
        pk = pollster_key(p["firm"])
        key = "|".join([cycle, pk, p["commissioner"], p["start"].isoformat(), p["end"].isoformat(), str(p["sample"] or "")])
        seen[key] += 1
        pid = f"{cycle}-" + hashlib.sha1(f"{key}#{seen[key]}".encode()).hexdigest()[:10]
        for lab, pct, seats in p["parties"]:
            # the 2023 result row is a candidacy row: Sumar then also counts Podemos (generales 2023 rule)
            ids = "fa;podemos" if p["result"] and lab == "Sumar" else PARTY_IDS[lab]
            rows.append([pid, cycle, p["firm"], pk, p["commissioner"], p["start"].isoformat(), p["end"].isoformat(),
                         "" if p["sample"] is None else p["sample"], int(p["result"]), int(pk in AGGREGATES),
                         lab, ids, pct, "" if seats is None else seats])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)
    print(f"wrote {len(rows)} rows, {len(polls)} polls -> {OUT}")
    for k, v in sorted(SKIP.items()):
        print(f"skipped {v:4d}  {k}")


if __name__ == "__main__":
    main()
