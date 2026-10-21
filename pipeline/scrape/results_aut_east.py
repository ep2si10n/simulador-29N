"""Autonomicas, eastern group: Cataluña, Comunitat Valenciana, Illes Balears, Aragón.

Sources (cached under raw/sources/aut_east/, downloaded only if missing):
  CT - Generalitat de Catalunya results export (resultats.dadeselectorals.gencat.cat), province level, incl. CERA.
  VC - Junta Electoral de la Comunitat Valenciana final results (DOGV), PDF mirrored by the Junta Electoral Central.
  IB - Junta Electoral de les Illes Balears proclamation acts (BOIB), PDF mirrored by the JEC; islands summed into 07.
  AR - Aragón open data (IAEST, opendata.aragon.es GA_OD_Core resources 374/375), province level.
Run: .venv/Scripts/python pipeline/scrape/results_aut_east.py
"""
import csv, io, re, time, zipfile
from pathlib import Path

import requests
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "raw" / "sources" / "aut_east"
OUT = ROOT / "raw" / "results"
UA = {"User-Agent": "Elecciones29N/0.1 (personal research project)"}

GENCAT = "https://resultats.dadeselectorals.gencat.cat/webelec/api/proces/{}/nivellTerritorial/PR/exportacio"
JEC = "https://www.juntaelectoralcentral.es/cs/jec/documentos/"
ARAGON = 'https://opendata.aragon.es/GA_OD_Core/download?resource_id={}&formato=csv&filters={{"cod_elec": "{}"}}'

CT = {"aut-CT-2017-12-21": "A20171", "aut-CT-2021-02-14": "A20211", "aut-CT-2024-05-12": "A20241"}
VC = {"aut-VC-2019-04-28": "VALENCIA_2019_Resultado.pdf", "aut-VC-2023-05-28": "VALENCIA_2023_Resultados.pdf"}
IB = {"aut-IB-2019-05-26": "BALEARES_2019_Proclamacion electos.pdf", "aut-IB-2023-05-28": "BALEARES_2023_Resultados.pdf"}
AR = {"aut-AR-2019-05-26": "CA2019", "aut-AR-2023-05-28": "CA2023", "aut-AR-2026-02-08": "CA2026"}
AR_PROV = {"Huesca": "22", "Teruel": "44", "Zaragoza": "50"}

# Lineage (see task rules). Keys are candidacy names exactly as written to the CSV; anything absent -> "".
PARTY = {
    # Cataluña (gencat names)
    "Partit dels Socialistes de Catalunya (PSC-PSOE)": "psoe", "Partit Popular": "pp", "Vox": "vox",
    "Esquerra Republicana de Catalunya-Catalunya Sí": "erc", "Esquerra Republicana de Catalunya": "erc",
    "Junts per Catalunya": "junts", "Junts + Carles Puigdemont per Catalunya-Junts+": "junts",
    "Partit Demòcrata Europeu Català": "junts",  # PDeCAT 2021
    "Catalunya en Comú-Podem": "fa;podemos", "En Comú Podem-Podem en Comú": "fa;podemos",  # UP-family lists
    "Comuns Sumar": "fa", "Aliança Catalana": "ac",
    # Comunitat Valenciana (DOGV names)
    "Partido Socialista Obrero Español (PSOE)": "psoe", "Partido Popular (PP)": "pp", "VOX": "vox", "Vox (VOX)": "vox",
    "COALICIÓ COMPROMÍS: BLOC-INICIATIVA-VERDSEQUO (COMPROMÍS)": "fv", "Compromís: Més-Iniciativa-Verdsequo (COMPROMÍS)": "fv",
    "COALICIÓ UNIDES PODEM-ESQUERRA UNIDA (UNIDES PODEM-EUPV)": "fa;podemos", "Unides Podem-Esquerra Unida (UP-EUPV)": "fa;podemos",
    # Illes Balears (BOIB names)
    "Partido Popular": "pp", "Partit Socialista de les Illes Balears": "psoe", "Vox-Baleares": "vox",
    "Unidas Podemos-Unides Podem": "fa;podemos", "Unidas Podemos": "fa;podemos",  # Podemos + EUIB
    "Sa Unió de Formentera (PP-Compromís Formentera)": "pp", "Sa Unió de Formentera (PP-Compromís)": "pp",  # Compromís Formentera is local
    "Gent per Formentera+Partit Socialista de les Illes Balears+ Esquerra Unida de les Illes Balears": "psoe;fa",  # GxF+PSIB+EUIB
    "Gent per Formentera + Partit Socialista de les Illes Balears": "psoe",
    # Aragón (IAEST names)
    "PARTIDO SOCIALISTA OBRERO ESPAÑOL": "psoe", "PARTIDO POPULAR": "pp",
    "IZQUIERDA UNIDA DE ARAGON": "fa", "IZQUIERDA UNIDA": "fa", "IZQUIERDA UNIDA- MOVIMIENTO SUMAR": "fa",
    "PODEMOS-EQUO": "podemos", "PODEMOS ALIANZA VERDE": "podemos", "PODEMOS - ALIANZA VERDE": "podemos",  # Podemos without IU
    "SE ACABÓ LA FIESTA": "salf",
}
# Same list spelled differently across Balearic islands.
IB_RENAME = {"Proyecto Libertal Español": "Proyecto Liberal Español", "Acúa": "Actúa"}


def fetch(url, name):
    path = SRC / name
    if not path.exists():
        r = requests.get(url, headers=UA, timeout=60)
        r.raise_for_status()
        path.write_bytes(r.content)
        time.sleep(1)
    return path


def num(s):
    return int(s.replace(".", ""))


def pdf_lines(path):
    return [l.strip() for p in PdfReader(path).pages for l in p.extract_text().splitlines()]


def catalonia(code):
    z = zipfile.ZipFile(fetch(GENCAT.format(code), f"ct_{code}_PR.zip"))
    read = lambda tag: list(csv.DictReader(io.StringIO(z.read(next(n for n in z.namelist() if tag in n)).decode("utf-8-sig").lstrip("﻿")), delimiter=";"))
    votes = [(f"{int(r['Codi Província']):02d}", r["Nom Candidatura"], int(r["Vots"])) for r in read("-Vots-")]
    tot = {f"{int(r['Codi Província']):02d}": (int(r["Total cens electoral"]), int(r["Total votants"]), int(r["Vots vàlids"]),
                                               int(r["Vots en blanc"]), int(r["Vots nuls"])) for r in read("-Columnes-")}
    return votes, tot


def valencia(pdf):
    lines = pdf_lines(fetch(JEC + pdf, "jec_" + pdf.replace(" ", "_")))
    row = lambda label: [num(x) for x in next(l for l in lines if l.replace(" ", "").startswith(label)).split()[-4:]]
    provs = ["03", "12", "46"]
    cens, vot, blank, null = (row(k) for k in ("Electors/Electores", "Votants/Votantes", "Votsenblanc", "Votsnuls"))
    tot = {p: (cens[i], vot[i], vot[i] - null[i], blank[i], null[i]) for i, p in enumerate(provs)}
    votes, pending = [], ""
    start = next(i for i, l in enumerate(lines) if l.replace(" ", "").startswith("Votsnuls")) + 1
    for l in lines[start:]:
        if not l or "DIPUTA" in l.upper():
            break
        m = re.match(r"^(.*?)\s*([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+)$", l)
        if not m:
            pending += l + " "
            continue
        name = re.sub(r"\s+", " ", re.sub(r"V (?=A)", "V", pending + m[1])).replace("A VANT", "AVANT").replace("- ", "-").strip()
        n = [num(x) for x in m.groups()[1:]]
        assert sum(n[:3]) == n[3], (pdf, name, n)  # province columns add up to the community total
        votes += [(p, name, n[i]) for i, p in enumerate(provs)]
        pending = ""
    return votes, tot


def baleares(pdf):
    lines = pdf_lines(fetch(JEC + pdf.replace(" ", "%20"), "jec_" + pdf.replace(" ", "_")))
    end = next((i for i, l in enumerate(lines) if l.startswith("Elecciones a Consejos Insulares")), len(lines))
    lines = lines[:end]  # 2019 act also proclaims Consells Insulars
    agg, tot = {}, [0] * 5
    for s in [i for i, l in enumerate(lines) if l.startswith("CIRCUMSCRIPCIÓN DE")]:
        block = lines[s:s + 80]
        get = lambda pat: num(next(re.search(r"([\d.]+)$", l)[1] for l in block if re.match(pat, l)))
        isl = [get(r"Núm\. de electores"), get(r"Núm\. de votantes"), get(r"Núm\. de votos v[aá]lidos"),
               get(r"Núm\. votos en blanco"), get(r"Núm\. de votos nulos")]
        cands, pending = get(r"Núm\. votos candidaturas"), ""
        got = 0
        for l in block[next(i for i, l in enumerate(block) if l.startswith("CANDIDATURAS")) + 1:]:
            if l.startswith(("De acuerdo", "http")):
                break
            if not l or l == "ELECTOS" or re.fullmatch(r"\d+\.", l):
                continue
            pending += " " + l
            m = re.match(r"^\s*\d+\.\s*(.+?)\s+([\d.]+)\s+(\d+)\s*$", pending)
            if m:
                name = re.sub(r"\s+", " ", m[1]).replace(" )", ")").strip()
                name = IB_RENAME.get(name, name)
                agg[name] = agg.get(name, 0) + num(m[2])
                got += num(m[2])
                pending = ""
        assert got == cands and isl[2] == cands + isl[3] and isl[1] == isl[2] + isl[4], (pdf, lines[s], got, cands, isl)
        tot = [a + b for a, b in zip(tot, isl)]
    return [("07", n, v) for n, v in agg.items()], {"07": tuple(tot)}


def fix_mojibake(s):  # some IAEST rows are UTF-8 read as cp1252 and re-encoded
    if "Ã" not in s:
        return s
    return bytes(ord(c) if ord(c) < 256 else c.encode("cp1252")[0] for c in s).decode("utf-8")


def aragon(code):
    read = lambda rid: list(csv.DictReader(io.StringIO(fetch(ARAGON.format(rid, code), f"ar_r{rid}_{code}.csv").read_text("utf-8"))))
    votes = [(AR_PROV[r["nombre_p"]], fix_mojibake(r["nombre_par"]).strip(), int(r["votos_p"])) for r in read(375)]
    tot = {AR_PROV[r["nombre_p"]]: tuple(int(r[k]) for k in ("electores_p", "votantes_p", "validos_p", "blancos_p", "nulos_p"))
           for r in read(374)}
    return votes, tot


def write(eid, votes, tot):
    region, date = eid.split("-")[1], eid[-10:]
    with open(OUT / f"{eid}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["election_id", "date", "type", "region", "province_id", "candidacy", "party_ids", "votes", "pct"])
        for p, name, v in sorted(votes, key=lambda x: (x[0], -x[2])):
            if v > 0:
                w.writerow([eid, date, "autonomicas", region, p, name, PARTY.get(name, ""), v, f"{100 * v / tot[p][2]:.3f}"])
    with open(OUT / f"{eid}_totals.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["province_id", "census", "voters", "valid_votes", "blank_votes", "null_votes"])
        w.writerows([p, *t] for p, t in sorted(tot.items()))
    for p, t in tot.items():  # candidacies + blank should equal valid votes (all candidacies are kept)
        gap = t[2] - t[3] - sum(v for q, _, v in votes if q == p)
        if gap:  # only VC 2019: the DOGV table itself is 952 votes short of voters - null (Wikipedia shows the same)
            print(f"  {eid} {p}: valid - blank - candidacies = {gap}")
        assert abs(gap) <= t[2] / 1000, (eid, p, gap)
    print(eid, "ok:", len(votes), "rows,", len(tot), "provinces")


def main():
    SRC.mkdir(parents=True, exist_ok=True)
    for eid, code in CT.items():
        write(eid, *catalonia(code))
    for eid, pdf in VC.items():
        write(eid, *valencia(pdf))
    for eid, pdf in IB.items():
        write(eid, *baleares(pdf))
    for eid, code in AR.items():
        write(eid, *aragon(code))


if __name__ == "__main__":
    main()
