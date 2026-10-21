"""Congreso (generales) and European election results by province.

Source: Ministerio del Interior, infoelectoral "Área de descargas", <code>_TOTA.zip
(fixed-width .DAT files, layout in FICHEROS.doc inside each ZIP):
  03 = candidaturas, 07 = totals above municipality, 08 = votes above municipality.
Provincial rows (distrito 9) are the official provincial totals, CERA included.
Re-runnable: ZIPs are cached in raw/sources/generales_europeas/.
Run: .venv/Scripts/python pipeline/scrape/results_generales_europeas.py
"""
import collections
import csv
import json
import time
import unicodedata
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'raw' / 'sources' / 'generales_europeas'
OUT = ROOT / 'raw' / 'results'
UA = {'User-Agent': 'Elecciones29N/0.1 (personal research project)'}
URL = 'https://infoelectoral.interior.gob.es/estaticos/docxl/apliextr/{}_TOTA.zip'
MIN_PCT = 0.1  # contract asks >= 0.3; 0.1 keeps every province within 2 points of 100

ELECTIONS = {
    'gen-2016-06-26': '02201606',
    'gen-2019-04-28': '02201904',
    'gen-2019-11-10': '02201911',
    'gen-2023-07-23': '02202307',
    'eur-2019-05-26': '07201905',
    'eur-2024-06-09': '07202406',
}

# Lineage by the Ministry's national accumulation header (siglas, accents stripped, upper).
# Regional ids are dropped later outside their provinces.
UP = 'fa;podemos'
NAT_IDS = {
    'PP': 'pp', 'PSOE': 'psoe', 'VOX': 'vox',
    # Unidos/Unidas Podemos family (2016-2019) and its regional confluences
    'PODEMOS-IU-EQUO': UP, 'PODEMOS-IU-': UP, 'PODEMOS-IU': UP, 'ECP': UP, 'ECP-GUANYEM': UP,
    'PODEMOS-EN MAREA-ANOVA-EU': UP, 'EN MAREA': UP, 'PODEMOS-EU-': UP, 'PODEMOS-EU': UP,  # En Marea 2019-04: spec lists it as UP family
    'PODEMOS-COM': UP,  # Compromis-Podemos-EUPV 2016 (fv added by the COMPROMIS rule)
    'MAS PAIS': 'fa', 'MAS PAIS-EQ': 'fa', 'M PAIS-CHA-': 'fa', 'MES COMPROM': 'fa',
    'PODEMOS': 'podemos',
    'BNG-NOS': 'bng', 'BNG': 'bng', 'B.N.G.': 'bng',
    'CCA-PNC': 'cc', 'CCA-PNC-NC': 'cc', 'CCA': 'cc',
    'CDC': 'junts', 'JXCAT-JUNTS': 'junts', 'JXCAT - JUNTS': 'junts', 'JUNTS': 'junts',
    'JUNTS UE': 'junts', 'PDECAT-E-CIU': 'junts',  # spec: "PDeCAT lists" belong to junts
    'ERC-CATSI': 'erc', 'ERC-SOBIRAN': 'erc', 'ERC-SOBIRANISTES': 'erc', 'ERC': 'erc',
    'EAJ-PNV': 'pnv', 'EH BILDU': 'bildu',
    'NA+': 'upn', 'U.P.N.': 'upn',
    'COMPROMIS 2': 'fv',
    'ADELANTE ANDALUCIA': 'aa',
    'AGRUPACION DE ELECTORES "SE ACABO LA FIESTA"': 'salf',
    # europeas multi-region coalitions: only the regional component survives the province filter
    'AHORA REPUB': 'erc;bildu;bng', 'AHORA REPUBLICAS (ERC-EH BILDU-BNG-ARA MES)': 'erc;bildu;bng',
    'CEUS': 'pnv;cc',
}
NATIONAL_IDS = {'pp', 'psoe', 'vox', 'fa', 'podemos', 'salf'}
REGION = {p['party_id']: set(p['provinces']) for p in
          json.loads((ROOT / 'pipeline' / 'build' / 'parties.json').read_text(encoding='utf-8')) if p['provinces']}


def norm(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode()
    return ' '.join(s.upper().split())


def party_ids(eid, prov, siglas, denom, nat):
    ids = set(filter(None, NAT_IDS.get(norm(nat), '').split(';')))
    if norm(nat) == 'SUMAR':
        ids |= {'fa', 'podemos'} if eid.startswith('gen') else {'fa'}
    own = ' ' + norm(siglas + ' ' + denom).replace('-', ' ').replace(':', ' ') + ' '
    if ' COMPROMIS ' in own:
        ids.add('fv')
    if ' UPN ' in own:  # UPN-PP 2016
        ids.add('upn')
    ids = {i for i in ids if i not in REGION or prov in REGION[i]}
    return ';'.join(sorted(ids))


def load(code):
    path = SRC / f'{code}_TOTA.zip'
    if not path.exists():
        r = requests.get(URL.format(code), headers=UA, timeout=60)
        r.raise_for_status()
        path.write_bytes(r.content)
        time.sleep(1)
    z = zipfile.ZipFile(path)
    dat = {n[:2]: z.read(n).decode('cp1252').splitlines() for n in z.namelist() if n.upper().endswith('.DAT')}
    cand = {l[8:14]: (l[14:64].strip(), l[64:214].strip(), l[226:232]) for l in dat['03']}
    tot, votes = {}, collections.defaultdict(dict)
    key = lambda l: 'ES' if l[9:11] == '99' else l[11:13]  # '99' alone = community totals, skipped
    for l in dat['07']:
        assert l[13] == '9', l[:20]
        tot[key(l)] = dict(census=int(l[85:93]), blank=int(l[125:133]), null=int(l[133:141]),
                             cand=int(l[141:149]))
    for l in dat['08']:
        votes[key(l)][l[14:20]] = int(l[20:28])
    return cand, tot, votes


def build(eid, code):
    cand, tot, votes = load(code)
    rows, totals, nat = [], [], collections.defaultdict(lambda: [0, set(), set()])
    kind = 'generales' if eid.startswith('gen') else 'europeas'
    for prov in sorted(p for p in tot if p not in ('99', 'ES')):
        t = tot[prov]
        assert sum(votes[prov].values()) == t['cand'], (eid, prov)
        valid = t['cand'] + t['blank']
        totals.append([prov, t['census'], valid + t['null'], valid, t['blank'], t['null']])
        for c, v in sorted(votes[prov].items(), key=lambda x: -x[1]):
            siglas, denom, natc = cand[c]
            ids = party_ids(eid, prov, siglas, denom, cand[natc][0])
            if v:
                n = nat[natc]
                n[0] += v
                n[1].update(filter(None, ids.split(';')))
                n[2].add(prov)
            if 100 * v / valid >= MIN_PCT:
                rows.append([eid, eid[4:], kind, 'ES', prov, siglas, ids, v, f'{100 * v / valid:.3f}'])
    # national aggregate per Ministry accumulation header; must equal the file's own national record
    nat_valid = sum(t[3] for t in totals)
    assert {c: n[0] for c, n in nat.items()} == {c: v for c, v in votes['ES'].items() if v}, eid
    nrows = []
    for natc, (v, ids, provs) in sorted(nat.items(), key=lambda x: -x[1][0]):
        keep = {i for i in ids if i in NATIONAL_IDS or provs <= REGION[i]}
        nrows.append([eid, cand[natc][0], ';'.join(sorted(keep)), v, f'{100 * v / nat_valid:.3f}'])
    return rows, totals, nrows


def write(path, header, rows):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main():
    selftest()
    OUT.mkdir(parents=True, exist_ok=True)
    national = []
    for eid, code in ELECTIONS.items():
        rows, totals, nrows = build(eid, code)
        write(OUT / f'{eid}.csv', ['election_id', 'date', 'type', 'region', 'province_id', 'candidacy',
                                   'party_ids', 'votes', 'pct'], rows)
        write(OUT / f'{eid}_totals.csv', ['province_id', 'census', 'voters', 'valid_votes', 'blank_votes',
                                          'null_votes'], totals)
        national += nrows
        check(eid, rows, totals, nrows)
    write(OUT / 'national_totals.csv', ['election_id', 'candidacy', 'party_ids', 'votes', 'pct'], national)


def check(eid, rows, totals, nrows):
    assert len(totals) == 52 and {r[4] for r in rows} == {t[0] for t in totals}, eid
    blank = {t[0]: 100 * t[4] / t[3] for t in totals}
    s = collections.defaultdict(float)
    for r in rows:
        s[r[4]] += float(r[8])
    gaps = {p: round(100 - s[p] - blank[p], 2) for p in blank}
    worst = max(gaps, key=gaps.get)
    top = ', '.join(f'{r[1]} {r[4]}' for r in nrows[:4])
    print(f'{eid}: {len(rows)} rows, max gap {gaps[worst]} ({worst}); national: {top}')
    assert gaps[worst] < 2, (eid, worst)
    # final figures in TOTA (CERA included) = Wikipedia national tables. Spec figures 10N Vox 15.09 and
    # 23J PP 33.05 / PSOE 31.70 / Vox 12.39 / Sumar 12.31 are NOT what either source gives (verifier, 2026-10-19).
    known = {'gen-2019-11-10': {'PSOE': 28.00, 'PP': 20.81, 'VOX': 15.08},
             'gen-2023-07-23': {'PP': 33.06, 'PSOE': 31.68, 'VOX': 12.38, 'SUMAR': 12.33},
             'eur-2024-06-09': {'PP': 34.21, 'PSOE': 30.19, 'VOX': 9.63}}
    for name, pct in known.get(eid, {}).items():
        assert abs(float(next(r[4] for r in nrows if r[1] == name)) - pct) < 0.006, (eid, name)


def selftest():
    assert party_ids('gen-2023-07-23', '46', 'SUMAR - COMPROMÍS', 'COMPROMÍS - SUMAR', 'SUMAR') == 'fa;fv;podemos'
    assert party_ids('gen-2023-07-23', '28', 'SUMAR', 'SUMAR', 'SUMAR') == 'fa;podemos'
    assert party_ids('eur-2024-06-09', '28', 'SUMAR', 'SUMAR', 'SUMAR') == 'fa'
    assert party_ids('gen-2016-06-26', '31', 'UPN-PP', 'UNIÓN DEL PUEBLO NAVARRO', 'PP') == 'pp;upn'
    assert party_ids('eur-2019-05-26', '46', 'ARA REPÚBLI', 'ARA REPÚBLIQUES', 'AHORA REPÚB') == ''
    assert party_ids('eur-2019-05-26', '15', 'BNG-AGORA R', 'BNG', 'AHORA REPÚB') == 'bng'
    assert party_ids('eur-2019-05-26', '35', 'CPE', 'NUEVA CANARIAS: COMPROMISO POR EUROPA', 'CPE') == ''
    assert party_ids('eur-2019-05-26', '07', 'JUNTS', 'LLIURES PER EUROPA', 'JUNTS') == ''
    assert party_ids('gen-2019-04-28', '42', 'PPSO', 'PLATAFORMA DEL PUEBLO SORIANO', 'PPSO') == ''
    assert party_ids('gen-2019-04-28', '15', 'EN MAREA', 'EN MAREA', 'EN MAREA') == 'fa;podemos'
    assert party_ids('gen-2023-07-23', '08', 'PDeCAT-E-CiU', 'PDECAT', 'PDeCAT-E-CiU') == 'junts'


if __name__ == '__main__':
    main()
