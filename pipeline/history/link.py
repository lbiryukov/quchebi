"""Stage link: history articles -> features, then (re)build ``name_history``.

Matching cascade for an article title (Georgian):

1. ``exact``  -- same (family key, kind, ordinal) as a feature's ``ka`` / alt / official name
   (``pipeline.names.parse.parse_name`` on both sides, so canonicalisation is not needed);
2. ``wiki``   -- (enc2008 only) a ka.wikipedia article that lists the title among its former names
   or has the same name; the street was renamed after 2008, the title becomes a former name;
3. ``old``    -- the title equals an OSM ``old_name`` of a feature (renamed after 2008);
4. ``loose``  -- same surname token, kind and ordinal, accepted only with district/place evidence.

Several candidates are filtered by district (2008 districts are mapped to today's) and by the
historical place in parentheses; without evidence an ambiguous title stays unlinked. ru.wikipedia
articles link through the Wikidata id of the ka article, else by the Russian name.

``name_history`` rows are then written per linked feature from the facts of all sources (LLM
results from ``llm_result`` replace the parser output of the same article), plus OSM
``old_name`` and address evidence; equal names of one feature are merged into one row.
"""
from __future__ import annotations

import collections
import json
import re
import sqlite3
from dataclasses import dataclass, field

from pipeline.db import tx
from pipeline.history import enc_parse as P
from pipeline.history import georgian as G
from pipeline.history.names_adapter import ka_to_lat, ka_to_ru, loose_key, name_key, normalize_ru_name, skeleton
from pipeline.history.store import HISTORY_SOURCES, NAME_SOURCES, load_articles, table_exists
from pipeline.log import get_logger, stage

log = get_logger(__name__)

KIND_GROUPS = {**{k: "street" for k in ("street", "avenue", "highway", "road", "embankment", "ascent", "descent",
                                       "alley", "row")},
               **{k: "lane" for k in ("lane", "dead_end", "passage")}, "square": "square", "garden": "garden",
               "park": "garden"}
SOURCE_PRIORITY = {"enc2008_llm": 0, "enc2008": 1, "kawiki": 2, "ruwiki": 3, "osm_old_name": 4, "addr": 5}
RU_TYPES = re.compile(r"\b(улица|ул\.|переулок|пер\.|проспект|пр\.|площадь|пл\.|тупик|проезд|спуск|подъём|подъем|"
                      r"набережная|шоссе|аллея|мост|сквер|парк|дорога|ряд|балка|тоннель)\b", re.I)


# ------------------------------------------------------------------------------ feature index

@dataclass
class Feat:
    id: int
    kind: str
    ka: str
    ru: str | None
    ord: int | None
    family_id: int | None
    lat: float | None
    lon: float | None
    bbox: tuple[float, float, float, float] | None = None
    areas: list[str] = field(default_factory=list)   # skeletons of area names (ka/ru/en)


class FeatureIndex:
    def __init__(self, con: sqlite3.Connection):
        self.feats: dict[int, Feat] = {}
        self.exact: dict[tuple, set[int]] = collections.defaultdict(set)
        self.old: dict[tuple, set[int]] = collections.defaultdict(set)
        self.loose: dict[tuple, set[int]] = collections.defaultdict(set)
        self.nokind: dict[tuple, set[int]] = collections.defaultdict(set)   # (family key, ord)
        self.ru: dict[str, set[int]] = collections.defaultdict(set)
        self.osm_old: dict[int, list[tuple[str, str]]] = collections.defaultdict(list)
        for r in con.execute("SELECT id, kind, ka, ru, ord, family_id, lat, lon, bbox FROM feature"):
            f = Feat(r["id"], r["kind"], r["ka"], r["ru"], r["ord"], r["family_id"], r["lat"], r["lon"],
                     _parse_bbox(r["bbox"]))
            self.feats[f.id] = f
            self._add_ka(f.id, f.ka, self.exact, loose=True)
            if f.ru:
                self.ru[_ru_key(f.ru)].add(f.id)
        if table_exists(con, "feature_name"):
            for r in con.execute("SELECT feature_id, lang, name, kind, source FROM feature_name"):
                fid, lang, nm, kind = r["feature_id"], r["lang"], r["name"], r["kind"]
                if fid not in self.feats:
                    continue
                if kind == "old":
                    self.osm_old[fid].append((lang, nm))
                    if lang == "ka":
                        self._add_ka(fid, nm, self.old, loose=False)
                elif kind in ("alt", "official"):
                    if lang == "ka":
                        self._add_ka(fid, nm, self.exact, loose=False)
                    elif lang == "ru":
                        self.ru[_ru_key(nm)].add(fid)
        if table_exists(con, "feature_area") and table_exists(con, "area"):
            for r in con.execute("SELECT fa.feature_id, a.ka, a.ru, a.en FROM feature_area fa "
                                 "JOIN area a ON a.id = fa.area_id"):
                f = self.feats.get(r[0])
                if f is not None:
                    f.areas.extend(skeleton(x) for x in (r[1], r[2], r[3]) if x)
        # an area feature (district/neighbourhood) is "in" its own area
        for f in self.feats.values():
            if f.kind in ("district", "neighbourhood"):
                f.areas.append(skeleton(f.ka))
        log.info("link: feature index %d features, %d exact keys, %d old keys, %d loose keys, %d ru keys",
                 len(self.feats), len(self.exact), len(self.old), len(self.loose), len(self.ru))

    def _add_ka(self, fid: int, name: str, table: dict, loose: bool) -> None:
        for v in _ka_variants(name):
            k = name_key(v)
            table[k].add(fid)
            if loose:
                self.nokind[(k[0], k[2])].add(fid)
                lk = loose_key(v)
                if lk[0]:
                    self.loose[lk].add(fid)


def _parse_bbox(s: str | None) -> tuple[float, float, float, float] | None:
    try:
        a = [float(x) for x in (s or "").split(",")]
        return (a[0], a[1], a[2], a[3]) if len(a) == 4 else None
    except ValueError:
        return None


def _ka_variants(name: str) -> list[str]:
    out = [name]
    s = re.sub(r"\s*\([^)]*\)", "", name).strip()
    if s and s != name:
        out.append(s)
    out.extend(v for v in P.lane_variants(s) if v not in out)
    return out


def _ru_key(ru: str) -> str:
    s = normalize_ru_name(re.sub(r"\s*\([^)]*\)", "", ru or ""))
    s = RU_TYPES.sub(" ", s.lower())
    return "".join(sorted(skeleton(w) for w in s.split() if skeleton(w)))


def _hint_stems(facts: dict) -> tuple[list[str], list[str]]:
    """(district stems, place stems) as skeletons."""
    dist: list[str] = []
    d = facts.get("district_2008")
    if d:
        for k, stems in G.DISTRICTS_2008.items():
            if d.startswith(k) or k in d:
                dist.extend(stems)
        if not dist:
            dist.extend(re.sub(r"(ის|ისა)$", "", w) for w in re.split(r"[\s\-]+", d) if len(w) > 3)
    for key in ("district",):
        v = facts.get(key)
        if v:
            dist.extend(re.sub(r"(ის|ისა)$", "", w) for w in re.split(r"[\s\-]+", v)
                        if len(w) > 3 and w not in ("რაიონი", "რაიონის"))
    places: list[str] = []
    for key in ("place", "quarter", "historic_district", "microdistrict"):
        v = facts.get(key)
        if not v:
            continue
        for p in re.split(r"[,;]|\sდა\s", v):
            p = p.strip()
            if p and len(p) > 2:
                places.append(re.sub(r"(ის|ისა)$", "", p.split()[-1]) if len(p.split()) > 2 else p)
    return [skeleton(x)[:6] for x in dist if skeleton(x)], [skeleton(x)[:6] for x in places if skeleton(x)]


def _score(f: Feat, dist: list[str], places: list[str]) -> int:
    s = 0
    if places and any(p and any(p in a for a in f.areas) for p in places):
        s += 2
    if dist and any(d and any(d in a for a in f.areas) for d in dist):
        s += 1
    return s


def choose(index: FeatureIndex, fids: set[int], facts: dict) -> tuple[list[int], float, str]:
    """Pick features among candidates using district/place evidence."""
    if not fids:
        return [], 0.0, "none"
    dist, places = _hint_stems(facts)
    cands = [index.feats[i] for i in fids if i in index.feats]
    if len(cands) == 1:
        sc = _score(cands[0], dist, places)
        return [cands[0].id], (0.95 if sc > 0 else 0.85), "unique"
    scored = sorted(((_score(f, dist, places), f.id) for f in cands), reverse=True)
    best = scored[0][0]
    if best > 0:
        chosen = [i for s, i in scored if s == best]
        return chosen, (0.85 if len(chosen) == 1 else 0.7), "district"
    return [], 0.0, "ambiguous"


def _choose_strict(index: FeatureIndex, fids: set[int], facts: dict) -> tuple[list[int], float, str]:
    """Like :func:`choose`, but with district/place hints a feature with area data must agree."""
    chosen, conf, how = choose(index, fids, facts)
    if not chosen:
        return chosen, conf, how
    dist, places = _hint_stems(facts)
    if dist or places:
        ok = [i for i in chosen if not index.feats[i].areas or _score(index.feats[i], dist, places) > 0]
        if not ok:
            return [], 0.0, "district_mismatch"
        chosen = ok
    return chosen, conf, how


# ------------------------------------------------------------------------------ linking

@dataclass
class Link:
    article_id: int
    source: str
    feature_ids: list[int]
    conf: float
    how: str
    renamed: bool = False       # the article title is itself a former name of the feature


def link_articles(con: sqlite3.Connection, index: FeatureIndex, arts: list[dict]) -> dict[int, Link]:
    links: dict[int, Link] = {}
    ka_arts = [a for a in arts if a["source"] == "kawiki"]
    enc_arts = [a for a in arts if a["source"] == "enc2008"]
    ru_arts = [a for a in arts if a["source"] == "ruwiki"]
    qid_to_ka: dict[str, int] = {}
    # ---- ka.wikipedia
    for a in ka_arts:
        facts = a["facts"]
        title = re.sub(r"\s*\([^)]*\)$", "", a["title"]).strip()
        names = [title] + ([facts["name"]] if facts.get("name") and facts["name"] != title else [])
        lk = _link_title(index, names, facts)
        if lk:
            links[a["id"]] = Link(a["id"], "kawiki", *lk)
        if facts.get("qid"):
            qid_to_ka[facts["qid"]] = a["id"]
    # former-name index of linked ka articles (for enc2008 titles renamed after 2008)
    ka_former: dict[tuple, list[int]] = collections.defaultdict(list)
    for a in ka_arts:
        if a["id"] not in links:
            continue
        for f in a["facts"].get("former", []):
            if f.get("name_ka"):
                ka_former[name_key(f["name_ka"])].append(a["id"])
    # ---- encyclopedia
    still_exists: set[int] = set()      # the 2008 name is still a current name somewhere
    for a in enc_arts:
        facts = a["facts"]
        title = a["title"]
        lk = _link_title(index, [title], facts)
        if lk:
            links[a["id"]] = Link(a["id"], "enc2008", *lk)
            continue
        if any(index.exact.get(name_key(v)) for v in _ka_variants(title)):
            still_exists.add(a["id"])   # ambiguous homonyms: do not treat as renamed
            continue
        # the type word changed after 2008 ("მელიქიშვილის ქუჩა" -> "მელიქიშვილის გამზირი")
        k = name_key(title)
        grp = KIND_GROUPS.get(k[1])
        fids = {i for i in index.nokind.get((k[0], k[2]), set())
                if grp and KIND_GROUPS.get(index.feats[i].kind) == grp and index.feats[i].kind != k[1]}
        if k[0] and fids:
            chosen, conf, how = _choose_strict(index, fids, facts)
            if chosen and len(chosen) == 1:
                links[a["id"]] = Link(a["id"], "enc2008", chosen, min(conf, 0.7), "kind", renamed=True)
                continue
        # renamed after 2008: ka.wikipedia lists the 2008 name among former names
        cand_ka = ka_former.get(name_key(title), [])
        fids: set[int] = set()
        for kid in cand_ka:
            fids.update(links[kid].feature_ids)
        if fids:
            chosen, conf, how = _choose_strict(index, fids, facts)
            if chosen:
                links[a["id"]] = Link(a["id"], "enc2008", chosen, min(conf, 0.8), "wiki", renamed=True)
                continue
        fids = set()
        for v in _ka_variants(title):
            fids |= index.old.get(name_key(v), set())
        if fids:
            chosen, conf, how = _choose_strict(index, fids, facts)
            if chosen:
                links[a["id"]] = Link(a["id"], "enc2008", chosen, min(conf, 0.8), "old", renamed=True)
                continue
    # renamed after 2008 and known only from addresses: houses still use the 2008 name
    pending = [a for a in enc_arts if a["id"] not in links and a["id"] not in still_exists
               and a["facts"].get("kind")]
    if pending:
        by_key = _addr_points(con)
        for a in pending:
            pts = by_key.get(name_key(a["title"]))
            if not pts:
                continue
            fid = _feature_for_points(index, pts, a["facts"].get("kind"))
            if fid is not None:
                links[a["id"]] = Link(a["id"], "enc2008", [fid], 0.6, "addr", renamed=True)
    # ---- ru.wikipedia
    for a in ru_arts:
        facts = a["facts"]
        kid = qid_to_ka.get(facts.get("qid") or "")
        if kid is not None and kid in links:
            l = links[kid]
            links[a["id"]] = Link(a["id"], "ruwiki", list(l.feature_ids), l.conf, "qid")
            continue
        fids = set(index.ru.get(_ru_key(a["title"]), set()))
        if facts.get("name"):
            fids |= index.ru.get(_ru_key(facts["name"]), set())
        chosen, conf, how = choose(index, fids, {})
        if chosen:
            links[a["id"]] = Link(a["id"], "ruwiki", chosen, conf, "ru_" + how)
    return links


def _addr_points(con: sqlite3.Connection) -> dict[tuple, list[tuple[float, float]]]:
    """name key of ``addr:street`` -> house points (only for houses not matched to that name)."""
    out: dict[tuple, list[tuple[float, float]]] = collections.defaultdict(list)
    if not table_exists(con, "house"):
        return out
    for h in con.execute("SELECT addr_street, lat, lon FROM house WHERE addr_street IS NOT NULL AND lat IS NOT NULL"):
        if re.search(r"[ა-ჰ]", h[0] or ""):
            out[name_key(h[0])].append((h[1], h[2]))
    return out


def _feature_for_points(index: FeatureIndex, pts: list[tuple[float, float]], kind: str | None,
                        pad: float = 0.0008) -> int | None:
    """The street-like feature whose bbox (padded ~80 m) contains most of the house points."""
    if len(pts) < 2:
        return None
    best: tuple[int, float, int] | None = None
    for f in index.feats.values():
        if f.bbox is None or (kind and f.kind != kind):
            continue
        a, b, c, d = f.bbox
        n = sum(1 for (y, x) in pts if a - pad <= y <= c + pad and b - pad <= x <= d + pad)
        if n * 2 < len(pts):
            continue
        area = (c - a + 2 * pad) * (d - b + 2 * pad)
        cand = (n, -area, f.id)
        if best is None or cand > best:
            best = cand
    return best[2] if best else None


def _given_parts(name: str) -> list[str]:
    """Words before the surname (given names, nicknames in parentheses, qualifiers), no initials."""
    from pipeline.history.names_adapter import parse_name
    base = parse_name(name).base_ka or ""
    words = [w.strip("()") for w in base.split()]
    return [w for w in words if w][:-1]


def _given_compatible(a: str, b: str) -> bool:
    """'კონსტანტინე მაყაშვილის' ~ 'კოტე მაყაშვილის', but not 'მერაბ ქავთარაძის' ~ 'მიხეილ ქავთარაძის'
    and not 'ვეძისის ქუჩა' ~ 'ზემო ვეძისის ქუჩა'."""
    ga, gb = _given_parts(a), _given_parts(b)
    if not ga and not gb:
        return True
    if not ga or not gb:
        return False

    def ok(x: str, y: str) -> bool:
        if x.endswith(".") or y.endswith("."):       # initial "ს." vs "სოლომონ"
            return x[0] == y[0]
        return x[:2] == y[:2]
    return any(ok(x, y) for x in ga for y in gb)


def _link_title(index: FeatureIndex, names: list[str], facts: dict) -> tuple[list[int], float, str] | None:
    fids: set[int] = set()
    for n in names:
        for v in _ka_variants(n):
            fids |= index.exact.get(name_key(v), set())
    if fids:
        chosen, conf, how = choose(index, fids, facts)
        if chosen:
            return chosen, conf, "exact" if how == "unique" else "exact_" + how
        return None
    fids = set()
    for n in names:
        lk = loose_key(n)
        if lk[0]:
            fids |= {i for i in index.loose.get(lk, set()) if _given_compatible(n, index.feats[i].ka)}
    if fids:
        dist, places = _hint_stems(facts)
        cands = [(_score(index.feats[i], dist, places), i) for i in fids]
        best = max(s for s, _ in cands)
        chosen = [i for s, i in cands if s == best and s > 0]
        if chosen and len(chosen) <= 2:
            return chosen, 0.65, "loose"
    return None


# ------------------------------------------------------------------------------ name_history rows

def _years_ok(y: int | None) -> int | None:
    return y if isinstance(y, int) and 1600 <= y <= 2030 else None


def rows_for_article(art: dict, link: Link, llm: dict | None) -> list[dict]:
    """name_history candidate rows (without feature id) for one linked article."""
    facts = art["facts"]
    src = art["source"]
    ref = f"{src}:p{art['page']}" if src == "enc2008" else f"{src}:{art['page']}"
    out: list[dict] = []
    former = facts.get("former", [])
    row_src = src
    if llm is not None and src == "enc2008":
        former = llm.get("former_names", [])
        row_src = "enc2008_llm"
        base_conf = float(llm.get("confidence", 0.7))
    else:
        base_conf = 1.0
    for f in former:
        ka = (f.get("name_ka") or "").strip() or None
        ru = (f.get("name_ru") or "").strip() or None
        if not ka and not ru:
            continue
        fy, ty = _years_ok(f.get("from_year")), _years_ok(f.get("to_year"))
        if fy and ty and ty < fy:
            ty = None
        if fy and ty and fy == ty:
            fy = None        # renamed in the same clause: only the end year is reliable
        conf = round(min(link.conf, float(f.get("confidence", 0.8)) * base_conf if llm is None else base_conf), 2)
        out.append({"name_ka": ka, "name_ru": ru, "from_year": fy, "to_year": ty, "source": row_src, "ref": ref,
                    "confidence": conf})
    if link.renamed and src == "enc2008":
        ny = (facts.get("named_year") or {}).get("year")
        out.append({"name_ka": art["title"], "name_ru": None, "from_year": _years_ok(ny), "to_year": None,
                    "source": "enc2008", "ref": ref + ":title", "confidence": round(link.conf, 2)})
    return out


def _initials(s: str) -> bool:
    return bool(re.search(r"(?:^|\s)[ა-ჰ]\.", s or ""))


def _ru_matches_ka(ru: str, ka: str) -> bool:
    rt = [skeleton(w) for w in RU_TYPES.sub(" ", ru.lower()).split()]
    rt = [w for w in rt if len(w) >= 3 and not w.isdigit()]
    kt = [re.sub(r"(is|s)$", "", skeleton(w)) for w in ka.split() if not G.kind_of(w) and not G.INITIAL_RE.match(w)]
    kt = [w for w in kt if len(w) >= 3]
    for r in rt:
        for k in kt:
            n = 0
            while n < min(len(r), len(k)) and r[n] == k[n]:
                n += 1
            if n >= 5 or (n >= 3 and n == min(len(r), len(k))):
                return True
    return False


def merge_rows(rows: list[dict]) -> list[dict]:
    """Merge rows naming the same former name of one feature."""
    rows = sorted(rows, key=lambda r: (SOURCE_PRIORITY.get(r["source"], 9), -(bool(r.get("from_year")) +
                                                                          bool(r.get("to_year")))))
    groups: list[dict] = []
    for r in rows:
        g = None
        for cand in groups:
            if r.get("name_ka") and cand.get("name_ka") and P._same_name(cand["name_ka"], r["name_ka"]):
                g = cand
                break
            if not r.get("name_ka") and r.get("name_ru") and cand.get("name_ka") and not cand.get("name_ru") \
                    and _ru_matches_ka(r["name_ru"], cand["name_ka"]):
                g = cand
                break
            if r.get("name_ru") and cand.get("name_ru") and _ru_key(r["name_ru"]) == _ru_key(cand["name_ru"]) \
                    and not (r.get("name_ka") and cand.get("name_ka")):
                g = cand
                break
            if r.get("name_lat") and cand.get("name_lat") and skeleton(r["name_lat"]) == skeleton(cand["name_lat"]):
                g = cand
                break
        if g is None:
            groups.append(dict(r))
            continue
        # same former name: keep the fuller spelling and fill gaps
        if r.get("name_ka") and (not g.get("name_ka") or (_initials(g["name_ka"]) and not _initials(r["name_ka"]))):
            g["name_ka"] = r["name_ka"]
        for k in ("name_ru", "name_lat", "from_year", "to_year"):
            if not g.get(k) and r.get(k):
                g[k] = r[k]
        if r["ref"] not in g["ref"].split(";"):
            g["ref"] = g["ref"] + ";" + r["ref"]
        g["confidence"] = max(g["confidence"], r["confidence"])
    for g in groups:
        if g.get("name_ka") and not g.get("name_lat"):
            g["name_lat"] = _display_name(g["name_ka"], "en") or ka_to_lat(g["name_ka"])
        if g.get("name_ka") and not g.get("name_ru"):
            # Readable Russian form ("переулок Плеханови"), not a letter-by-letter "плехановис шесахвеви".
            g["name_ru"] = _display_name(g["name_ka"], "ru") or ka_to_ru(g["name_ka"])
        if g.get("from_year") and g.get("to_year") and g["to_year"] < g["from_year"]:
            g["to_year"] = None
    return groups


def _display_name(name_ka: str, lang: str) -> str | None:
    """Russian/English display form of a Georgian street name via pipeline.names (None if unavailable)."""
    try:
        from pipeline.names.parse import make_en_name, make_ru_name, parse_name as _parse
        p = _parse(name_ka)
        return (make_ru_name(p) if lang == "ru" else make_en_name(p)) or None
    except Exception:  # noqa: BLE001 - display nicety only, fall back to transliteration
        return None


def osm_old_rows(index: FeatureIndex, fid: int) -> list[dict]:
    out = []
    for lang, nm in index.osm_old.get(fid, []):
        d = {"name_ka": None, "name_ru": None, "name_lat": None, "from_year": None, "to_year": None,
             "source": "osm_old_name", "ref": "osm", "confidence": 0.9}
        if lang == "ka":
            d["name_ka"] = nm
        elif lang == "ru":
            d["name_ru"] = nm
        elif lang in ("lat", "en"):
            d["name_lat"] = nm
        else:
            continue
        out.append(d)
    return out


def addr_rows(con: sqlite3.Connection, index: FeatureIndex, per_feature: dict[int, list[dict]]) -> dict[int, list[dict]]:
    """Old names used in ``addr:street``: house rows whose street is not the current name but an old one."""
    out: dict[int, list[dict]] = collections.defaultdict(list)
    if not table_exists(con, "house"):
        return out
    old_keys: dict[tuple, set[int]] = collections.defaultdict(set)
    for fid, rows in per_feature.items():
        for r in rows:
            if r.get("name_ka"):
                for v in _ka_variants(r["name_ka"]):
                    old_keys[name_key(v)].add(fid)
    for k, fids in index.old.items():
        old_keys[k] |= fids
    counts: collections.Counter = collections.Counter()
    for h in con.execute("SELECT feature_id, addr_street, match, lat, lon FROM house WHERE addr_street IS NOT NULL"):
        street = h["addr_street"]
        if not re.search(r"[ა-ჰ]", street or ""):
            continue
        k = name_key(street)
        fids = old_keys.get(k)
        if not fids:
            continue
        cur = index.feats.get(h["feature_id"]) if h["feature_id"] else None
        if cur is not None and name_key(cur.ka) == k:
            continue
        if cur is not None and cur.id in fids:
            target = cur.id
        elif cur is None and len(fids) == 1:
            target = next(iter(fids))
        elif cur is None and h["lat"] is not None:
            near = [(abs((index.feats[f].lat or 0) - h["lat"]) + abs((index.feats[f].lon or 0) - h["lon"]), f)
                    for f in fids if index.feats.get(f) and index.feats[f].lat is not None]
            near.sort()
            if not near or near[0][0] > 0.01:
                continue
            target = near[0][1]
        else:
            continue
        counts[(target, street)] += 1
    for (fid, street), n in counts.items():
        out[fid].append({"name_ka": street, "name_ru": None, "name_lat": None, "from_year": None, "to_year": None,
                         "source": "addr", "ref": f"addr:{n}", "confidence": 0.7})
    return out


def load_llm_overrides(con: sqlite3.Connection) -> dict[str, dict]:
    """article key (enc2008:<page>:<text_hash>) -> validated LLM payload."""
    out: dict[str, dict] = {}
    if not table_exists(con, "llm_result"):
        return out
    for r in con.execute("SELECT family_key, payload FROM llm_result WHERE role='history' ORDER BY created_at"):
        try:
            out[r[0]] = json.loads(r[1])
        except ValueError:
            continue
    return out


def article_key(art: dict) -> str:
    return f"{art['source']}:{art['page']}:{art.get('text_hash') or ''}:{art['title']}"


def write_name_history(con: sqlite3.Connection, index: FeatureIndex, arts: list[dict], links: dict[int, Link]) -> dict:
    llm = load_llm_overrides(con)
    per_feature: dict[int, list[dict]] = collections.defaultdict(list)
    unlinked: list[dict] = []
    n_llm = 0
    for a in arts:
        if a["source"] not in ("enc2008", "kawiki", "ruwiki"):
            continue
        ov = llm.get(article_key(a))
        if ov is not None:
            n_llm += 1
        lk = links.get(a["id"])
        if lk is None:
            for r in rows_for_article(a, Link(a["id"], a["source"], [], 0.5, "none"), ov):
                unlinked.append(r)
            continue
        rows = rows_for_article(a, lk, ov)
        for fid in lk.feature_ids:
            per_feature[fid].extend(dict(r) for r in rows)
    for fid in list(index.osm_old):
        per_feature[fid].extend(osm_old_rows(index, fid))
    for fid, rows in addr_rows(con, index, per_feature).items():
        per_feature[fid].extend(rows)
    out_rows = []
    for fid, rows in per_feature.items():
        f = index.feats.get(fid)
        cur_ka = f.ka if f else None
        for g in merge_rows(rows):
            if cur_ka and g.get("name_ka") and name_key(g["name_ka"]) == name_key(cur_ka) \
                    and not g.get("from_year") and not g.get("to_year"):
                continue      # the current name itself, no period known
            out_rows.append((fid, f.family_id if f else None, g.get("name_ka"), g.get("name_ru"), g.get("name_lat"),
                             g.get("from_year"), g.get("to_year"), g["source"], g["ref"][:500], g["confidence"]))
    for g in merge_rows(unlinked):
        out_rows.append((None, None, g.get("name_ka"), g.get("name_ru"), g.get("name_lat"), g.get("from_year"),
                         g.get("to_year"), g["source"], g["ref"][:500], round(min(g["confidence"], 0.5), 2)))
    with tx(con):
        q = ",".join("?" * len(NAME_SOURCES))
        con.execute(f"DELETE FROM name_history WHERE source IN ({q})", NAME_SOURCES)
        con.executemany("INSERT INTO name_history(feature_id, family_id, name_ka, name_ru, name_lat, from_year, "
                        "to_year, source, ref, confidence) VALUES (?,?,?,?,?,?,?,?,?,?)", out_rows)
        for aid, lk in links.items():
            fid = lk.feature_ids[0]
            f = index.feats.get(fid)
            con.execute("UPDATE history_article SET feature_id=?, family_id=? WHERE id=?",
                        (fid, f.family_id if f else None, aid))
    by_src = collections.Counter(r[7] for r in out_rows)
    stats = {"name_history": len(out_rows), "linked_rows": sum(1 for r in out_rows if r[0] is not None),
             "features_with_history": len({r[0] for r in out_rows if r[0] is not None}),
             "with_years": sum(1 for r in out_rows if r[5] or r[6]), "llm_articles": n_llm,
             **{"src_" + k: v for k, v in by_src.items()}}
    return stats


def run(con: sqlite3.Connection) -> dict:
    with stage(log, "history.link") as st:
        if not table_exists(con, "feature") or con.execute("SELECT COUNT(*) FROM feature").fetchone()[0] == 0:
            log.warning("link: feature table is empty; run the osm stages first")
            st["skipped"] = "no features"
            return st
        index = FeatureIndex(con)
        arts = load_articles(con, HISTORY_SOURCES)
        with tx(con):
            con.execute("UPDATE history_article SET feature_id=NULL, family_id=NULL")
        links = link_articles(con, index, arts)
        hows = collections.Counter(f"{l.source}:{l.how}" for l in links.values())
        for src in ("enc2008", "kawiki", "ruwiki"):
            n = sum(1 for a in arts if a["source"] == src)
            k = sum(1 for l in links.values() if l.source == src)
            st[f"{src}_articles"] = n
            st[f"{src}_linked"] = k
        st["how"] = dict(hows)
        st.update(write_name_history(con, index, arts, links))
        st.update(coverage(con, index, arts, links))
        log.info("link: %s", st)
        return st


def coverage(con: sqlite3.Connection, index: FeatureIndex, arts: list[dict], links: dict[int, Link]) -> dict:
    """Share of named street-like features that got an encyclopedia article."""
    enc_feats = set()
    for l in links.values():
        if l.source == "enc2008":
            enc_feats.update(l.feature_ids)
    street_kinds = {"street", "avenue", "highway", "road", "embankment", "ascent", "descent", "alley", "row",
                    "square", "lane", "dead_end", "passage"}
    main = [f for f in index.feats.values() if f.kind in street_kinds]
    main_streets = [f for f in main if f.kind not in ("lane", "dead_end", "passage")]
    # "encyclopedic status": features whose name (key) has an encyclopedia article anywhere in the book
    enc_keys = set()
    for a in arts:
        if a["source"] == "enc2008":
            enc_keys.add(name_key(a["title"]))
    with_status = [f for f in main if name_key(f.ka) in enc_keys or f.id in enc_feats]
    return {
        "cov_streetlike": len(main),
        "cov_streetlike_with_enc": sum(1 for f in main if f.id in enc_feats),
        "cov_main_streets": len(main_streets),
        "cov_main_streets_with_enc": sum(1 for f in main_streets if f.id in enc_feats),
        "cov_enc_status": len(with_status),
        "cov_enc_status_linked_pct": round(100.0 * sum(1 for f in with_status if f.id in enc_feats)
                                           / max(1, len(with_status)), 1),
    }
