"""Electomanía poll archive (Spain, every territorial level, since 2018) -> raw/polls/electomania/polls.jsonl.

Fast path: the WordPress REST API returns 100 posts with their content per request (~50 requests for ~4,700 posts).
- Posts whose content carries the data template (hidden <div id="nombreEncuesta">...) embed the poll as JSON arrays
  (datosDeVoto / datosDeEscanos: [{name, points, pointsA}]) only in the full page, so just those pages are fetched,
  8 at a time, and parsed once (cache: raw/sources/electomania/<id>.json).
- ElectoPanel posts publish their tables as Datawrapper charts: each chart's dataset.csv ("Partido | EP actual | ...")
  is downloaded (cache: raw/sources/datawrapper/) and the first vote-intention table is kept.
- Every other post is read from its text and tables: "el PP, con el 31,5 %..." -> {PP: 31.5}.
  Text and Datawrapper polls are accepted with >= 3 parties and a sum between 50 and 105. Image-only posts yield nothing.
Each record gets a scope: gen | eur | aut-<CCAA> | mun-<town> (from categories, the "ámbito" field and the title).
Run from the project root:  .venv/Scripts/python pipeline/scrape/polls_electomania_archive.py [--relist]
"""
import html, json, re, sys, time, unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "raw" / "sources" / "electomania"
DW_CACHE = ROOT / "raw" / "sources" / "datawrapper"
OUTDIR = ROOT / "raw" / "polls" / "electomania"
UA = {"User-Agent": "Mozilla/5.0 Elecciones29N (personal research project)", "Accept-Encoding": "gzip"}
POSTS = "https://electomania.es/wp-json/wp/v2/posts"
SINCE = "2018-01-01T00:00:00"
GEN, AUT, MUN, EP_GEN, EP_AUT = 27918, 27917, 27919, 27915, 27931
CATS = [GEN, AUT, MUN, EP_GEN, EP_AUT]  # subestatal (foreign), cabildos, consells, juntas generales left out
# autonómicas sub-categories -> region code (the ElectoPanel autonómicas category has none: region from the title)
AUT_SUB = {28089: "AN", 643: "AR", 644: "AS", 28073: "IB", 646: "CN", 647: "CB", 28090: "CM", 649: "CL", 650: "CT",
           652: "MD", 654: "VC", 655: "EX", 656: "GA", 657: "RI", 653: "NC", 659: "PV", 660: "MC"}
REGION_WORDS = {"andalucia": "AN", "aragon": "AR", "asturias": "AS", "baleares": "IB", "balears": "IB", "canarias": "CN",
                "cantabria": "CB", "castilla y leon": "CL", "castilla-la mancha": "CM", "castilla la mancha": "CM",
                "cataluna": "CT", "catalunya": "CT", "comunidad valenciana": "VC", "comunitat valenciana": "VC",
                "extremadura": "EX", "galicia": "GA", "comunidad de madrid": "MD", "la rioja": "RI", "navarra": "NC",
                "pais vasco": "PV", "euskadi": "PV", "region de murcia": "MC", "murcia": "MC"}
FIELDS = ["fElecciones", "fecha", "nombreEncuesta", "medio", "ambito", "variacion"]
# text parsing: party -> regex of how Spanish posts name it
PARTIES = {"PP": r"\bPP\b|Partido Popular", "PSOE": r"\bPSOE\b|\bPSC\b|\bPSPV\b|PSdeG|PSE-EE|socialistas",
           "Vox": r"\bVox\b|\bVOX\b", "Sumar": r"\bSumar\b|\bSUMAR\b", "Podemos": r"\bPodemos\b|Unidas Podemos|\bUP\b",
           "Cs": r"Ciudadanos|\bCs\b", "Más Madrid": r"Más Madrid", "Más País": r"Más País", "ERC": r"\bERC\b|Esquerra",
           "Junts": r"\bJunts\b|JxCat", "PNV": r"\bPNV\b", "EH Bildu": r"Bildu", "BNG": r"\bBNG\b", "CC": r"\bCC\b|Coalición Canaria",
           "UPN": r"\bUPN\b", "SALF": r"\bSALF\b|Se Acabó la Fiesta", "Compromís": r"Comprom[ií]s", "CUP": r"\bCUP\b",
           "PRC": r"\bPRC\b", "Adelante Andalucía": r"Adelante Andaluc[ií]a", "Aliança Catalana": r"Aliança Catalana",
           "Frente Amplio": r"Frente Amplio", "Teruel Existe": r"Teruel Existe"}
PCT = re.compile(r"(\d{1,2}(?:[.,]\d{1,2})?)\s?(?:%|por ciento)")


def fold(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def get(url, **params):
    for attempt in range(4):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=120)
            if r.status_code in (200, 400):
                return r
        except requests.RequestException:
            pass
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"no response from {url}")


def relist():
    posts = {}
    for cat in CATS:
        page, pages = 1, 1
        while page <= pages:
            r = get(POSTS, categories=cat, after=SINCE, per_page=100, page=page, _fields="id,date,link,categories,title,content")
            if r.status_code != 200:
                break
            pages = int(r.headers.get("X-WP-TotalPages", 1))
            for p in r.json():
                posts[p["id"]] = {"id": p["id"], "date": p["date"], "link": p["link"], "categories": p["categories"],
                                  "title": html.unescape(p["title"]["rendered"]), "content": p["content"]["rendered"]}
            print(f"cat {cat}: page {page}/{pages}, {len(posts)} posts", flush=True)
            page += 1
    (OUTDIR / "index.json").write_text(json.dumps(list(posts.values()), ensure_ascii=False), "utf-8")
    return list(posts.values())


def series(page, var):
    m = re.search(r"var\s+" + var + r"\s*=\s*(\[.*?\]);", page, re.S)
    try:
        rows = json.loads(m.group(1)) if m else []
    except json.JSONDecodeError:
        rows = []
    return {html.unescape(r["name"]).strip(): [r.get("points"), r.get("pointsA")] for r in rows if r.get("name")} or None


def hidden(text, field):
    m = re.search(r'<div id="' + field + r'"[^>]*>(.*?)</div>', text, re.S)
    return html.unescape(m.group(1)).strip() if m else None


def structured(post):
    """Full page -> data arrays (cached per post id)."""
    path = CACHE / f"{post['id']}.json"
    if path.exists():
        return json.loads(path.read_text("utf-8"))
    page = get(post["link"]).text
    rec = {"id": post["id"], "link": post["link"], "pub": post["date"][:10], "categories": post["categories"],
           "title": post["title"], **{f: hidden(page, f) for f in FIELDS}}
    vote = series(page, "datosDeVoto")
    rec.update({"vote": vote, "seats": series(page, "datosDeEscanos")} if vote else {"nodata": True})
    path.write_text(json.dumps(rec, ensure_ascii=False), "utf-8")
    return rec


def from_text(post):
    """Party % from the post text and tables: first number with % within 60 characters after each party name."""
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", post["content"], flags=re.S)
    text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)))
    vote = {}
    for name, rx in PARTIES.items():
        for m in re.finditer(rx, text):
            n = PCT.search(text[m.end():m.end() + 60])
            if n and not re.search(r"\b(sube|baja|puntos|pts|escaños)\b", text[m.end():m.end() + n.start()]):
                vote[name] = [float(n.group(1).replace(",", ".")), None]
                break
    total = sum(v[0] for v in vote.values())
    return vote if len(vote) >= 3 and 50 <= total <= 105 else None


def from_datawrapper(post):
    """First Datawrapper table of the post that reads as vote intention: {party: [pct, None]} or None."""
    for cid, ver in re.findall(r"datawrapper\.dwcdn\.net/([A-Za-z0-9]{5})/(\d+)", post["content"]):
        path = DW_CACHE / f"{cid}_{ver}.csv"
        if not path.exists():
            try:  # a deleted or private chart answers 403/404: cache it as empty
                r = requests.get(f"https://datawrapper.dwcdn.net/{cid}/{ver}/dataset.csv", headers=UA, timeout=60)
                path.write_text(r.text if r.status_code == 200 else "", "utf-8")
            except requests.RequestException:
                continue
        vote = {}
        for line in path.read_text("utf-8").splitlines()[1:]:
            cells = line.split("\t")
            if len(cells) < 2:
                continue
            name = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", cells[0]).strip()
            m = re.fullmatch(r"\s*(\d{1,2}(?:[.,]\d{1,2})?)\s*%?\s*", cells[1])
            if name and m and not re.search(r"\d", name):
                vote[name] = [float(m.group(1).replace(",", ".")), None]
        if len(vote) >= 3 and 50 <= sum(v[0] for v in vote.values()) <= 105:
            return vote
    return None


def pollster_of(title):
    t = re.sub(r"^(#?electopanel|ep)\b", "ElectoPanel", title.strip(), flags=re.I)
    t = re.sub(r"^(nueva\s+)?(encuesta|sondeo|barómetro( del)?|tracking)\s+", "", t, flags=re.I)
    return re.split(r"\s*[(:–\-]\s*|\s+para\s+", t)[0].strip()


def scope_of(rec, post):
    cats, title = set(post["categories"]), fold(post["title"])
    amb = fold(re.sub(r"^\s*para\s+", "", rec.get("ambito") or ""))
    if MUN in cats:
        town = amb or fold(re.split(r"\s*[(:]", re.sub(r"^(encuesta|sondeo)\s+", "", post["title"], flags=re.I))[0])
        return "mun-" + re.sub(r"[^a-z0-9]", "", town)
    if "europe" in title or "europe" in amb:
        return "eur"
    if amb in ("espana", "") and cats & {GEN, EP_GEN} and not cats & {AUT, EP_AUT}:
        return "gen"
    for c, code in AUT_SUB.items():
        if c in cats:
            return "aut-" + code
    for w, code in sorted(REGION_WORDS.items(), key=lambda x: -len(x[0])):
        if w in amb or w in title:
            return "aut-" + code
    return "gen" if amb == "espana" else None


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    OUTDIR.mkdir(parents=True, exist_ok=True)
    index = OUTDIR / "index.json"
    posts = json.loads(index.read_text("utf-8")) if index.exists() and "--relist" not in sys.argv else []
    if not posts or "content" not in posts[0]:
        posts = relist()
    templ = [p for p in posts if 'id="nombreEncuesta"' in p["content"]]
    print(f"{len(posts)} posts; {len(templ)} with the data template (full page needed)", flush=True)
    recs = []
    with ThreadPoolExecutor(8) as pool:
        for i, rec in enumerate(pool.map(structured, templ), 1):
            recs.append(rec)
            if i % 250 == 0:
                print(f"  pages {i}/{len(templ)}", flush=True)
    by_id = {p["id"]: p for p in posts}
    out, n_text = [], 0
    for rec in recs:
        if rec.get("vote"):
            out.append({**rec, "source": "data"})
    done = {r["id"] for r in out}
    rest = [p for p in posts if p["id"] not in done]
    DW_CACHE.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(8) as pool:
        dws = list(pool.map(lambda p: from_datawrapper(p) if "datawrapper" in p["content"] else None, rest))
    for p, dw in zip(rest, dws):
        vote, src = (dw, "datawrapper") if dw else (from_text(p), "text")
        if vote:
            n_text += 1
            out.append({"id": p["id"], "link": p["link"], "pub": p["date"][:10], "categories": p["categories"],
                        "title": p["title"], "nombreEncuesta": pollster_of(p["title"]), "vote": vote, "source": src})
    kept = []
    for r in out:
        r["scope"] = scope_of(r, by_id.get(r["id"], r))
        if r["scope"]:
            kept.append(r)
    with (OUTDIR / "polls.jsonl").open("w", encoding="utf-8") as f:
        for r in sorted(kept, key=lambda r: r["pub"]):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    from collections import Counter
    print(f"{len(kept)} polls -> {OUTDIR / 'polls.jsonl'}")
    print(Counter(r["source"] for r in kept), Counter(r["scope"].split("-")[0] for r in kept))


if __name__ == "__main__":
    main()
