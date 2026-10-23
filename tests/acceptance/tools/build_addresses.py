"""Build tests/acceptance/runcity_addresses.json: Latin street names from Runcity CP cards.

Every ordinary CP card of past Tbilisi games gives its address in Russian with a Latin form in
brackets, e.g. "Ул. Галактиона Табидзе (Galaktioni st.), д. 15". Players type such Latin forms
(also seen on plaques) into the search. For each card we resolve the OSM street by name
similarity plus distance to the CP point, and emit queries with the Latin words only.
Only addresses (facts) are stored, no riddle texts.

Usage: uv run python tests/acceptance/tools/build_addresses.py
       (needs data/raw/runcity/extracted.json and data/raw/prototype_cache/overpass/*)
"""
from __future__ import annotations

import difflib
import json
import logging
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from pipeline.names.translit import skeleton  # noqa: E402

EXTRACTED = ROOT / "data" / "raw" / "runcity" / "extracted.json"
OVERPASS = ROOT / "data" / "raw" / "prototype_cache" / "overpass"
OUT = ROOT / "tests" / "acceptance" / "runcity_addresses.json"
LOGS = ROOT / "logs"
MAX_DIST_M = 300
MIN_SCORE = 0.62

LAT_TYPE = {
    "st", "str", "street", "ave", "av", "avenue", "lane", "turn", "rise", "ascent", "descent",
    "dead", "end", "deadend", "square", "sq", "park", "garden", "emb", "embankment", "highway",
    "hwy", "road", "rd", "bridge", "alley", "blind", "exit", "passage", "row", "1st", "2nd", "3rd",
    "4th", "5th", "the", "left", "right", "named", "bank", "bldg", "bl", "blds", "korp", "block", "q",
    "enbankment", "quarter", "microdistrict", "district", "metro", "station", "of",
}
ROMAN = re.compile(r"^(i|ii|iii|iv|v|vi|vii|viii|ix|x|xi|xii)$")
LAT_KIND_WORDS = {"st", "str", "street", "ave", "av", "avenue", "lane", "turn", "rise", "ascent", "descent",
                  "dead", "deadend", "square", "sq", "emb", "embankment", "highway", "road", "alley", "exit",
                  "passage", "row"}
ORD_KIND_WORDS = {"lane", "turn", "dead", "deadend", "exit", "passage", "alley", "rise", "ascent", "descent"}
LAT_KIND = {"st": "street", "str": "street", "street": "street", "ave": "avenue", "av": "avenue",
            "avenue": "avenue", "lane": "lane", "turn": "lane", "dead": "dead_end", "deadend": "dead_end",
            "rise": "ascent", "ascent": "ascent", "descent": "descent", "square": "square", "sq": "square",
            "emb": "embankment", "embankment": "embankment", "highway": "highway", "park": "park",
            "garden": "garden", "bridge": "bridge", "row": "row", "exit": "passage", "passage": "passage"}


def lat_kind(s: str) -> str | None:
    for w in reversed(re.findall(r"[a-z]+", s.lower())):
        if w in LAT_KIND:
            return LAT_KIND[w]
    return None
RU_TYPE = re.compile(
    r"(?i)\b(ул|улица|пр|просп|проспект|пер|переулок|туп|тупик|пл|площадь|наб|набережная|"
    r"спуск|подъ[её]м|шоссе|аллея|сквер|парк|сад|мост|проезд|ряд|ряды|д|дом|угол|перекр[её]сток|"
    r"и|й|го|ого|ий|ая|ой)\b\.?")
KIND_OF_HIGHWAY_WORD = {
    "ქუჩა": "street", "გამზირი": "avenue", "გზატკეცილი": "highway", "გზა": "road", "სანაპირო": "embankment",
    "აღმართი": "ascent", "დაღმართი": "descent", "ხეივანი": "alley", "რიგი": "row", "შესახვევი": "lane",
    "ჩიხი": "dead_end", "გასასვლელი": "passage", "მოედანი": "square", "ხიდი": "bridge", "გვირაბი": "tunnel",
    "ხევი": "ravine", "სკვერი": "garden", "ბაღი": "garden", "პარკი": "park",
}

log = logging.getLogger("acceptance.build_addresses")


def setup_logging() -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%d/%m/%Y %H:%M:%S")
    fh = logging.FileHandler(LOGS / "acceptance.log", encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(fh)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in root.handlers):
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(fmt)
        ch.setLevel(logging.INFO)
        root.addHandler(ch)


def seg_dist(p, a, b) -> float:
    kx = math.cos(math.radians(p[0])) * 111320
    ky = 110540
    px, py, ax, ay, bx, by = p[1] * kx, p[0] * ky, a[1] * kx, a[0] * ky, b[1] * kx, b[0] * ky
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def load_osm() -> list[dict]:
    feats: dict[str, dict] = {}
    for fname in ("highways/highways.json", "misc/misc.json"):
        p = OVERPASS / fname
        if not p.exists():
            log.warning("missing %s", p)
            continue
        for e in json.loads(p.read_text(encoding="utf-8"))["elements"]:
            t = e.get("tags", {})
            ka = t.get("name")
            if not ka:
                continue
            pts = [(g["lat"], g["lon"]) for g in e.get("geometry", []) if isinstance(g, dict)]
            if not pts and "lat" in e:
                pts = [(e["lat"], e["lon"])]
            if not pts and "bounds" in e:
                b = e["bounds"]
                pts = [((b["minlat"] + b["maxlat"]) / 2, (b["minlon"] + b["maxlon"]) / 2)]
            if not pts:
                continue
            f = feats.setdefault(ka, {"ka": ka, "ru": set(), "en": set(), "parts": []})
            for k, v in t.items():
                if k.startswith("name:ru") or k in ("alt_name:ru", "old_name:ru"):
                    f["ru"].add(v)
                elif k.startswith("name:en") or k in ("alt_name:en", "int_name", "old_name:en"):
                    f["en"].add(v)
            f["parts"].append(pts)
    return list(feats.values())


def dist_to(f: dict, p) -> float:
    best = 1e9
    for pts in f["parts"]:
        if len(pts) == 1:
            best = min(best, seg_dist(p, pts[0], pts[0]))
        for i in range(len(pts) - 1):
            best = min(best, seg_dist(p, pts[i], pts[i + 1]))
    return best


def lat_base(s: str) -> list[str]:
    """Latin name words without type words and lane ordinals ("Vakhtang VI st." keeps "vi")."""
    ws = re.findall(r"[A-Za-z0-9']+", s.lower())
    out = []
    for i, w in enumerate(ws):
        if ROMAN.match(w):
            nxt = ws[i + 1] if i + 1 < len(ws) else ""
            if nxt in ORD_KIND_WORDS or nxt == "":
                if nxt == "" and i > 0 and ws[i - 1] not in LAT_KIND_WORDS:
                    out.append(w)  # "Erekle II" at the end is a regnal number
                continue
            out.append(w)
            continue
        if w in LAT_TYPE or re.fullmatch(r"[a-z]", w) or w.isdigit():
            continue
        out.append(w)
    return out


def latin_groups(h: str):
    """Balanced (...) groups whose content starts with a Latin letter: [(start, end, inner)]."""
    res, depth, start = [], 0, None
    for i, ch in enumerate(h):
        if ch == "(":
            if depth == 0:
                start = i
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                inner = h[start + 1:i]
                if re.match(r"\s*[A-Za-z]", inner):
                    res.append((start, i + 1, inner))
                start = None
    return res


def ru_base(s: str) -> str:
    s = re.sub(r"\d+-?[а-я]{0,3}\b", " ", s)
    return RU_TYPE.sub(" ", s)


def kind_of(ka: str) -> str:
    last = ka.split()[-1] if ka.split() else ""
    return KIND_OF_HIGHWAY_WORD.get(last, "other")


def parse_header(h: str) -> list[dict]:
    """Split a CP header into (ru, latin) name pairs and a house number."""
    h = re.sub(r"\s+", " ", h).strip()
    house = None
    m = re.search(r"д\.\s*([0-9]+[а-яa-zა-ჰ]?(?:[/\-][0-9]+[а-яa-z]?)?)", h)
    if m:
        house = m.group(1)
    pairs = []
    pos = 0
    for g0, g1, inner in latin_groups(h):
        ru = h[pos:g0]
        ru = re.split(r"(?i)\bи\b|,|;|\.\s", ru)[-1] if re.search(r"(?i)\bи\b", ru) else ru
        latin = re.sub(r",?\s*\d+[\-/]?\d*\s*$", "", re.sub(r"[()]", " ", inner)).strip(" ,.")
        pairs.append({"ru": ru.strip(" ,.:"), "latin": latin})
        pos = g1
    return [dict(p, house=house if i == 0 else None) for i, p in enumerate(pairs)]


def main() -> int:
    setup_logging()
    if not EXTRACTED.exists():
        log.error("missing %s: run fetch_runcity.py and extract_runcity.py first", EXTRACTED)
        return 2
    ex = json.loads(EXTRACTED.read_text(encoding="utf-8"))
    osm = load_osm()
    log.info("OSM named features: %d", len(osm))
    items, seen, unresolved = [], set(), 0
    per_kp: dict[str, int] = {}
    for ev, recs in ex.items():
        for r in recs:
            if r["is_riddle"] or r["lat"] is None:
                continue
            p = (r["lat"], r["lon"])
            for pair in parse_header(r["header"]):
                lb = lat_base(pair["latin"])
                if not lb:
                    continue
                lsk = skeleton("".join(lb))
                rsk = skeleton(ru_base(pair["ru"]))
                best = None
                for f in osm:
                    d = dist_to(f, p)
                    if d > MAX_DIST_M:
                        continue
                    cand = [skeleton(" ".join(lat_base(e))) for e in f["en"]] + [skeleton(f["ka"])]
                    cand_ru = [skeleton(ru_base(x)) for x in f["ru"]]
                    s1 = max((difflib.SequenceMatcher(None, lsk, c).ratio() for c in cand if c), default=0)
                    s2 = max((difflib.SequenceMatcher(None, rsk, c).ratio() for c in cand_ru if c), default=0) if rsk else 0
                    score = max(s1, s2) - d / 3000
                    lk = lat_kind(pair["latin"])
                    if lk:
                        score += 0.08 if lk == kind_of(f["ka"]) else -0.05
                    if best is None or score > best[0]:
                        best = (score, d, f)
                if best is None or best[0] < MIN_SCORE:
                    unresolved += 1
                    log.debug("unresolved %s-%s: %s", ev, r["kp"], r["header"][:100])
                    continue
                score, d, f = best
                key = (lsk, f["ka"])
                if key in seen:
                    continue
                seen.add(key)
                q_lat = " ".join(lb)
                queries = [{"q": q_lat, "mode": "latin", "expects": "answer street in results", "max_results_ok": 10}]
                hq = re.match(r"\d+(?:/\d+|[а-яa-zა-ჰ](?![\-/]))?", pair["house"] or "")
                if hq:
                    queries.append({"q": f"{q_lat} дом:{hq.group(0)}", "mode": "latin",
                                    "expects": "answer street in results", "max_results_ok": 10})
                ru_name = sorted(f["ru"], key=len)[0] if f["ru"] else None
                base_id = f"{ev}-{r['kp']}-addr"
                per_kp[base_id] = per_kp.get(base_id, 0) + 1
                items.append({
                    "id": base_id if per_kp[base_id] == 1 else f"{base_id}{per_kp[base_id]}",
                    "event": ev, "kp": r["kp"], "kp_point": [r["lat"], r["lon"]],
                    "raw_address": re.sub(r"\s+", " ", r["header"]).strip()[:160],
                    "latin": pair["latin"],
                    "answer": {"street_ka": f["ka"], "street_ru": ru_name, "alt_ka": [], "kind": kind_of(f["ka"]),
                               "house": pair["house"]},
                    "match": {"distance_m": round(d), "score": round(score, 2)},
                    "queries": queries,
                })
    doc = {
        "v": 1,
        "about": ("Латинские написания адресов с карточек КП прошлых игр в Тбилиси; ответ — улица OSM, "
                  "найденная по сходству имени и расстоянию до точки КП. Проверяет поиск по латинице."),
        "built": __import__("datetime").date.today().strftime("%d/%m/%Y"),
        "items": items,
    }
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log.info("wrote %s: %d items, %d unresolved headers", OUT.relative_to(ROOT), len(items), unresolved)
    return 0


if __name__ == "__main__":
    sys.exit(main())
