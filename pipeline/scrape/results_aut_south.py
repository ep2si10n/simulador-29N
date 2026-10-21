"""Autonomicas, southern group: Andalucia, Extremadura, Castilla-La Mancha, Region de Murcia.

Sources (cached under raw/sources/aut_south/):
  Official results as published in the regional gazettes (BOJA, DOE, DOCM, BORM) by the regional /
  provincial electoral boards, downloaded from the Junta Electoral Central document library.
  MC 2019: the JEC only holds the list of elected members, so votes come from English Wikipedia's
  results table (Murcia is a single constituency since 2015).
Run: .venv/Scripts/python pipeline/scrape/results_aut_south.py
"""
import io, itertools, re, time
from pathlib import Path

import pandas as pd
import requests
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "raw" / "sources" / "aut_south"
OUT = ROOT / "raw" / "results"
UA = {"User-Agent": "Elecciones29N/0.1 (personal research project)"}
JEC = "https://www.juntaelectoralcentral.es/cs/jec/documentos/"

PDFS = {  # election -> (JEC document, local cache name)
    "aut-AN-2018-12-02": ("ANDALUCIA_2018_Resultados.pdf", "AN_2018_jec.pdf"),
    "aut-AN-2022-06-19": ("Resultados_2022.pdf", "AN_2022_jec.pdf"),
    "aut-AN-2026-05-17": ("Andalucia_2026_resultados.pdf", "AN_2026_jec.pdf"),
    "aut-EX-2019-05-26": ("EXTREMADURA_2019_Resultado.pdf", "EX_2019_jec.pdf"),
    "aut-EX-2023-05-28": ("resultados-escrutinio-locales-extremadura.pdf", "EX_2023_jec.pdf"),  # DOE despite the name
    "aut-EX-2025-12-21": ("extremadura_2025_resultados.pdf", "EX_2025_jec.pdf"),
    "aut-CM-2019-05-26": ("CASTILLA%20LA%20MANCHA_2019_Resultado.pdf", "CM_2019_jec.pdf"),
    "aut-CM-2023-05-28": ("Resultados%20definitivos%20Castilla%20La%20Mancha.pdf", "CM_2023_jec.pdf"),
    "aut-MC-2023-05-28": ("Resultados%20definitivos.pdf", "MC_2023_jec.pdf"),
}
MC_WIKI = {"aut-MC-2019-05-26": "2019_Murcian_regional_election"}

# BOJA tables: pages with one row per province; column names typed from the table headers.
# First listed page = seats+votes table, the rest = votes only. Summary (Cuadro I) page given separately.
AN = {
    "aut-AN-2018-12-02": (0, {
        1: ["PARTIDO SOCIALISTA OBRERO ESPAÑOL", "PARTIDO POPULAR", "CIUDADANOS-PARTIDO DE LA CIUDADANÍA",
            "ADELANTE ANDALUCÍA", "VOX"],
        2: ["PARTIDO ANIMALISTA CONTRA EL MALTRATO ANIMAL (PACMA)", "ANDALUCÍA POR SÍ (AxSÍ)", "EQUO-INICIATIVA VERDE",
            "RECORTES CERO-POR UN MUNDO MÁS JUSTO-GRUPO VERDE", "PARTIDO COMUNISTA DEL PUEBLO ANDALUZ (PCPA)",
            "UNIÓN, PROGRESO Y DEMOCRACIA (UPYD)", "NACIÓN ANDALUZA (NA)"],
        3: ["INDEPENDIENTES HUELVA (IxH)", "CIUDADANOS LIBRES UNIDOS (CILUS)", "PARTIDO COMUNISTA OBRERO ESPAÑOL (P.C.O.E.)",
            "ESCAÑOS EN BLANCO (EB)", "FALANGE ESPAÑOLA DE LAS JONS (FE de las JONS)", "ALTERNATIVA REPUBLICANA (ALTER)",
            "CONVERGENCIA ANDALUZA (CAnda)"],
        4: ["RESPETO", "CIUDADANOS INDEPENDIENTES DE LINARES UNIDOS (CILU-LINARES)",
            "IZQUIERDA ANTICAPITALISTA REVOLUCIONARIA (IZAR)", "SOLUCIONA", "CONECTA ANDALUCÍA (ConecAnd)",
            "UNIDOS Y SOCIALISTAS+ POR LA DEMOCRACIA (UNIDOS SÍ-Democracia Efectiva)",
            "PARTIDO REPUBLICANO INDEPENDIENTE SOLIDARIO DE ANDALUCÍA (RISA)"],
    }),
    "aut-AN-2022-06-19": (0, {
        1: ["PARTIDO POPULAR", "PARTIDO SOCIALISTA OBRERO ESPAÑOL DE ANDALUCÍA", "VOX",
            "IZQUIERDA UNIDA ANDALUCÍA-MÁS PAIS ANDALUCÍA-VERDES EQUO-INICIATIVA DEL PUEBLO ANDALUZ: POR ANDALUCÍA",
            "ADELANTE ANDALUCÍA-ANDALUCISTAS"],
        2: ["CIUDADANOS-PARTIDO DE LA CIUDADANÍA", "PARTIDO ANIMALISTA CONTRA EL MALTRATO ANIMAL", "JAÉN MERECE MÁS",
            "ANDALUCES LEVANTAOS", "COALICIÓN ESCAÑOS EN BLANCO", "PARTIDO COMUNISTA DEL PUEBLO ANDALUZ",
            "POR UN MUNDO MÁS JUSTO"],
        3: ["POR HUELVA", "NACIÓN ANDALUZA", "PARTIDO COMUNISTA DE LOS TRABAJADORES DE ESPAÑA", "RECORTES CERO",
            "COALICIÓN REPUBLICANA SOCIALISTA POR ANDALUCÍA", "PARTIDO AUTÓNOMOS", "FALANGE ESPAÑOLA DE LAS J.O.N.S."],
        4: ["LOS VERDES", "JUNTOS POR GRANADA-EL PARTIDO DEL GRANADEXIT", "VOLT ESPAÑA",
            "PARTIDO DE JUBILADOS POR EL FUTURO, DIGNIDAD Y DEMOCRACIA",
            "SOMOS FUERZA, UNIÓN, TRABAJO, UTILIDAD, REGENERACIÓN Y OPORTUNIDAD",
            "DESPIERTA. PARTIDO SOCIO-POLÍTICO CONSTITUCIONALISTA", "IZQUIERDA ANTICAPITALISTA REVOLUCIONARIA", "BASTA YA!"],
    }),
    "aut-AN-2026-05-17": (1, {
        2: ["PARTIDO POPULAR", "PARTIDO SOCIALISTA OBRERO ESPAÑOL DE ANDALUCÍA", "VOX", "ADELANTE ANDALUCIA",
            "POR ANDALUCIA-IZQUIERDA UNIDA-PODEMOS-MOVIMIENTO SUMAR-INICIATIVA DEL PUEBLO ANDALUZ-VERDES EQUO-"
            "ALTERNATIVA REPUBLICANA-ALIANZA VERDE"],
        3: ["SE ACABÓ LA FIESTA", "PARTIDO ANIMALISTA CON EL MEDIO AMBIENTE", "100X100 UNIDOS",
            "ANDALUCISTAS PUEBLO ANDALUZ", "ESCAÑOS EN BLANCO", "JAÉN MERECE MÁS", "PARTIDO COMUNISTA DEL PUEBLO ANDALUZ"],
        4: ["FALANGE ESPAÑOLA DE LAS JONS", "POR UN MUNDO MÁS JUSTO", "PARTIDO AUTÓNOMOS", "NACIÓN ANDALUZA",
            "HUELVA EXISTE", "PARTIDO COMUNISTA DE LOS TRABAJADORES DE ESPAÑA", "PODER ANDALUZ"],
        5: ["ALTERNATIVA MALAGUEÑA", "ALMERIENSES REGIONALISTAS PRO ALMERÍA", "PARTIDO ANDALUSÍ",
            "IZQUIERDA ANTICAPITALISTA REVOLUCIONARIA", "PARTIDO DE JUBILADOS POR EL FUTURO, DIGNIDAD Y DEMOCRACIA",
            "IZQUIERDA POR ALMERIA", "CONECTA ANDALUCÍA", "SOCIEDAD UNIDA"],
    }),
}
AN_PROV = {"ALMERÍA": "04", "CÁDIZ": "11", "CÓRDOBA": "14", "GRANADA": "18", "HUELVA": "21", "JAÉN": "23",
           "MÁLAGA": "29", "SEVILLA": "41", "TOTAL": "total"}
CM_PROV = {"Albacete": "02", "Ciudad Real": "13", "Cuenca": "16", "Guadalajara": "19", "Toledo": "45"}
NUM = r"\d{1,3}(?:\.\d{3})*"
TOTAL_KEYS = ["census", "voters", "valid_votes", "blank_votes", "null_votes"]


def fetch(url, name):
    path = SRC / name
    if not path.exists():
        r = requests.get(url, headers=UA, timeout=90)
        r.raise_for_status()
        path.write_bytes(r.content)
        time.sleep(1)
    return path


def pages(eid):
    doc, name = PDFS[eid]
    return [p.extract_text() for p in PdfReader(fetch(JEC + doc, name)).pages]


def num(s):
    return int(s.replace(".", "")) if s else 0


def prov_rows(text):
    """BOJA province rows -> {pid: [cell strings]}; '' = empty cell ('---' or a bare space in 2018)."""
    out = {}
    for line in text.splitlines():
        name, _, rest = line.lstrip().partition(" ")
        if name.upper() in AN_PROV and re.fullmatch(r"[\d. -]*", rest):
            cells = rest.split() if "-" in rest else rest.split(" ")  # 2018 marks an empty cell by an extra space
            out[AN_PROV[name.upper()]] = ["" if set(c) <= {"-"} else c for c in cells]
    return out


def andalucia(eid):
    summary, tables = AN[eid]
    txt = pages(eid)
    prov, cands = {}, {}
    for pid, c in prov_rows(txt[summary]).items():  # electores, votantes, validos, a candidaturas, blanco, nulos
        if pid != "total":
            prov[pid] = dict(zip(["census", "voters", "valid_votes", "cand", "blank_votes", "null_votes"], map(num, filter(None, c))))
    for i, (page, names) in enumerate(tables.items()):
        rows = prov_rows(txt[page])
        for pid, cells in rows.items():
            if i == 0:  # seats/votes pairs, 2022+ prefixed by the constituency's seat count
                cells = list(filter(None, cells))
                cells = cells[len(cells) % 2:][1::2]
            cells = (cells + [""] * len(names))[:len(names)] if all(c == "" for c in cells[len(names):]) else None
            assert cells, (eid, page, pid, rows[pid])
            rows[pid] = cells
        for j, name in enumerate(names):
            assert sum(num(rows[p][j]) for p in prov) == num(rows["total"][j]), (eid, page, name)
            for p in prov:
                if rows[p][j]:
                    cands[(p, name)] = num(rows[p][j])
    for t in prov.values():
        del t["cand"]  # = valid - blank; build() checks the candidacy sum against it
    return prov, cands


def extremadura(eid):
    text = "\n".join(pages(eid))
    header = re.compile(r"\s*(N[ÚU]MERO \d+|(Lunes|Martes|Miércoles|Jueves|Viernes|Sábado|Domingo),? \d+ de .*|\d{4,5})\s*")
    prov, cands = {}, {}
    for block in text.split("CIRCUNSCRIPCIÓN ELECTORAL DE ")[1:]:
        pid = "06" if block.startswith("BADAJOZ") else "10"
        g = lambda label: num(re.search(label + r"\s+(" + NUM + ")", block).group(1))
        prov[pid] = dict(census=g("ELECTORES"), voters=g("VOTANTES"), valid_votes=g("VÁLIDOS"),
                         blank_votes=g("EN BLANCO"), null_votes=g("NULOS"), cand=g("A CANDIDATURAS"))
        body = block.split("candidaturas ha sido la siguiente:")[1].split("La distribución de los")[0]
        buf = ""
        for line in body.splitlines():
            if header.fullmatch(line):
                continue
            buf = f"{buf} {line.strip()}".strip()
            m = re.fullmatch(r"(.*?)\s*(" + NUM + ")", buf)
            if m:
                cands[(pid, m.group(1))] = num(m.group(2))
                buf = ""
        assert buf == "" and sum(v for (p, _), v in cands.items() if p == pid) == prov[pid].pop("cand"), (eid, pid)
    return prov, cands


def place(rows, totals):
    """Rows list only the non-empty cells; find the unique placement whose column sums equal totals."""
    n = len(totals)
    loose = [k for k, v in rows.items() if len(v) < n]
    rest = [t - sum(v[j] for v in rows.values() if len(v) == n) for j, t in enumerate(totals)]
    sols = []

    def dfs(i, rest, acc):
        if i == len(loose):
            if not any(rest):
                sols.append(dict(acc))
            return
        for pos in itertools.combinations(range(n), len(rows[loose[i]])):
            r = rest[:]
            for p, v in zip(pos, rows[loose[i]]):
                r[p] -= v
            if min(r) >= 0:
                dfs(i + 1, r, {**acc, loose[i]: pos})

    dfs(0, rest, {})
    assert len(sols) == 1, sols
    out = {k: v for k, v in rows.items() if len(v) == n}
    for k, pos in sols[0].items():
        out[k] = [dict(zip(pos, rows[k])).get(j) for j in range(n)]
    return out


def castilla_la_mancha(eid):
    text = "\n".join(pages(eid))
    pids = list(CM_PROV.values())
    prov = {}
    for m in re.finditer(r"^(" + "|".join(CM_PROV) + r") ([\d. ]+)$", text, re.M):  # electores, votantes, a cand., blanco, validos, nulos
        c = list(map(num, m.group(2).split()))
        prov[CM_PROV[m.group(1)]] = dict(census=c[0], voters=c[1], valid_votes=c[4], blank_votes=c[3], null_votes=c[5], cand=c[2])
    body = text.split("Relación de votos correspondientes")[1].split("\n", 2)[2]  # skip the province header line
    rows, buf = {}, ""
    for line in body.splitlines():
        buf = f"{buf} {line.strip()}".strip()
        m = re.fullmatch(r"(.*?)((?:\s+" + NUM + ")+)", buf)  # \s+: empty cells leave a double space
        if not m:
            continue  # name wrapped onto the next line
        vals = list(map(num, m.group(2).split()))
        if m.group(1) == "TOTAL":
            totals = vals[:-1]
            break
        assert sum(vals[:-1]) == vals[-1], (eid, buf)
        rows[m.group(1)] = vals[:-1]
        buf = ""
    assert sum(totals) == sum(t["cand"] for t in prov.values()) and [prov[p].pop("cand") for p in pids] == totals, eid
    cands = {}
    for name, cells in place(rows, totals).items():
        for p, v in zip(pids, cells):
            if v is not None:
                cands[(p, name)] = v
    return prov, cands


def murcia_borm(eid):
    text = "\n".join(pages(eid)[:2])
    g = lambda label: num(re.search(label + r"\s+(" + NUM + ")", text).group(1))
    prov = {"30": dict(census=g("NÚMERO DE ELECTORES"), voters=g("NÚMERO DE VOTANTES"), valid_votes=g("VOTOS VÁLIDOS"),
                       blank_votes=g("EN BLANCO"), null_votes=g("VOTOS NULOS"))}
    # "<n> <NAME IN CAPITALS> <votes in words> <votes> [seats]"
    rows = re.findall(r"^\s*\d{1,2} ([^a-záéíóúñ]+?) [A-ZÁÉÍÓÚ][a-záéíóúñ].*?\s(" + NUM + r")\s+\d*\s*$", text, re.M)
    cands = {("30", name): num(v) for name, v in rows}
    assert sum(cands.values()) == g("CANDIDATURAS"), eid
    return prov, cands


def murcia_wiki(eid):
    page = MC_WIKI[eid]
    html = fetch(f"https://en.wikipedia.org/w/index.php?title={page}&action=render", f"{page}.html").read_text("utf-8")
    table = next(t for t in pd.read_html(io.StringIO(html)) if "Blank ballots" in t.to_string() and "Registered voters" in t.to_string())
    labels = {"Blank ballots": "blank_votes", "Valid votes": "valid_votes", "Invalid votes": "null_votes",
              "Votes cast / turnout": "voters", "Registered voters": "census"}
    t, cands = {}, {}
    for r in table.itertuples(index=False):
        if str(r[0]) in labels:
            t[labels[str(r[0])]] = int(r[2])
        elif str(r[0]) == "nan" and str(r[1]) != "nan" and str(r[2]).isdigit():
            cands[("30", re.sub(r"\)\d+$", ")", str(r[1])))] = int(r[2])
    return {"30": t}, cands


def party_ids(eid, name):
    n = name.upper()
    if n in ("PARTIDO POPULAR", "PP", "PEOPLE'S PARTY (PP)"):
        return "pp"
    if n.startswith(("PARTIDO SOCIALISTA OBRERO ESPAÑOL", "SPANISH SOCIALIST WORKERS' PARTY")) or n == "PSOE":
        return "psoe"
    if n in ("VOX", "VOX (VOX)"):
        return "vox"
    if n == "SE ACABÓ LA FIESTA":
        return "salf"
    if n.startswith("ADELANTE ANDALUC"):  # 2018 = Podemos+IU coalition; 2022+ = the 2021 party
        return "fa;podemos" if eid == "aut-AN-2018-12-02" else "aa"
    if n.startswith(("WE CAN–EQUO", "PODEMOS–EQUO")):  # MC 2019: Podemos+Equo, no IU
        return "podemos"
    if "PODEMOS" in n or n.startswith(("POR ANDALUC", "IZQUIERDA UNIDA ANDALUC")):  # UP-family, Por Andalucía 2022/2026
        return "fa;podemos"
    if n.startswith(("MAS REGION", "CHANGE THE REGION OF MURCIA")):  # Más País brand; IU-Verdes without Podemos
        return "fa"
    return ""


def build(eid):
    region, date = eid.split("-")[1], eid[-10:]
    fn = {"AN": andalucia, "EX": extremadura, "CM": castilla_la_mancha}.get(region)
    prov, cands = fn(eid) if fn else murcia_borm(eid) if eid in PDFS else murcia_wiki(eid)
    rows = [dict(election_id=eid, date=date, type="autonomicas", region=region, province_id=p, candidacy=name,
                 party_ids=party_ids(eid, name), votes=v, pct=f"{100 * v / prov[p]['valid_votes']:.3f}")
            for (p, name), v in cands.items() if v > 0]
    rows.sort(key=lambda r: (r["province_id"], -r["votes"]))
    for p, t in prov.items():
        cand = sum(r["votes"] for r in rows if r["province_id"] == p)
        for what, a, b in [("candidacies+blank vs valid", cand + t["blank_votes"], t["valid_votes"]),
                           ("valid+null vs voters", t["valid_votes"] + t["null_votes"], t["voters"])]:
            assert abs(a - b) <= 60, (eid, p, what, a, b)  # MC 2023 BORM: voters differ from valid+null by 51 in the source itself
            if a != b:  # small inconsistencies inside the official publication itself; kept as published
                print(f"  note {eid} {p}: {what}: {a} vs {b}")
    pd.DataFrame(rows).to_csv(OUT / f"{eid}.csv", index=False)
    pd.DataFrame([{"province_id": p, **{k: prov[p][k] for k in TOTAL_KEYS}} for p in sorted(prov)]).to_csv(
        OUT / f"{eid}_totals.csv", index=False)
    return rows, prov


if __name__ == "__main__":
    SRC.mkdir(parents=True, exist_ok=True)
    expected = {"AN": set(AN_PROV.values()) - {"total"}, "EX": {"06", "10"}, "CM": set(CM_PROV.values()), "MC": {"30"}}
    for eid in sorted([*PDFS, *MC_WIKI]):
        rows, prov = build(eid)
        assert set(prov) == expected[eid.split("-")[1]], (eid, sorted(prov))
        print(eid, len(rows), "rows, provinces", sorted(prov))
