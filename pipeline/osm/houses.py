"""Houses stage: every ``addr:housenumber`` object in Tbilisi -> ``house`` rows matched to streets.

House numbers: :func:`pipeline.names.parse_house` -> one row per part; an unusable value gives one
row with ``kind = 'junk'``, ``n = NULL`` and the raw text. Empty ``lit`` / ``sub`` / ``corp`` are NULL.

Street matching cascade for ``addr:street`` (then ``addr:street:ka|en|ru``), ``house.match``:

1. ``exact``        -- equals a feature's ``ka`` (nearest of homonyms);
2. ``variant``      -- equals any other name (ru / en / alt / old / official / name:*);
3. ``normalized``   -- equal after :func:`normalize_ka` + :func:`skeleton` (case, Mtavruli, typos,
   script);
4. ``no_type``      -- equal base without the type word (order-insensitive stemmed tokens, same
   ordinal, compatible type) within :data:`NO_TYPE_MAX_M`;
5. ``fuzzy``        -- a feature within :data:`FUZZY_M` with the same ordinal and type and base
   similarity >= :data:`FUZZY_RATIO`;
6. ``homonym_swap`` -- a match farther than :data:`SWAP_TRIGGER_M` is replaced by a closer similar
   street within :data:`SWAP_M` when there is one (far homonym, or the old name of a nearby street);
7. ``place``        -- no street (or an unmatched street that is really a neighbourhood name): the
   ``addr:place`` / ``addr:street`` value matched to an area (``house.area_id``).

Matches farther than :data:`FAR_REJECT_M` are dropped (flag ``far_rejected:<m>``); matches farther
than :data:`FAR_FLAG_M` keep the flag ``far:<m>``. Unmatched rows have ``match = 'none'``. Non-exact street spellings that matched are added to
``feature_name`` as ``kind = 'addr_variant'``, ``source = 'addr'``.
"""
from __future__ import annotations

import difflib
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import shapely
from shapely.geometry import Point

from pipeline.db import tx
from pipeline.log import get_logger, stage
from pipeline.names import KIND_GROUP, normalize_ka, parse_house, parse_name, skeleton, strip_type
from pipeline.osm.features import script_lang
from pipeline.osm.geo import proj, to_xy
from pipeline.osm.model import Extract

log = get_logger(__name__)

NO_TYPE_MAX_M = 1500.0
FUZZY_M = 500.0
SWAP_M = 300.0
SWAP_TRIGGER_M = 300.0    # a match farther than this looks for a similar street within SWAP_M
LONG_TOKEN_RATIO = 0.7
FUZZY_RATIO = 0.8
FAR_FLAG_M = 300.0
FAR_REJECT_M = 1500.0     # a street match farther than this is dropped (match 'none', flag far_rejected)
ALT_STREET_TAGS = ("addr:street:ka", "addr:street:en", "addr:street:ru")
PLACE_FUZZY_M = 3000.0
NO_ADDR_KINDS = ("district", "neighbourhood", "metro", "river", "stream", "canal", "lake")

_ROMAN = r"(?=[IVX])(?:X{0,3})(?:IX|IV|V?I{0,3})"
_ORD_RES = [
    re.compile(r"(?<![\w])(\d{1,3})\s*-?\s*(?:й|ый|ой|ий|я|ая|е|ое|ее|st|nd|rd|th|ლი|ე)(?![\w])", re.IGNORECASE),
    re.compile(r"(?<![\w])მე-?(\d{1,3})(?:-?ე)?(?![\w])"),
    re.compile(rf"(?<![\w])({_ROMAN})(?![\w])"),
]
_SPLIT = re.compile(r"[\s\-‐–—.,;:()\"'«»„“”/]+")
_DROP = {"named", "after", "imeni", "im", "saxelobis", "sakhelobis", "the", "of", "da", "i"}
_ROMAN_VAL = {"I": 1, "V": 5, "X": 10}


def _roman(s: str) -> int | None:
    if not s or not re.fullmatch(_ROMAN, s):
        return None
    total, prev = 0, 0
    for ch in reversed(s):
        v = _ROMAN_VAL[ch]
        total = total - v if v < prev else total + v
        prev = max(prev, v)
    return total or None


def _stem(tok: str) -> str:
    """Crude cross-script stem of a skeleton token: drop genitive -is/-s, then final vowels."""
    if len(tok) > 4 and tok.endswith("is"):
        tok = tok[:-2]
    elif len(tok) > 3 and tok.endswith("s"):
        tok = tok[:-1]
    while len(tok) > 3 and tok[-1] in "aeiou":
        tok = tok[:-1]
    return tok


@dataclass(frozen=True)
class NameKey:
    norm: str                 # skeleton(normalize_ka(full name))
    core: tuple[str, ...]     # sorted stemmed base tokens
    ord: int | None
    kind: str                 # canonical kind ('other' if unknown)

    @property
    def core_str(self) -> str:
        return " ".join(self.core)


@lru_cache(maxsize=1 << 17)
def name_key(s: str) -> NameKey | None:
    """Matching key of a street name in any script."""
    if not s or not s.strip():
        return None
    nk = normalize_ka(s.strip())
    norm = skeleton(nk)
    p = parse_name(nk)
    sc = script_lang(nk) or "lat"
    if sc == "ka":
        base, ordv = p.base_ka or strip_type(nk), p.ord
        if p.regnal:
            base = base  # the regnal number stays part of the base
    else:
        base = strip_type(nk)
        ordv = None
        for rx in _ORD_RES:
            m = rx.search(nk)
            if m:
                g = m.group(1)
                ordv = int(g) if g.isdigit() else _roman(g)
                if ordv is not None:
                    break
        base = re.sub(rf"(?<![\w]){_ROMAN}(?![\w])", " ", base) if ordv else base
    toks = []
    for t in _SPLIT.split(base):
        sk = skeleton(t)
        if not sk or sk in _DROP or sk.isdigit():
            continue
        toks.append(_stem(sk))
    if not toks and not norm:
        return None
    return NameKey(norm=norm, core=tuple(sorted(toks)), ord=ordv, kind=p.kind or "other")


def kinds_compatible(a: str, b: str, strict: bool) -> bool:
    if a == "other" or b == "other" or a == b:
        return True
    if strict:
        return False
    ga, gb = KIND_GROUP.get(a, ""), KIND_GROUP.get(b, "")
    return not ({ga, gb} == {"street*", "lane*"})


class StreetIndex:
    """Name indexes and geometry of candidate features (projected)."""

    def __init__(self, con: sqlite3.Connection):
        rows = con.execute(
            "SELECT id, kind, ka, ru, en, ru_generated, geom_wkt FROM feature WHERE geom_wkt IS NOT NULL "
            f"AND kind NOT IN ({','.join('?' * len(NO_ADDR_KINDS))})", NO_ADDR_KINDS).fetchall()
        self.fids = [r["id"] for r in rows]
        self.pos = {fid: i for i, fid in enumerate(self.fids)}
        self.kind = {r["id"]: r["kind"] for r in rows}
        self.ka = {r["id"]: r["ka"] for r in rows}
        self.geoms = np.array(to_xy(shapely.from_wkt([r["geom_wkt"] for r in rows]))) if rows else np.array([])
        self.tree = shapely.STRtree(self.geoms) if len(self.geoms) else None
        self.exact: dict[str, set[int]] = defaultdict(set)
        self.variant: dict[str, set[int]] = defaultdict(set)
        self.norm: dict[str, set[int]] = defaultdict(set)
        self.core: dict[tuple, set[int]] = defaultdict(set)
        self.keys: dict[int, list[NameKey]] = defaultdict(list)
        self.names: dict[int, set[str]] = defaultdict(set)
        self._skel: dict[int, set[str]] = {}
        for r in rows:
            fid = r["id"]
            self.exact[r["ka"]].add(fid)
            self._add(fid, r["ka"])
            for nm in (r["ru"] if not r["ru_generated"] else None, r["en"]):
                if nm:
                    self.variant[nm].add(fid)
                    self._add(fid, nm)
        for r in con.execute("SELECT feature_id, name FROM feature_name WHERE kind != 'addr_variant'"):
            if r["feature_id"] in self.pos:
                self.variant[r["name"]].add(r["feature_id"])
                self._add(r["feature_id"], r["name"])

    def _add(self, fid: int, nm: str) -> None:
        self.names[fid].add(nm)
        k = name_key(nm)
        if k is None:
            return
        if k.norm:
            self.norm[k.norm].add(fid)
        if k.core:
            self.core[(k.core, k.ord)].add(fid)
        if k not in self.keys[fid]:
            self.keys[fid].append(k)

    def skeletons(self, fid: int) -> set[str]:
        """Skeletons of all known names of a feature (cached)."""
        sk = self._skel.get(fid)
        if sk is None:
            sk = self._skel[fid] = {skeleton(normalize_ka(n)) for n in self.names[fid]}
        return sk

    def nearest(self, p: Point, fids) -> tuple[int, float]:
        fl = sorted(fids)
        if len(fl) == 1:
            return fl[0], float(shapely.distance(self.geoms[self.pos[fl[0]]], p))
        d = shapely.distance(self.geoms[[self.pos[f] for f in fl]], p)
        i = int(np.argmin(d))
        return fl[i], float(d[i])

    def near(self, p: Point, radius: float) -> list[int]:
        if self.tree is None:
            return []
        idx = self.tree.query(p, predicate="dwithin", distance=radius)
        return [self.fids[i] for i in idx.tolist()]

    def fuzzy(self, k: NameKey, p: Point, radius: float) -> tuple[int, float, float] | None:
        """Best similar feature within ``radius``: (fid, distance, score).

        Similar = same ordinal, same type (when both known) and either the address base is a
        token subset of the feature base (given name omitted: ``რუსთაველის`` ~ ``შოთა რუსთაველის``)
        or the stemmed bases have a SequenceMatcher ratio >= FUZZY_RATIO and the longest address
        token has a counterpart with ratio >= LONG_TOKEN_RATIO.
        """
        if not k.core:
            return None
        cs = k.core_str
        ks = set(k.core)
        longest = max(k.core, key=len)
        best: tuple[float, float, int] | None = None
        for fid in self.near(p, radius):
            if not kinds_compatible(k.kind, self.kind[fid], strict=True):
                continue
            for kv in self.keys[fid]:
                if kv.ord != k.ord or not kv.core:
                    continue
                if not kinds_compatible(k.kind, kv.kind, strict=True):
                    continue
                if len(longest) >= 4 and ks < set(kv.core):
                    score = 0.9
                else:
                    sm = difflib.SequenceMatcher(None, cs, kv.core_str)
                    if sm.real_quick_ratio() < FUZZY_RATIO or sm.quick_ratio() < FUZZY_RATIO:
                        continue
                    score = sm.ratio()
                    if score < FUZZY_RATIO:
                        continue
                    if max(difflib.SequenceMatcher(None, longest, t).ratio() for t in kv.core) < LONG_TOKEN_RATIO:
                        continue
                if best is None or score > best[0]:
                    d = float(shapely.distance(self.geoms[self.pos[fid]], p))
                    best = (score, d, fid)
                elif score == best[0]:
                    d = float(shapely.distance(self.geoms[self.pos[fid]], p))
                    if d < best[1]:
                        best = (score, d, fid)
        if best is None or best[1] > radius:
            return None
        return best[2], best[1], best[0]


def match_street(idx: StreetIndex, s: str, p: Point) -> tuple[str, int | None, float | None]:
    """Run steps 1-6 of the cascade for one street string."""
    how, fid, d = "none", None, None
    k = name_key(s)
    if s in idx.exact:
        how, (fid, d) = "exact", idx.nearest(p, idx.exact[s])
    elif s in idx.variant:
        how, (fid, d) = "variant", idx.nearest(p, idx.variant[s])
    elif k is not None and k.norm and k.norm in idx.norm:
        how, (fid, d) = "normalized", idx.nearest(p, idx.norm[k.norm])
    elif k is not None and k.core and (k.core, k.ord) in idx.core:
        cands = [f for f in idx.core[(k.core, k.ord)] if kinds_compatible(k.kind, idx.kind[f], strict=False)]
        if cands:
            f2, d2 = idx.nearest(p, cands)
            if d2 <= NO_TYPE_MAX_M:
                how, fid, d = "no_type", f2, d2
    if how == "none" and k is not None:
        r = idx.fuzzy(k, p, FUZZY_M)
        if r:
            how, fid, d = "fuzzy", r[0], r[1]
    if how != "none" and d is not None and d > SWAP_TRIGGER_M and k is not None:
        r = idx.fuzzy(k, p, SWAP_M)
        if r and r[0] != fid and r[1] < d:
            how, fid, d = "homonym_swap", r[0], r[1]
    return how, fid, d


#: generic words of neighbourhood names, ignored when matching addr:place / addr:street to areas
PLACE_STOP = {"uban", "ubn", "dasakhleb", "mikroraion", "mikro", "raion", "mr", "mkr", "kvartal", "kv", "masiv",
              "sopel", "dasakhl", "korpus", "plat", "district", "microdistrict", "settlement", "quarter",
              "mikrorain", "rn", "ms"}


def place_core(k: NameKey) -> tuple[str, ...]:
    return tuple(t for t in k.core if t not in PLACE_STOP)


class AreaMatcher:
    """addr:place / addr:street -> area: by name (exact, core, fuzzy), else by location."""

    def __init__(self, con: sqlite3.Connection):
        self.rows = con.execute("SELECT id, kind, ka, ru, en, lat, lon, geom_wkt FROM area").fetchall()
        self.geom = {}
        self.by_norm: dict[str, set[int]] = defaultdict(set)
        self.by_core: dict[tuple, set[int]] = defaultdict(set)
        self.keys: dict[int, list[NameKey]] = defaultdict(list)
        for r in self.rows:
            g = shapely.from_wkt(r["geom_wkt"]) if r["geom_wkt"] else Point(r["lon"], r["lat"])
            self.geom[r["id"]] = to_xy(g)
            for nm in (r["ka"], r["ru"], r["en"]):
                k = name_key(nm) if nm else None
                if k is None:
                    continue
                self.by_norm[k.norm].add(r["id"])
                pc = place_core(k)
                if pc:
                    self.by_core[pc].add(r["id"])
                self.keys[r["id"]].append(k)
        from pipeline.osm.feature_area import AreaIndex

        self.index = AreaIndex(self.rows)
        self._fuzzy_cache: dict[str, int | None] = {}

    def _nearest(self, p: Point, ids) -> tuple[int, float]:
        best = min(ids, key=lambda i: (self.geom[i].distance(p), i))
        return best, float(self.geom[best].distance(p))

    def match(self, s: str, p: Point) -> int | None:
        """Area named ``s`` (nearest of equal names; fuzzy only within PLACE_FUZZY_M)."""
        k = name_key(s)
        if k is None:
            return None
        if k.norm in self.by_norm:
            return self._nearest(p, self.by_norm[k.norm])[0]
        pc = place_core(k)
        if pc and pc in self.by_core:
            return self._nearest(p, self.by_core[pc])[0]
        if not pc:
            return None
        best = self._fuzzy_best(" ".join(pc))
        if best is not None and self.geom[best].distance(p) <= PLACE_FUZZY_M:
            return best
        return None

    def _fuzzy_best(self, cs: str) -> int | None:
        if cs in self._fuzzy_cache:
            return self._fuzzy_cache[cs]
        best = None
        for aid, ks in self.keys.items():
            for kv in ks:
                kc = " ".join(place_core(kv))
                if not kc:
                    continue
                r = difflib.SequenceMatcher(None, cs, kc).ratio()
                if r >= FUZZY_RATIO and (best is None or r > best[0]):
                    best = (r, aid)
        self._fuzzy_cache[cs] = best[1] if best else None
        return self._fuzzy_cache[cs]

    def locate(self, p: Point) -> int | None:
        """Smallest neighbourhood polygon containing ``p`` (else a point place within 300 m)."""
        aid, _ = self.index.place_of(np.array([[p.x, p.y]]))
        return int(aid[0]) or None


_PLACE_WORDS = re.compile(r"უბან|დასახლ|მიკრო|მ/რ|მკრ|კვარტ|მასივ|პლატო|სოფელი|ქალაქი|M/R|mikro|plato|kv\.", re.IGNORECASE)


def _looks_like_place(s: str) -> bool:
    """An addr:street that names a neighbourhood / micro-district rather than a street."""
    return bool(_PLACE_WORDS.search(s))


def build_houses(con: sqlite3.Connection, ex: Extract) -> tuple[list[tuple], list[tuple], dict]:
    idx = StreetIndex(con)
    areas = AreaMatcher(con)
    st = Counter()
    rows: list[tuple] = []
    variants: Counter = Counter()
    cache: dict[tuple, tuple] = {}
    for o in ex.addresses:
        t = o.tags
        raw = (t.get("addr:housenumber") or "").strip()
        if not raw:
            continue
        st["objects"] += 1
        lat, lon = o.geom.y, o.geom.x
        p = Point(proj(lat, lon))
        flags: list[str] = []
        street = (t.get("addr:street") or "").strip()
        place = (t.get("addr:place") or t.get("addr:place:ka") or t.get("addr:place:en") or "").strip()
        alt_streets = [t[k].strip() for k in ALT_STREET_TAGS if (t.get(k) or "").strip()]
        if street:
            st["with_street"] += 1
        elif alt_streets and not place:
            street = alt_streets[0]  # only addr:street:en / :ka / :ru
            flags.append("alt_tag")
            st["alt_street_only"] += 1
        how, fid, d, area_id = "none", None, None, None
        used = street
        if street:
            ck = (street, int(p.x // 100), int(p.y // 100))
            if ck not in cache:
                cache[ck] = match_street(idx, street, p)
            how, fid, d = cache[ck]
            if how == "none":
                for alt in ALT_STREET_TAGS:
                    v = (t.get(alt) or "").strip()
                    if v and v != street:
                        how, fid, d = match_street(idx, v, p)
                        if how != "none":
                            flags.append("alt_tag")
                            used = v
                            break
            if fid is not None and d is not None and d > FAR_REJECT_M:
                # the only street of that name is kilometres away: the real one is missing in OSM
                flags.append(f"far_rejected:{int(round(d))}")
                st["far_rejected"] += 1
                how, fid, d = "none", None, None
        elif place:
            st["place_only"] += 1
            # "ლილო. ივანე ფრანგულიანის ქუჩა": a street written into addr:place
            cands = [place.split(". ", 1)[-1].strip(), place]  # "ლილო. X ქუჩა": try "X ქუჩა" first
            for a in alt_streets:
                cands += [a.split(". ", 1)[-1].strip(), a]
            for cand in dict.fromkeys(c for c in cands if c):
                k = name_key(cand)
                if k is None or k.kind in ("other", "neighbourhood", "district"):
                    continue
                h2, f2, d2 = match_street(idx, cand, p)
                if h2 not in ("none", "homonym_swap") and d2 is not None and d2 <= FUZZY_M:
                    how, fid, d, used = h2, f2, d2, cand
                    flags.append("place_is_street")
                    st["place_is_street"] += 1
                    break
        else:
            st["no_street_no_place"] += 1
        if fid is None and (place or street):
            for cand in (place, street):
                if cand:
                    area_id = areas.match(cand, p)
                    if area_id:
                        flags.append("place_by_name" if cand == place else "street_is_place")
                        break
            if area_id is None and (place or (street and _looks_like_place(street))):
                area_id = areas.locate(p)
                if area_id:
                    flags.append("place_by_location")
            if area_id:
                how = "place"
        st["m_" + how] += 1
        if t.get("addr:street"):
            st["street_m_" + ("matched" if fid else "unmatched")] += 1
        if d is not None and d > FAR_FLAG_M:
            flags.append(f"far:{int(round(d))}")
        if fid and how not in ("exact", "variant") and used not in idx.names[fid]:
            sk = skeleton(normalize_ka(used))
            if sk and sk not in idx.skeletons(fid):
                variants[(fid, used)] += 1
        parts = parse_house(raw)
        if not parts:
            st["junk_numbers"] += 1
            rows.append((fid, area_id, raw, None, None, None, None, None, "junk", round(lat, 7), round(lon, 7),
                         street or None, how, ",".join(flags) or None, o.type, o.id))
            continue
        for hp in parts:
            st["parts_" + hp.kind] += 1
            rows.append((fid, area_id, raw, hp.n, hp.lit or None, hp.sub or None, hp.n_to, hp.corp or None,
                         hp.kind, round(lat, 7), round(lon, 7), street or None, how, ",".join(flags) or None,
                         o.type, o.id))
    ws = st.get("with_street", 0)
    st["street_matched_pct"] = round(100.0 * st.get("street_m_matched", 0) / ws, 2) if ws else 0.0
    st["rows"] = len(rows)
    vrows = [(fid, script_lang(nm) or "lat", nm, "addr_variant", "addr") for (fid, nm), _n in sorted(variants.items())]
    return rows, vrows, dict(st)


def run(con: sqlite3.Connection, ex: Extract) -> dict:
    with stage(log, "osm.houses", addresses=len(ex.addresses)) as out:
        rows, vrows, st = build_houses(con, ex)
        out.update(st)
        with tx(con):
            con.execute("DELETE FROM house")
            con.execute("DELETE FROM feature_name WHERE kind = 'addr_variant'")
            con.executemany(
                "INSERT INTO house(feature_id, area_id, raw, n, lit, sub, n_to, corp, kind, lat, lon, addr_street, "
                "match, flags, osm_type, osm_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            con.executemany("INSERT OR IGNORE INTO feature_name(feature_id, lang, name, kind, source) "
                            "VALUES (?,?,?,?,?)", vrows)
        out["addr_variants"] = len(vrows)
        return out
