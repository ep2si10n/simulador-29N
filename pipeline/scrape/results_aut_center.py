"""Autonomicas, central group: Comunidad de Madrid, Castilla y León, Canarias.

Sources (cached in raw/sources/aut_center/, downloaded only when missing):
  MD - BOCM proclamation acts of the Junta Electoral Provincial de Madrid (candidacy votes, census, blank, null).
  CL - BOCYL resumen general y por circunscripciones of the Junta Electoral de Castilla y León
       (official candidacy votes incl. CERA, read from word positions with PyMuPDF; mesa CSVs only give names).
  CN - Spanish Wikipedia island-ballot tables for Canarias (island ballots only; the regional/autonomic ballot is ignored).
Run: .venv/Scripts/python pipeline/scrape/results_aut_center.py
"""
import csv, re, time, unicodedata
from pathlib import Path

import pymupdf
import requests
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "raw" / "sources" / "aut_center"
OUT = ROOT / "raw" / "results"
UA = {"User-Agent": "Elecciones29N/0.1 (personal research project)"}
NUM = re.compile(r"\d{1,3}(?:\.\d{3})+|\d+")
HEADER = ["election_id", "date", "type", "region", "province_id", "candidacy", "party_ids", "votes", "pct"]

# Party lineage for every candidacy name written to the CSVs (see task rules). Missing key -> "".
PARTY = {
    # Comunidad de Madrid (BOCM names)
    "PARTIDO SOCIALISTA OBRERO ESPAÑOL": "psoe", "PARTIDO POPULAR": "pp", "VOX": "vox",
    "MÁS MADRID": "fa", "MÁS MADRID-VERDES EQUO": "fa",
    "UNIDAS PODEMOS-IU-MADRID EN PIE": "fa;podemos", "UNIDAS PODEMOS": "fa;podemos",
    "PODEMOS-IZQUIERDA UNIDA-ALIANZA VERDE": "fa;podemos",
    # Castilla y León (BOCYL abbreviations)
    "PSOE": "psoe", "PP": "pp", "VOX": "vox", "PODEMOS-EQUO": "podemos", "SUMAR-PODEMOS-AV": "fa",
    "PODEMOS-IU-AV": "fa;podemos", "IU-ANTICAP-PCAS-TC-ALTER": "fa", "IU-MS-VQ": "fa", "SALF": "salf",
}


def norm(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().upper().strip()


def fetch(url, name):
    path = SRC / name
    if not path.exists():
        r = requests.get(url, headers=UA, timeout=120)
        r.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
        time.sleep(1)
    return path


def int_(s):
    return int(s.replace(".", ""))


def write_results(eid, date, region, rows, totals):
    """rows: (province_id, candidacy, votes) for candidacies; totals: province_id -> (census, voters, valid_votes, blank, null)."""
    OUT.mkdir(parents=True, exist_ok=True)
    valid = {p: t[2] for p, t in totals.items()}
    with open(OUT / f"{eid}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for p, cand, votes in sorted(rows, key=lambda r: (r[0], -r[2])):
            pct = round(100 * votes / valid[p], 4)
            w.writerow([eid, date, "autonomicas", region, p, cand, PARTY.get(cand, ""), votes, f"{pct:.4f}"])
    with open(OUT / f"{eid}_totals.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["province_id", "census", "voters", "valid_votes", "blank_votes", "null_votes"])
        for p in sorted(totals):
            w.writerow([p, *totals[p]])


# ---------------------------------------------------------------- Comunidad de Madrid (BOCM)
MD_BOCM = {  # election_id: (cached file, BOCM url, date)
    "aut-MD-2019-05-26": ("md2019_bocm.pdf", "https://www.bocm.es/boletin/CM_Boletin_BOCM/2019/06/07/BOCM-20190607-28.PDF", "2019-05-26"),
    "aut-MD-2021-05-04": ("md2021_bocm.pdf", "https://www.bocm.es/boletin/CM_Boletin_BOCM/2021/05/20/BOCM-20210520-42.PDF", "2021-05-04"),
    "aut-MD-2023-05-28": ("md2023_bocm.pdf", "https://www.bocm.es/boletin/CM_Boletin_BOCM/2023/06/09/BOCM-20230609-72.PDF", "2023-05-28"),
}


MD_NAME = {"M ÁS MADRID-VERDES EQUO": "MÁS MADRID-VERDES EQUO", "PARTIDO SOCIALISTA OBRER O ESPAÑOL": "PARTIDO SOCIALISTA OBRERO ESPAÑOL",
           "POR UN MUNDO M ÁS JUSTO": "POR UN MUNDO MÁS JUSTO"}


def md_parse(path):
    text = "\n".join(p.extract_text() for p in PdfReader(path).pages)
    head = lambda lab: int_(re.search(lab + r"\s*:?\s*([\d.]+)", text).group(1))
    totals = dict(census=head(r"(?:Número de electores|N\.º DE ELECTORES \(1\))"),
                  voters=head(r"(?:Número de votantes|N\.º DE VOTANTES)"),
                  cand=head(r"(?:Número de votos a candidaturas?|N\.º DE VOTOS A CANDIDATURAS?)"),
                  blank=head(r"(?:Número de votos en blanco|N\.º DE VOTOS EN BLANCO)"),
                  null=head(r"(?:Número de votos nulos|N\.º DE VOTOS NULOS)"))
    seg = text[re.search(r"(?m)^1 [A-ZÁÉÍÓÚÑ]", text).start(): text.index("De acuerdo con")]
    seg = re.split(r"\n\(1\) Los que", seg)[0]  # 2023 footnotes follow the table
    body = "\n".join(l for l in seg.splitlines() if not re.search(r"B\.O\.C\.M|BOLET|BOCM-|Pág\.|JUEVES|VIERNES|MIÉRCOLES|MARTES|LUNES|SÁBADO|DOMINGO", l))
    cands = []
    for block in re.split(r"(?m)^(?=\d{1,2} [A-ZÁÉÍÓÚÑ])", body):
        block = block.strip()
        if not block:
            continue
        rest = block.split(" ", 1)[1]
        m = re.search(r"(\d{1,3}(?:\.\d{3})*)(?:\s+\d{1,2})?\s*$", rest)
        toks = re.split(r"\s+", rest[: m.start()].strip())
        name = []
        for t in toks:  # candidacy name = leading tokens without lowercase letters, stop at letters-in-words
            if re.search(r"[a-záéíóúñ]", t):
                break
            name.append(t)
        cands.append((re.sub(r"\s+", " ", " ".join(name)).strip(), int_(m.group(1))))
    assert sum(v for _, v in cands) == totals["cand"], (sum(v for _, v in cands), totals["cand"])
    return totals, cands


def md_run():
    for eid, (fname, url, date) in MD_BOCM.items():
        path = fetch(url, fname)
        t, cands = md_parse(path)
        valid = t["cand"] + t["blank"]
        assert valid + t["null"] == t["voters"], eid
        rows = [("28", MD_NAME.get(name, name), v) for name, v in cands]
        write_results(eid, date, "MD", rows, {"28": (t["census"], t["voters"], valid, t["blank"], t["null"])})
        print(eid, t, len(cands), "candidacies")
        for name, v in cands:
            print("   ", name, v, PARTY.get(name, ""))


# ---------------------------------------------------------------- Castilla y León (open-data mesas, BOCYL cross-check)
CL_PROV = {"AVILA": "05", "BURGOS": "09", "LEON": "24", "PALENCIA": "34", "SALAMANCA": "37",
           "SEGOVIA": "40", "SORIA": "42", "VALLADOLID": "47", "ZAMORA": "49"}
CL_MESA = {  # election_id: (mesa CSV, date, BOCYL summary PDF, its URL)
    "aut-CL-2019-05-26": ("cl2019_mesas.csv", "2019-05-26", "cl2019_bocyl.pdf",
                          "https://bocyl.jcyl.es/boletines/2019/06/20/pdf/BOCYL-D-20062019-7.pdf"),
    "aut-CL-2022-02-13": ("cl2022_mesas.csv", "2022-02-13", "cl2022_bocyl.pdf",
                          "https://bocyl.jcyl.es/boletines/2022/03/09/pdf/BOCYL-D-09032022-13.pdf"),
    "aut-CL-2026-03-15": ("cl2026_mesas.csv", "2026-03-15", "cl2026_bocyl.pdf",
                          "https://bocyl.jcyl.es/boletines/2026/04/29/pdf/BOCYL-D-29042026-80-13.pdf"),
}
# Raw party strings of the mesa files (accents stripped, upper case) -> candidacy name.
CL_NAME = {norm(k): v for k, v in {
    "P.S.O.E.": "PSOE", "PSOE": "PSOE", "PP": "PP", "VOX": "VOX", "Cs": "Cs",
    "PODEMOS-EQUO": "PODEMOS-EQUO", "PODEMOS - EQUO": "PODEMOS-EQUO", "PODEMOS-IU-AV": "PODEMOS-IU-AV",
    "PODEMOS - AV": "SUMAR-PODEMOS-AV", "IU-MS-VQ": "IU-MS-VQ", "SALF": "SALF", "EV": "ESPAÑA VACIADA",
    "IU-ANTICAPITALISTAS-PCAS/TC-ALTER": "IU-ANTICAP-PCAS-TC-ALTER", "IU - ANTICAPITALISTAS - PCAS/TC - ALTER": "IU-ANTICAP-PCAS-TC-ALTER",
    "IU-Anticapitalistas-PCAS/TC-ALTER": "IU-ANTICAP-PCAS-TC-ALTER", "IU-ANTICAPITALISTAS-PCAS-TC-ALTER": "IU-ANTICAP-PCAS-TC-ALTER",
    "IU-ANTICAPITALISTA CYL-PCAS/TC-ALTER": "IU-ANTICAP-PCAS-TC-ALTER", "IU-ANTICAPITALISTAS-PCAS/TC-ALTER ": "IU-ANTICAP-PCAS-TC-ALTER",
    "U.P.L.": "UPL", "UPL": "UPL", "XAV": "XAV", "ÁvilaLP": "ÁvilaLP", "AHORA DECIDE": "AHORA DECIDE", "ahora Decide": "AHORA DECIDE",
    "TyF": "TyF", "P.C.P.E.": "PCPE", "PCPE": "PCPE", "CI - CCD": "CI-CCD", "CCD": "CCD", "USE BIERZO": "USE BIERZO",
    "P.R.B.": "PRB", "CRA": "C.R.A.", "C. Bierzo": "C.Bierzo", "C.Bierzo - BEX": "C.Bierzo-BEX", "centrados": "CENTRADOS",
    "CONTIGO": "CONTIGO", "P.P.SO.": "P.P.SO.", "PUM+J": "PUM+J", "MUNDO+JUSTO": "PUM+J", "PREPAL": "PREPAL", "PCTE": "PCTE",
    "PACMA": "PACMA", "FE de las JONS": "FE de las JONS", "UNIÓN REGIONALISTA": "UNION REGIONALISTA",
    "PCAS-TC-RC": "PCAS-TC-RC", "PCAS-TC-Recortes Cero": "PCAS-TC-RC", "ESPAÑA VACIADA": "ESPAÑA VACIADA",
    "ZAMORA DECIDE": "ZAMORA DECIDE", "POR ZAMORA": "POR ZAMORA", "VOLT": "VOLT", "TAB": "TAB", "PUEDE": "PUEDE",
    "DESPIERTA": "DESPIERTA", "SY": "SY", "PP.CC.AL": "PP.CC.AL", "EB": "EB", "PYC (ANULADA)": "PYC (ANULADA)",
}.items()}


def cl_name(raw):
    return CL_NAME.get(norm(raw), raw.strip())


def cl_mesas(eid):
    """Polling-station totals and candidacy votes per province from the open-data mesa CSV (CERA rows included when present)."""
    import io
    import pandas as pd
    fname = CL_MESA[eid][0]
    raw = (SRC / fname).read_bytes()
    try:
        txt = raw.decode("utf-8")
    except UnicodeDecodeError:
        txt = raw.decode("latin-1")
    d = pd.read_csv(io.StringIO(txt), sep=";", dtype=str)
    d.columns = [" ".join(c.split()) for c in d.columns]
    col = lambda pref: next(c for c in d.columns if c.startswith(pref))
    mesa, cen, vot, nul = col("Código de mesa"), col("Censo"), col("Total Votantes"), col("Votos Nulos")
    bla, party, votes = col("Votos Blanc"), col("Partido"), col("N")
    for c in (cen, vot, nul, bla):
        d[c] = pd.to_numeric(d[c])
    d[votes] = pd.to_numeric(d[votes])
    d["prov"] = d[mesa].str[:2]
    d["cand"] = d[party].map(cl_name)
    m = d.drop_duplicates(mesa)
    totals = m.groupby("prov")[[cen, vot, nul, bla]].sum()
    cands = d.groupby(["prov", "cand"])[votes].sum()
    return totals, cands, (cen, vot, nul, bla)


def cl_cuadro(eid):
    """BOCYL 'Resumen general' (Cuadro I / Anexo I) per province: electores, candidaturas, blancos, total valid, nulos."""
    from pypdf import PdfReader
    path = fetch(CL_MESA[eid][3], CL_MESA[eid][2])
    lines = PdfReader(str(path)).pages[1].extract_text(extraction_mode="layout").splitlines()
    out = {}
    for l in lines:
        first = l.strip().split(" ")[0] if l.strip() else ""
        code = CL_PROV.get(norm(first))
        if code:
            out[code] = [int_(m.group()) for m in NUM.finditer(l)]
    assert len(out) == 9 and all(len(v) == 5 for v in out.values()), out
    return out


NUMW = re.compile(r"^\d{1,3}(?:\.\d{3})*$")


def cl_columns(eid):
    """Candidacy columns of the BOCYL tables (official, CERA included): list of {prov: votes}.
    Column = numeric token of a TOTAL row (x position); each province number goes to the nearest column."""
    cols = []
    for pg in pymupdf.open(SRC / CL_MESA[eid][2]):
        if "CORRESPONDIENTES" not in pg.get_text().upper():
            continue
        ws = pg.get_text("words")
        cy = lambda w: (w[1] + w[3]) / 2  # text baseline box centre; row labels and numbers differ a few points
        labels = [w for w in ws if w[0] < 150 and (norm(w[4]) == "TOTAL" or norm(w[4]) in CL_PROV)]
        total = next(w for w in labels if norm(w[4]) == "TOTAL")
        nums = [w for w in ws if w[0] >= 150 and NUMW.match(w[4])]
        owner = {}
        for i, n in enumerate(nums):
            lab = min(labels, key=lambda l: abs(cy(l) - cy(n)))
            if abs(cy(lab) - cy(n)) < 9:
                owner[i] = lab
        anchors = sorted([nums[i] for i, l in owner.items() if l is total], key=lambda w: w[0])
        seat = any(w[4].startswith("Esca") for w in ws)  # seat tables: (seats, votes) pairs; seats have no dots
        vote_cols = [k for k, a in enumerate(anchors) if "." in a[4] or not seat]
        page = {k: {} for k in vote_cols}
        for i, lab in owner.items():
            if lab is total:
                continue
            k = min(range(len(anchors)), key=lambda j: abs(anchors[j][0] - nums[i][0]))
            if k in page:
                page[k][CL_PROV[norm(lab[4])]] = int_(nums[i][4])
        for k in vote_cols:
            assert sum(page[k].values()) == int_(anchors[k][4]), (eid, pg.number, k)  # every province read
            cols.append(page[k])
    return cols


def cl_match(cols, eid):
    """Candidacy name per column: mesa candidacy with the closest per-province votes (mesas miss only CERA)."""
    _, cands, _ = cl_mesas(eid)
    mesa = {}
    for (p, name), v in cands.items():
        mesa.setdefault(name, {})[p] = int(v)
    pairs = sorted((sum(abs(c.get(p, 0) - m.get(p, 0)) for p in set(c) | set(m)), i, n)
                   for i, c in enumerate(cols) for n, m in mesa.items())
    names, taken = {}, set()
    for _, i, n in pairs:
        if i not in names and n not in taken:
            names[i] = n
            taken.add(n)
    assert len(names) == len(cols), (eid, "unnamed columns")
    return [names[i] for i in range(len(cols))]


def cl_run(eid):
    """Totals and candidacy votes: BOCYL summary and BOCYL candidacy tables (official). Mesa CSV only names the columns."""
    off = cl_cuadro(eid)
    cols = cl_columns(eid)
    names = cl_match(cols, eid)
    rows, tot = [], {}
    for p in CL_PROV.values():
        census, cand, blank, valid, null = off[p]
        tot[p] = (census, valid + null, valid, blank, null)
        assert sum(c.get(p, 0) for c in cols) == cand, (eid, p)  # candidacy columns add up to official candidaturas
        for c, name in zip(cols, names):
            if p in c and 100 * c[p] / valid >= 0.3:
                rows.append((p, name, c[p]))
    return rows, tot


def main():
    md_run()
    for eid, (_, date, _, _) in CL_MESA.items():
        rows, tot = cl_run(eid)
        write_results(eid, date, "CL", rows, tot)
        print(eid, len(rows), "rows")


if __name__ == "__main__":
    main()
