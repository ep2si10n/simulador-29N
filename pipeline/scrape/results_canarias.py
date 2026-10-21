"""Autonomicas Canarias (CN) 2019 and 2023, island ballots only. Regional-list ballot ignored.

Source (cached in raw/sources/aut_center/, downloaded only when missing):
  parcan.es/elecciones/{year}/islas/{code}/ - Parlamento de Canarias, "Resumen resultados <isla>":
  electores, votos validos, blancos, nulos and votes per candidacy, final official results.
  Islands: eh El Hierro, fv Fuerteventura, gc Gran Canaria, lg La Gomera, lp La Palma, lz Lanzarote, tf Tenerife.
  Province 35 = Las Palmas (fv, gc, lz); 38 = Santa Cruz de Tenerife (eh, lg, lp, tf).
Checks: each island's candidacy votes + blanks = votos validos; every candidacy name is mapped.
Run: .venv/Scripts/python pipeline/scrape/results_canarias.py
"""
import csv, html, re, time, unicodedata
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "raw" / "sources" / "aut_center"
OUT = ROOT / "raw" / "results"
UA = {"User-Agent": "Elecciones29N/0.1 (personal research project)"}
HEADER = ["election_id", "date", "type", "region", "province_id", "candidacy", "party_ids", "votes", "pct"]
ELECTIONS = {"aut-CN-2019-05-26": "2019-05-26", "aut-CN-2023-05-28": "2023-05-28"}
ISLAND_PROV = {"eh": "38", "fv": "35", "gc": "35", "lg": "38", "lp": "38", "lz": "35", "tf": "38"}

# Normalized candidacy name -> (label, party_ids). Pre-2023 Sí Podemos Canarias = podemos (no IU in list).
CAND = {
    "PARTIDO SOCIALISTA OBRERO ESPAÑOL": ("PSOE", "psoe"),
    "PARTIDO POPULAR": ("PP", "pp"),
    "VOX": ("VOX", "vox"),
    "COALICIÓN CANARIA-PARTIDO NACIONALISTA CANARIO": ("CC", "cc"),
    "COALICION CANARIA": ("CC", "cc"),
    "SÍ PODEMOS CANARIAS": ("SI PODEMOS CANARIAS", "podemos"),
    "UNIDAS SÍ PODEMOS": ("UNIDAS SI PODEMOS", "fa"),
    "IZQUIERDA UNIDA CANARIA": ("IZQUIERDA UNIDA CANARIA", "fa"),
    "NUEVA CANARIAS": ("NUEVA CANARIAS", ""),
    "NUEVA CANARIAS - BLOQUE CANARISTA": ("NUEVA CANARIAS", ""),
    "CIUDADANOS-PARTIDO DE LA CIUDADANÍA": ("CIUDADANOS", ""),
    "CIUDADANOS-PARTIDO DE LA CIUDADANIA": ("CIUDADANOS", ""),
    "PARTIDO ANIMALISTA CONTRA EL MALTRATO ANIMAL": ("PARTIDO ANIMALISTA", ""),
    "PARTIDO ANIMALISTA CON EL MEDIO AMBIENTE": ("PARTIDO ANIMALISTA", ""),
    "AGRUPACION SOCIALISTA GOMERA": ("ASG", ""),
    "AGRUPACIÓN SOCIALISTA TENERIFE": ("AGRUPACION SOCIALISTA TENERIFE", ""),
    "INICIATIVA POR LA GOMERA": ("INICIATIVA POR LA GOMERA", ""),
    "LOS VERDES-GRUPO VERDE": ("LOS VERDES", ""),
    "AHORA CANARIAS: ALTERNATIVA NACIONALISTA CANARIA (ANC) Y UNIDAD DEL PUEBLO": ("AHORA CANARIAS", ""),
    "AHORA CANARIAS-PARTIDO COMUNISTA DEL PUEBLO CANARIO": ("AHORA CANARIAS", ""),
    "PARTIDO COMUNISTA DEL PUEBLO CANARIO": ("PCPC", ""),
    "CONTIGO SOMOS DEMOCRACIA": ("CONTIGO SOMOS DEMOCRACIA", ""),
    "UNIÓN DEMOCRÁTICA DE CANARIAS": ("UDC", ""),
    "UNION DEMOCRATICA DE CANARIAS": ("UDC", ""),
    "TERCERA EDAD EN ACCIÓN": ("TERCERA EDAD EN ACCION", ""),
    "DRAGO VERDES CANARIAS": ("DRAGO VERDES CANARIAS", ""),
    "PARTIDO NACIONALISTA CANARIO": ("PNC", ""),
    "MÁS CANARIAS": ("MAS CANARIAS", ""),
    "REUNIR CANARIAS SOSTENIBLE": ("REUNIR CANARIAS SOSTENIBLE", ""),
    "HABLEMOS AHORA": ("HABLEMOS AHORA", ""),
    "UNIDOS POR GRAN CANARIA": ("UNIDOS POR GRAN CANARIA", ""),
    "MOVIMIENTO ALTERNATIVO ELECTORAL": ("MOVIMIENTO ALTERNATIVO ELECTORAL", ""),
    "ASAMBLEAS MUNICIPALES DE FUERTEVENTURA": ("ASAMBLEAS MUNICIPALES DE FUERTEVENTURA", ""),
    "NIVARIA": ("NIVARIA", ""),
    "SENTIDO COMÚN SANTA CRUZ": ("SENTIDO COMUN SANTA CRUZ", ""),
    "PARTIDO DE FUERTEVENTURA": ("PARTIDO DE FUERTEVENTURA", ""),
    "VOTEMOS FUERTEVENTURA": ("VOTEMOS FUERTEVENTURA", ""),
    "PARTIDO SOCIALISTA LIBRE FEDERACIÓN": ("PSLF", ""),
    "MAS POR TELDE": ("MAS POR TELDE", ""),
    "CANARIAS POR EL PROGRESO": ("CANARIAS POR EL PROGRESO", ""),
    "UNIDOS POR LANZAROTE": ("UNIDOS POR LANZAROTE", ""),
    "POR UN MUNDO MÁS JUSTO": ("POR UN MUNDO MAS JUSTO", ""),
    "PARTIDO HUMANISTA": ("PARTIDO HUMANISTA", ""),
    "AGRUPACIÓN HERREÑA INDEPENDIENTE": ("AHI", ""),
    "ASAMBLEA HERREÑA": ("ASAMBLEA HERREÑA", ""),
}


def norm(s):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().upper()).strip()


CAND = {norm(k): v for k, v in CAND.items()}


def fetch(year, code):
    path = SRC / f"parcan_{year}_{code}.html"
    if not path.exists():
        r = requests.get(f"https://www.parcan.es/elecciones/{year}/islas/{code}/", headers=UA, timeout=120)
        r.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
        time.sleep(1)
    return path


def flat_text(path):
    t = path.read_text(encoding="utf-8", errors="replace")
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", t, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t)))


def num(s):
    return int(s.replace(".", "").replace(" ", ""))


def parse_island(text):
    """-> dict(electores, validos, blancos, nulos, cands=[(name, votes)])."""
    m = re.search(r"Electores ([\d.]+) Votos válidos ([\d.]+) Votos a candidaturas ([\d.]+) "
                  r"Votos en blanco ([\d.]+) Votos nulos ([\d.]+)", text)
    e, v, c, b, n = (num(x) for x in m.groups())
    head = "C a n d i d a t u r a Votos %"
    sec = text[text.index(head) + len(head): text.index("Candidatos electos")]
    cands = [(name.strip(), num(votes)) for name, votes in re.findall(r"([^\d]+?)\s(\d{1,3}(?: \d{3})*)(?= [^\d]|$)", sec.strip())]
    assert sum(x for _, x in cands) == c, ("candidacy sum", sum(x for _, x in cands), c)
    assert c + b == v, ("valid = candidacies + blank", c, b, v)
    return dict(electores=e, validos=v, blancos=b, nulos=n, cands=cands)


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    unknown = set()
    for eid, date in ELECTIONS.items():
        year = int(date[:4])
        prov_tot, prov_cand = {}, {}
        for code, prov in ISLAND_PROV.items():
            isl = parse_island(flat_text(fetch(year, code)))
            t = prov_tot.setdefault(prov, [0, 0, 0, 0, 0])  # census, voters, valid, blank, null
            t[0] += isl["electores"]
            t[1] += isl["validos"] + isl["nulos"]
            t[2] += isl["validos"]
            t[3] += isl["blancos"]
            t[4] += isl["nulos"]
            cs = prov_cand.setdefault(prov, {})
            for name, votes in isl["cands"]:
                key = norm(name)
                if key not in CAND:
                    unknown.add(name)
                    continue
                label, party = CAND[key]
                cs[(label, party)] = cs.get((label, party), 0) + votes
        assert not unknown, sorted(unknown)
        rows = []
        for prov in sorted(prov_tot):
            valid = prov_tot[prov][2]
            kept = [(lab, par, v) for (lab, par), v in prov_cand[prov].items() if 100 * v / valid >= 0.3]
            for lab, par, v in sorted(kept, key=lambda r: -r[2]):
                rows.append([eid, date, "autonomicas", "CN", prov, lab, par, v, f"{100 * v / valid:.4f}"])
            # check: all candidacy votes + blanks = valid
            full = sum(prov_cand[prov].values()) + prov_tot[prov][3]
            assert full == valid, (eid, prov, full, valid)
        with open(OUT / f"{eid}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(HEADER)
            w.writerows(rows)
        with open(OUT / f"{eid}_totals.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["province_id", "census", "voters", "valid_votes", "blank_votes", "null_votes"])
            for prov in sorted(prov_tot):
                w.writerow([prov, *prov_tot[prov]])
        print(eid, {p: prov_tot[p] for p in sorted(prov_tot)}, len(rows), "rows")


if __name__ == "__main__":
    run()
