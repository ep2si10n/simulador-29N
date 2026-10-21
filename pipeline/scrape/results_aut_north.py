"""Autonomicas, northern group: Galicia, Pais Vasco, Navarra, Asturias, Cantabria, La Rioja.

Sources (cached under raw/sources/aut_north/):
  Galicia   - Xunta open data (abertos.xunta.gal), final results by municipality incl. CERA (code 990).
  Pais Vasco - Gobierno Vasco ARE downloads (euskadi.eus), results by territorio historico.
  NC/AS/CB/RI - English Wikipedia overall results table (single-province communities).
Run: .venv/Scripts/python pipeline/scrape/results_aut_north.py
"""
import csv, io, re, time, zipfile
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "raw" / "sources" / "aut_north"
OUT = ROOT / "raw" / "results"
UA = {"User-Agent": "Elecciones29N/0.1 (personal research project)"}

XUNTA = "https://abertos.xunta.gal/catalogo/administracion-publica/-/dataset/"
GA_FILES = {  # election -> list of municipal CSVs (one per province, or one for all)
    "aut-GA-2016-09-25": [f"{XUNTA}0371/eleccions-parlamento-galicia-resultados-2016/001/descarga-directa-ficheiro.csv"],
    "aut-GA-2020-07-12": [f"{XUNTA}0426/eleccions-parlamento-galicia-resultados-2020/{n}/descarga-directa-ficheiro.csv" for n in ("001", "005", "009", "013")],
    "aut-GA-2024-02-18": [f"{XUNTA}0656/eleccions-parlamento-galicia-resultados-2024/{n}/descarga-directa-ficheiro.csv" for n in ("001", "005", "009", "013")],
}
# 2020 province files spell the same list differently; "mg" (Lugo) = Marea Galeguista, the En Marea-CxG-PG list.
GA_RENAME = {"B.N.G.": "BNG", "mg": "EN MAREA-COMPROMISO POR GALICIA-PARTIDO GALEGUISTA"}
EUS = "https://www.euskadi.eus/contenidos/informacion/w_em_descargas/es_def/adjuntos/csv/"
PV_FILES = {  # election -> (zip url, member with results by territorio)
    "aut-PV-2016-09-25": (EUS + "P16_c.zip", "CirP16_c.csv"),
    "aut-PV-2020-07-12": (EUS + "P20_c.zip", "P20_c/CirP20_c.csv"),
    "aut-PV-2024-04-21": (EUS + "Cas-csv.zip", "CirPV24_c.csv"),
}
PV_TH = {"ARABA/ÁLAVA": "01", "GIPUZKOA": "20", "BIZKAIA": "48"}
WIKI = {  # election -> (EN Wikipedia page, province)
    "aut-NC-2019-05-26": ("2019_Navarrese_regional_election", "31"),
    "aut-NC-2023-05-28": ("2023_Navarrese_regional_election", "31"),
    "aut-AS-2019-05-26": ("2019_Asturian_regional_election", "33"),
    "aut-AS-2023-05-28": ("2023_Asturian_regional_election", "33"),
    "aut-CB-2019-05-26": ("2019_Cantabrian_regional_election", "39"),
    "aut-CB-2023-05-28": ("2023_Cantabrian_regional_election", "39"),
    "aut-RI-2019-05-26": ("2019_Riojan_regional_election", "26"),
    "aut-RI-2023-05-28": ("2023_Riojan_regional_election", "26"),
}
# Wikipedia rows are matched by the abbreviation in the last parentheses; anything missing -> "".
WIKI_IDS = {
    "PP": "pp", "PSOE": "psoe", "PSN–PSOE": "psoe", "Vox": "vox",
    "NA+": "upn", "UPN": "upn", "EH Bildu": "bildu",
    "Podemos": "podemos", "Podemos Asturies": "podemos",  # Podemos alone
    "Podemos–IU": "fa;podemos", "Podemos–IU–Equo": "fa;podemos", "Contigo/Zurekin": "fa;podemos",  # UP-family lists
    "IU–IAS": "fa", "I–E (n)": "fa", "IU+Equo (Marea Cántabra)": "fa", "IU–MP–IAS": "fa",  # IU without Podemos
}


def fetch(url, name):
    path = SRC / name
    if not path.exists():
        r = requests.get(url, headers=UA, timeout=60)
        r.raise_for_status()
        path.write_bytes(r.content)
        time.sleep(1)
    return path.read_bytes()


def num(s):
    s = (s or "").strip().replace(".", "")
    return int(s) if s else 0


def ids_ga(eid, name):
    n = name.upper()
    if n.startswith("PP ") or n == "PP": return "pp"
    if "PSDEG" in n: return "psoe"
    if "BNG" in n or "B.N.G." in n: return "bng"
    if "EN MAREA" in n: return "fa;podemos" if eid.endswith("2016-09-25") else ""  # 2020 En Marea = rump party without Podemos/IU
    if "PODEMOS-ESQUERDA UNIDA" in n: return "fa;podemos"  # Galicia en Común 2020
    if "SUMAR" in n: return "fa"
    if "PODEMOS" in n: return "podemos"  # Podemos-Alianza Verde 2024
    if n == "VOX": return "vox"
    return ""


def ids_pv(eid, name):
    n = name.upper()
    if n == "EAJ-PNV": return "pnv"
    if n == "EH BILDU": return "bildu"
    if n.startswith("PSE-EE"): return "psoe"
    if n in ("PP", "PP+CS"): return "pp"
    if n.startswith("PODEMOS"): return "podemos" if eid.startswith("aut-PV-2024") else "fa;podemos"  # Elkarrekin Podemos(-IU) 2016/2020
    if n == "SUMAR": return "fa"
    if n == "VOX": return "vox"
    return ""


def galicia(eid):
    prov, cands = {}, {}  # pid -> totals dict ; (pid, name) -> votes
    for i, url in enumerate(GA_FILES[eid]):
        raw = fetch(url, f"{eid}_{i}.csv")
        text = raw.decode("cp850" if b"V\xa0lidos" in raw else "latin-1")  # some 2020 files are DOS-encoded
        rows = list(csv.reader(io.StringIO(text), delimiter=";"))
        head = [h.strip() for h in rows[0]]
        key = {h.upper().replace(".", ""): j for j, h in enumerate(head)}
        if "CENSO TOTAL" in key:  # 2020/2024 layout
            col = dict(census=key["CENSO TOTAL"], voters=key["VOTOS TOTAIS"], valid_votes=key["VOTOS VÁLIDOS"],
                       blank_votes=key["VOTOS BRANCOS"], null_votes=key["VOTOS NULOS"])
            first = key["VOTOS CANDIDATURAS"] + 1
        else:  # 2016 layout
            col = dict(census=key["CENSO"], voters=key["TOTAL VOTOS"], valid_votes=key["VOTOS VÁLIDOS"],
                       blank_votes=key["VOTOS EN BRANCO"], null_votes=key["VOTOS NULOS"])
            first = key["VOTOS VÁLIDOS"] + 1
        parties = [(j, GA_RENAME.get(head[j], head[j])) for j in range(first, len(head)) if head[j]]
        for r in rows[1:]:
            if not r or not r[0].strip().isdigit():
                continue  # blank lines and the "Total" row
            pid = r[0].strip().zfill(2)
            t = prov.setdefault(pid, dict.fromkeys(col, 0))
            for k, j in col.items():
                t[k] += num(r[j])
            for j, name in parties:
                cands[(pid, name)] = cands.get((pid, name), 0) + num(r[j])
    return prov, cands


def pais_vasco(eid):
    url, member = PV_FILES[eid]
    text = zipfile.ZipFile(io.BytesIO(fetch(url, url.rsplit("/", 1)[1].replace("Cas-csv", "P24_c")))).read(member).decode("latin-1")
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=";")]
    prov, cands = {}, {}
    if eid.startswith("aut-PV-2024"):  # party names on row 0 (4 columns each), labels on row 1
        top, head = rows[0], rows[1]
        parties = [(j, top[j]) for j in range(len(top)) if top[j] and head[j] == "V. Candidaturas"]
    else:
        hi = next(i for i, r in enumerate(rows) if r and r[0].upper() in ("AMBITO", "ÁMBITO"))
        head = rows[hi]
        start = head.index("Abstenciones" if "Abstenciones" in head else "Abstención") + 1
        parties = [(j, head[j]) for j in range(start, len(head)) if head[j]]
    key = {h.strip().upper(): j for j, h in enumerate(head)}
    col = dict(census=key["CENSO"], voters=key.get("VOTANTES", key.get("VOTOS")), valid_votes=key["VÁLIDOS"],
               blank_votes=key["BLANCOS"], null_votes=key["NULOS"])
    for r in rows:
        pid = PV_TH.get(r[0].strip().upper()) if r else None
        if not pid:
            continue
        prov[pid] = {k: num(r[j]) for k, j in col.items()}
        for j, name in parties:
            cands[(pid, name.strip())] = num(r[j])
    return prov, cands


def wikipedia(eid):
    page, pid = WIKI[eid]
    html = fetch(f"https://en.wikipedia.org/w/index.php?title={page}&action=render", f"{page}.html").decode("utf-8")
    table = next(t for t in pd.read_html(io.StringIO(html)) if "Blank ballots" in t.to_string() and "Registered voters" in t.to_string())
    cands, t = {}, {}
    labels = {"Blank ballots": "blank_votes", "Valid votes": "valid_votes", "Invalid votes": "null_votes",
              "Votes cast / turnout": "voters", "Registered voters": "census"}
    for r in table.itertuples(index=False):
        if str(r[0]) in labels:
            t[labels[str(r[0])]] = int(r[2])
        elif str(r[0]) == "nan" and str(r[1]) != "nan" and str(r[2]).isdigit():
            cands[(pid, re.sub(r"\)\d+$", ")", str(r[1])))] = int(r[2])
    return {pid: t}, cands


def wiki_ids(name):
    m = re.search(r"\(([^()]*(?:\([^()]*\))?[^()]*)\)$", name)  # last parenthesised group, one nesting level
    return WIKI_IDS.get(m.group(1) if m else "", "")


def build(eid):
    region, date = eid.split("-")[1], eid[-10:]
    if region == "GA": (prov, cands), ids = galicia(eid), lambda n: ids_ga(eid, n)
    elif region == "PV": (prov, cands), ids = pais_vasco(eid), lambda n: ids_pv(eid, n)
    else: (prov, cands), ids = wikipedia(eid), wiki_ids
    rows = []
    for (pid, name), v in cands.items():
        if v > 0:
            rows.append(dict(election_id=eid, date=date, type="autonomicas", region=region, province_id=pid,
                             candidacy=name, party_ids=ids(name), votes=v, pct=f"{100 * v / prov[pid]['valid_votes']:.3f}"))
    rows.sort(key=lambda r: (r["province_id"], -r["votes"]))
    # consistency: candidacy votes + blank == valid, valid + null == voters
    for pid, t in prov.items():
        cand = sum(r["votes"] for r in rows if r["province_id"] == pid)
        assert cand + t["blank_votes"] == t["valid_votes"], (eid, pid, cand, t)
        assert t["valid_votes"] + t["null_votes"] == t["voters"], (eid, pid, t)
    pd.DataFrame(rows).to_csv(OUT / f"{eid}.csv", index=False)
    tot = pd.DataFrame([{"province_id": p, **prov[p]} for p in sorted(prov)])
    tot[["province_id", "census", "voters", "valid_votes", "blank_votes", "null_votes"]].to_csv(OUT / f"{eid}_totals.csv", index=False)
    return rows, prov


if __name__ == "__main__":
    SRC.mkdir(parents=True, exist_ok=True)
    expected = {"GA": {"15", "27", "32", "36"}, "PV": {"01", "20", "48"}}
    for eid in [*GA_FILES, *PV_FILES, *WIKI]:
        rows, prov = build(eid)
        region = eid.split("-")[1]
        assert set(prov) == expected.get(region, {WIKI.get(eid, (0, 0))[1]}), (eid, sorted(prov))
        print(eid, len(rows), "rows,", "provinces", sorted(prov))
