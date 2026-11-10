"""Merge the reference data of a Russian-language district into the database.

Inputs: ``ref.json`` (:mod:`pipeline.ru.fetch`), the researcher and classifier results
(:mod:`pipeline.ru.batches`). Everything written here is owned by this stage and is rebuilt on every
run: eponyms and their categories, ``family.eponym_id``, ``name_history`` rows with source
``ruwiki``, ``feature_ref`` rows, and the features with ``note_source = 'ref'`` (heritage buildings
of kind ``house`` and heritage monuments that OSM does not have).

Sources and trust:

* eponym identity: a Wikidata QID only when it comes from the street item's "named after" statement
  or the OSM etymology tag (never from an LLM); otherwise the eponym has no QID;
* years, gender and origin of a person: Wikidata facts of that QID only;
* categories: the Haiku classifier (``source = 'llm'``, shown as "оценка ИИ" in the client);
* former names and notable buildings: extracted by the Opus researcher from the article text;
  a building is used only when its house number is found among the OSM addresses of that street.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict

import shapely
from shapely.geometry import Point

from pipeline.config import CITY
from pipeline.db import tx
from pipeline.export.taxonomy import load_taxonomy, taxonomy_ids
from pipeline.log import get_logger, stage
from pipeline.names.houses import parse_house_ru
from pipeline.names.parse import KIND_GROUP, LANDMARK_KINDS
from pipeline.osm.geo import proj, to_xy
from pipeline.ru.batches import BATCH_DIR, family_origins, load_results
from pipeline.ru.fetch import STREET_KINDS, load_ref, osm_wiki_tags

log = get_logger(__name__)

VALID_KINDS = {"person", "persons_multi", "group", "fictional_character", "toponym", "event_date", "nature",
               "concept", "object"}
PERSON_KINDS = {"person", "persons_multi"}
LANDMARK_NEAR_M = 45.0
HOUSE_NEAR_M = 40.0
STREET_NEAR_M = 70.0
NAME_MAX = 110
PROTECTION = {"Ф": "объект культурного наследия федерального значения",
              "Р": "объект культурного наследия регионального значения",
              "М": "объект культурного наследия местного значения",
              "В": "выявленный объект культурного наследия",
              "Ц": "ценный градоформирующий объект"}
SKIP_WV_PAGES = ("Новодевичье кладбище",)       # graves: several hundred objects nobody searches by address
_STOP = {"дом", "жилой", "доходный", "здание", "главный", "городская", "городской", "усадьба", "флигель", "корпус",
         "имени", "при", "для", "под", "над", "the", "и", "в", "на", "с", "по", "им", "ограда", "ворота", "строение",
         "служебный", "служебные", "хозяйственный", "хозяйственные", "постройка", "постройки", "сторожка",
         "пристройка", "левый", "правый", "северный", "южный", "восточный", "западный", "часть", "стена", "стены",
         "службы", "служба", "встройка", "одноэтажная", "двухэтажная", "трехэтажная", "планировка", "озеленение",
         "благоустройство", "приемы", "каменный", "деревянный", "жилые", "комплекс", "ансамбль", "застройка",
         "участок", "владение", "строения", "здания", "дома", "палаты", "конюшня", "каретный", "сарай", "людская",
         "кухня", "погреб", "амбар", "оранжерея", "беседка", "павильон", "объект"}
_STOP_STEMS = {w[:5] for w in _STOP if len(w) > 2}


# ---------------------------------------------------------------------------- helpers
def stems(name: str) -> set[str]:
    words = re.findall(r"[a-zа-я0-9]+", (name or "").lower().replace("ё", "е"))
    return {w[:5] for w in words if len(w) > 2 and w not in _STOP and w[:5] not in _STOP_STEMS}


def similar(a: str, b: str) -> bool:
    sa, sb = stems(a), stems(b)
    if not sa or not sb:
        return False
    common = len(sa & sb)
    return common >= 2 or common / min(len(sa), len(sb)) >= 0.6


def clip(s: str | None, n: int) -> str:
    s = re.sub(r"\s+", " ", (s or "")).strip()
    return s if len(s) <= n else s[: n - 1].rstrip(" ,;.") + "…"


def year_ok(y) -> int | None:
    try:
        y = int(y)
    except (TypeError, ValueError):
        return None
    return y if 1000 <= y <= 2026 else None


class Points:
    """Nearest-neighbour index over (id, lat, lon) rows in local metres."""

    def __init__(self, rows: list[tuple[int, float, float]]):
        self.ids = [r[0] for r in rows]
        self.pts = [Point(*proj(r[1], r[2])) for r in rows]
        self.tree = shapely.STRtree(self.pts) if self.pts else None

    def near(self, lat: float, lon: float, radius: float) -> list[tuple[float, int]]:
        if self.tree is None:
            return []
        p = Point(*proj(lat, lon))
        idx = self.tree.query(p, predicate="dwithin", distance=radius).tolist()
        return sorted((self.pts[i].distance(p), self.ids[i]) for i in idx)


# ---------------------------------------------------------------------------- eponyms
def merge_eponyms(con: sqlite3.Connection, ref: dict) -> dict:
    st: Counter = Counter()
    tax, _src, _kinds = load_taxonomy()
    tax_ids = taxonomy_ids(tax)
    results = {str(r.get("family_key")): r for r in load_results("classifier")}
    origins = family_origins(con, ref)
    fams = con.execute(
        "SELECT fam.id, fam.key, GROUP_CONCAT(f.wikidata, ';') AS wd FROM family fam "
        "JOIN feature f ON f.family_id = fam.id GROUP BY fam.id ORDER BY fam.key").fetchall()
    matched = match_people(ref, [((results.get(f["key"]) or {}).get("name_ru") or (origins.get(f["key"]) or {}).get("label_ru"),
                                  (results.get(f["key"]) or {}).get("kind") or (origins.get(f["key"]) or {}).get("kind"))
                                 for f in fams])
    st["people_matched_by_name"] = len(matched)
    with tx(con):
        con.execute("UPDATE family SET eponym_id = NULL")
        con.execute("UPDATE name_history SET eponym_id = NULL")
        con.execute("DELETE FROM eponym_category")
        con.execute("DELETE FROM eponym")
        by_qid: dict[str, int] = {}
        for fam in fams:
            res, org = results.get(fam["key"]), origins.get(fam["key"])
            if not res and not org:
                st["families_without_source"] += 1
                continue
            kind = (res or {}).get("kind") or (org or {}).get("kind")
            if kind not in VALID_KINDS:
                st[f"skip_kind_{kind}"] += 1
                continue
            osm_q = list(dict.fromkeys(re.findall(r"Q\d+", fam["wd"] or "")))
            p138_q = list((org or {}).get("allowed_qids", []))
            qid = (res or {}).get("wikidata_qid") or (org or {}).get("wikidata_qid")
            if qid not in osm_q and qid not in p138_q:
                if qid:
                    st["qid_not_allowed"] += 1
                qid = None
            if qid is None and len(set(osm_q)) == 1 and kind in PERSON_KINDS:
                qid = osm_q[0]  # the OSM etymology tag names the entity
            label = (res or {}).get("name_ru") or (org or {}).get("label_ru")
            by_name = False
            if qid is None and kind == "person" and label in matched:
                qid, by_name = matched[label], True   # the only human in Wikidata with this full name
            p = ref["people"].get(qid or "", {})
            label = label or p.get("ru")
            if not label:
                st["skip_no_label"] += 1
                continue
            is_person = kind in PERSON_KINDS
            origin = None
            if is_person and p.get("citizen"):
                origin = "local" if set(p["citizen"]) & set(CITY.local_countries) else "foreign"
            elif is_person and (res or {}).get("origin") in ("local", "foreign"):
                origin = res["origin"]
            cats = [c for c in (res or {}).get("categories", []) if c in tax_ids and "." in c][:4]
            st["categories_dropped"] += len((res or {}).get("categories", [])) - len(cats)
            occ = [clip(o, 40) for o in ((res or {}).get("occupations") or p.get("occupations") or []) if o][:6]
            source = "wd_match" if by_name else "osm" if qid and qid in osm_q else "wd_p138" if qid else "llm"
            conf = min(float((res or {}).get("confidence") or 0.8), float((org or {}).get("confidence") or 1.0))
            short = clip((res or {}).get("short_ru") or (org or {}).get("who_ru") or p.get("desc"), 170)
            if qid and qid in by_qid:
                eid = by_qid[qid]
            else:
                cur = con.execute(
                    "INSERT INTO eponym(qid, key, kind, ka, ru, en, gender, birth_year, death_year, origin, short_ru, "
                    "occupations_ru, confidence, source, needs_review) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (qid, None if qid else "ru:" + fam["key"], kind, None, clip(label, 120), p.get("en"),
                     p.get("gender") if is_person else None,
                     year_ok(p.get("birth_year")) if is_person else None,
                     year_ok(p.get("death_year")) if is_person else None,
                     origin, short or None, json.dumps(occ, ensure_ascii=False) if occ else None,
                     round(conf, 2), source, int(conf < 0.6)))
                eid = cur.lastrowid
                if qid:
                    by_qid[qid] = eid
                for rank, c in enumerate(cats):
                    con.execute("INSERT OR IGNORE INTO eponym_category(eponym_id, category, rank, source) VALUES (?,?,?,?)",
                                (eid, c, rank, "llm"))
                st["eponyms"] += 1
                st[f"kind_{kind}"] += 1
                st[f"source_{source}"] += 1
            con.execute("UPDATE family SET eponym_id = ? WHERE id = ?", (eid, fam["id"]))
            st["families_linked"] += 1
    return dict(st)


_FULL_NAME = re.compile(r"^[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)? [А-ЯЁ][а-яё]+ [А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?$")


def match_people(ref: dict, labels: list[tuple[str | None, str | None]]) -> dict[str, str]:
    """Full name ("Николай Нилович Бурденко") -> QID when exactly one human in Wikidata has that
    Russian label. Only three-word names (given name, patronymic, surname) are tried, so that a
    homeowner known by a surname is never mistaken for a namesake. The facts of the matched
    people are added to ``ref["people"]``."""
    from pipeline.ru import sources as S
    from pipeline.ru.fetch import humans_by_label, wd_people

    names = sorted({lb for lb, kind in labels if lb and kind == "person" and _FULL_NAME.match(lb)})
    if not names:
        return {}
    def inverted(n: str) -> str:   # many Russian labels follow the Wikipedia title: "Сеченов, Иван Михайлович"
        given, patronymic, surname = n.split(" ")
        return f"{surname}, {given} {patronymic}"

    try:
        found = humans_by_label(names + [inverted(n) for n in names])
    except (S.Offline, RuntimeError) as exc:
        log.warning("people by name: Wikidata not available (%s); eponyms keep no QID", exc)
        return {}
    out = {}
    for n in names:
        qs = set(found.get(n, [])) | set(found.get(inverted(n), []))
        if len(qs) == 1:
            out[n] = qs.pop()
    # Wikidata labels often omit the patronymic ("Иван Сеченов"): accept such an item only when its
    # Russian Wikipedia article is titled "Фамилия, Имя Отчество"
    short = {n: f"{n.split(' ')[0]} {n.split(' ')[2]}" for n in names if n not in out}
    cand: dict[str, list[str]] = {}
    try:
        cand = humans_by_label(sorted(set(short.values())))
        missing = sorted(({q for qs in cand.values() for q in qs} | set(out.values())) - set(ref["people"]))
        if missing:
            ref["people"].update(wd_people(missing, offline=False))
    except (S.Offline, RuntimeError) as exc:
        log.warning("people by name: facts not available (%s)", exc)
    for n, sh in short.items():
        given, patronymic, surname = n.split(" ")
        title = f"{surname}, {given} {patronymic}".replace("ё", "е")
        hits = [q for q in cand.get(sh, []) if (ref["people"].get(q, {}).get("ruwiki") or "").replace("ё", "е") == title]
        if len(set(hits)) == 1:
            out[n] = hits[0]
    return {n: q for n, q in out.items() if q in ref["people"]}


# ---------------------------------------------------------------------------- former names
def _researcher(con: sqlite3.Connection) -> list[tuple[dict, dict, list[sqlite3.Row]]]:
    """(result, index entry, street features of the dossier) for every researcher result."""
    idx_path = BATCH_DIR / "researcher" / "index.json"
    if not idx_path.exists():
        return []
    index = json.loads(idx_path.read_text(encoding="utf-8"))
    ph = ",".join("?" * len(STREET_KINDS))
    by_name: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in con.execute(f"SELECT id, ka, kind, ord, family_id, lat, lon FROM feature WHERE kind IN ({ph})", STREET_KINDS):
        by_name[r["ka"]].append(r)
    out = []
    for res in load_results("researcher"):
        meta = index.get(str(res.get("id")))
        if not meta:
            continue
        feats = [f for nm in meta["features"] for f in by_name.get(nm, [])]
        out.append((res, meta, feats))
    return out


def merge_history(con: sqlite3.Connection, research) -> dict:
    st: Counter = Counter()
    with tx(con):
        con.execute("DELETE FROM name_history WHERE source = 'ruwiki'")
        for res, meta, feats in research:
            current = {f["ka"].lower().replace("ё", "е") for f in feats}
            for fn in res.get("former_names") or []:
                name = clip(fn.get("name") if isinstance(fn, dict) else None, 120)
                if not name or name.lower().replace("ё", "е") in current or len(name) < 3:
                    continue
                y0, y1 = year_ok(fn.get("from")), year_ok(fn.get("to"))
                if y0 and y1 and y0 > y1:
                    y0 = y1 = None
                targets = feats
                if len(feats) > 1:  # an article about "1-й ... 3-й переулок": match the ordinal when the name has one
                    m = re.match(r"\s*(\d+)-", name)
                    if m:
                        targets = [f for f in feats if f["ord"] == int(m.group(1))]
                    elif len(feats) > 4:
                        targets = []
                if not targets:
                    st["former_unattached"] += 1
                for f in targets:
                    con.execute(
                        "INSERT INTO name_history(feature_id, family_id, name_ka, name_ru, name_lat, from_year, to_year, "
                        "eponym_id, source, ref, confidence) VALUES (?,?,NULL,?,NULL,?,?,NULL,'ruwiki',?,0.9)",
                        (f["id"], f["family_id"], name, y0, y1, meta["title"]))
                    st["former_names"] += 1
    return dict(st)


# ---------------------------------------------------------------------------- places: heritage, buildings
def _wv_text(m: dict, with_address: bool = True) -> str:
    parts = []
    if m.get("year"):
        parts.append("Год: " + clip(m["year"], 40))
    if m.get("author"):
        parts.append("Автор: " + clip(m["author"], 90))
    if PROTECTION.get(m.get("protection", "")):
        parts.append(PROTECTION[m["protection"]])
    if m.get("address") and with_address:
        parts.append("Адрес по реестру: " + clip(m["address"], 80))
    if m.get("desc"):
        parts.append(clip(m["desc"], 200))
    return "; ".join(parts)


def _clear(con: sqlite3.Connection) -> None:
    con.execute("DELETE FROM feature_ref")
    con.execute("DELETE FROM feature WHERE note_source = 'ref'")
    for r in con.execute("SELECT id, flags FROM house WHERE flags LIKE '%landmark:house%'").fetchall():
        flags = ",".join(x for x in (r["flags"] or "").split(",") if x and x != "landmark:house")
        con.execute("UPDATE house SET flags = ? WHERE id = ?", (flags or None, r["id"]))


class Places:
    """Existing landmarks, addresses and streets, plus the features created by this stage."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con
        ph = ",".join("?" * len(LANDMARK_KINDS))
        self.lm = {r["id"]: r for r in con.execute(
            f"SELECT id, kind, ka, lat, lon, osm_ids, parent_id FROM feature WHERE kind IN ({ph}) AND lat IS NOT NULL",
            sorted(LANDMARK_KINDS))}
        self.lm_pts = Points([(i, r["lat"], r["lon"]) for i, r in self.lm.items()])
        self.lm_of_osm: dict[str, int] = {}
        tags = osm_wiki_tags()
        self.lm_of_qid: dict[str, int] = {}
        for i, r in self.lm.items():
            for ref in json.loads(r["osm_ids"] or "[]"):
                self.lm_of_osm.setdefault(ref, i)
                q = tags.get(ref, {}).get("wikidata")
                if q:
                    self.lm_of_qid.setdefault(q, i)
        self.houses = {r["id"]: r for r in con.execute(
            "SELECT id, feature_id, n, lit, sub, corp, raw, lat, lon, flags, osm_type, osm_id FROM house "
            "WHERE lat IS NOT NULL AND n IS NOT NULL")}
        self.house_pts = Points([(i, r["lat"], r["lon"]) for i, r in self.houses.items()])
        self.by_street: dict[int, list[sqlite3.Row]] = defaultdict(list)
        for r in self.houses.values():
            if r["feature_id"] is not None:
                self.by_street[r["feature_id"]].append(r)
        self.streets = [r for r in con.execute(
            "SELECT id, kind, geom_wkt FROM feature WHERE geom_wkt IS NOT NULL AND highway IS NOT NULL")
            if KIND_GROUP.get(r["kind"]) in ("street*", "lane*") or r["kind"] == "square"]
        self.street_geoms = [to_xy(shapely.from_wkt(r["geom_wkt"])) for r in self.streets]
        self.street_tree = shapely.STRtree(self.street_geoms) if self.street_geoms else None
        self.created: dict[int, dict] = {}          # new feature id -> {"name", "lat", "lon"}
        self.created_rows: list[tuple[int, float, float]] = []
        self.linked_houses: set[int] = {i for i, r in self.houses.items() if "landmark:" in (r["flags"] or "")}
        self.ref_rank: Counter = Counter()

    def nearest_street(self, lat: float, lon: float) -> int | None:
        if self.street_tree is None:
            return None
        p = Point(*proj(lat, lon))
        idx = self.street_tree.query(p, predicate="dwithin", distance=STREET_NEAR_M).tolist()
        if not idx:
            return None
        return self.streets[min(idx, key=lambda i: self.street_geoms[i].distance(p))]["id"]

    def add_ref(self, fid: int, source: str, text: str) -> None:
        text = clip(text, 330)
        if not text:
            return
        rank = self.ref_rank[(fid, source)]
        self.ref_rank[(fid, source)] += 1
        self.con.execute("INSERT OR REPLACE INTO feature_ref(feature_id, source, rank, text) VALUES (?,?,?,?)",
                         (fid, source, rank, text))

    def find_existing(self, name: str, lat: float, lon: float, qid: str | None = None,
                      house: sqlite3.Row | None = None) -> int | None:
        """An OSM landmark or a feature created earlier in this run that is the same object."""
        if qid and qid in self.lm_of_qid:
            return self.lm_of_qid[qid]
        if house is not None and house["osm_type"] and house["osm_id"] is not None:
            i = self.lm_of_osm.get(f"{house['osm_type'][:1]}{house['osm_id']}")
            if i is not None and self.lm[i]["kind"] not in ("plaque", "monument", "stop"):
                return i
        for _d, i in self.lm_pts.near(lat, lon, LANDMARK_NEAR_M):
            if self.lm[i]["kind"] not in ("plaque", "stop") and similar(name, self.lm[i]["ka"]):
                return i
        best = None
        for fid, c in self.created.items():
            d = Point(*proj(c["lat"], c["lon"])).distance(Point(*proj(lat, lon)))
            if d <= 30.0 and (similar(name, c["name"]) or d <= 8.0) and (best is None or d < best[0]):
                best = (d, fid)
        return best[1] if best else None

    def create(self, kind: str, name: str, lat: float, lon: float, house: sqlite3.Row | None) -> int:
        parent = house["feature_id"] if house is not None and house["feature_id"] is not None else self.nearest_street(lat, lon)
        osm_ids: list[str] = []
        if house is not None and house["id"] not in self.linked_houses and house["osm_type"] and kind == "house":
            osm_ids = [f"{house['osm_type'][:1]}{house['osm_id']}"]
            flags = ",".join(x for x in [house["flags"], "landmark:house"] if x)
            self.con.execute("UPDATE house SET flags = ? WHERE osm_type = ? AND osm_id = ?",
                             (flags, house["osm_type"], house["osm_id"]))
            self.linked_houses.update(i for i, r in self.houses.items()
                                      if r["osm_type"] == house["osm_type"] and r["osm_id"] == house["osm_id"])
        name = clip(name, NAME_MAX)
        cur = self.con.execute(
            "INSERT INTO feature(kind, ka, ru, en, ru_generated, parent_id, len_m, lat, lon, bbox, geom_wkt, osm_ids, "
            "note, note_source) VALUES (?,?,?,NULL,1,?,0,?,?,?,?,?,NULL,'ref')",
            (kind, name, name, parent, round(lat, 7), round(lon, 7), f"{lat:.7f},{lon:.7f},{lat:.7f},{lon:.7f}",
             f"POINT ({lon:.7f} {lat:.7f})", json.dumps(osm_ids)))
        fid = cur.lastrowid
        self.created[fid] = {"name": name, "lat": lat, "lon": lon}
        return fid

    def house_at(self, lat: float, lon: float) -> sqlite3.Row | None:
        near = self.house_pts.near(lat, lon, HOUSE_NEAR_M)
        return self.houses[near[0][1]] if near else None

    def house_by_number(self, street_ids: list[int], text: str) -> sqlite3.Row | None:
        parts = parse_house_ru(text)
        if not parts:
            return None
        hp = parts[0]
        want = set(hp.corp.split()) if hp.corp else set()
        cands = []
        for sid in street_ids:
            for r in self.by_street.get(sid, []):
                if r["n"] != hp.n or (r["lit"] or "") != hp.lit:
                    continue
                if hp.sub and (r["sub"] or "") and (r["sub"] or "").split("/")[0] != hp.sub.split("/")[0]:
                    continue
                have = set((r["corp"] or "").split())
                if want and not want <= have:
                    continue
                cands.append((bool(have - want), bool(hp.sub) != bool(r["sub"]), r["id"], r))
        return min(cands, key=lambda c: c[:3])[3] if cands else None


def merge_places(con: sqlite3.Connection, ref: dict, research) -> dict:
    st: Counter = Counter()
    with tx(con):
        _clear(con)
        pl = Places(con)
        # --- heritage register (Wikivoyage lists): the complex head or a single object
        mons = [m for m in ref.get("monuments", []) if not str(m.get("page", "")).startswith(SKIP_WV_PAGES)]
        heads = {m.get("knid"): m["name"] for m in mons if m.get("complex") and m.get("complex") == m.get("knid")}
        for m in mons:
            if m.get("status") in ("destroyed", "снесён") or len(m["name"]) < 4:
                st["wv_skipped"] += 1
                continue
            if m.get("complex") and m["complex"] != m.get("knid") and m["complex"] in heads:
                # a part of an ensemble: kept only when it has a name of its own ("Больничные палаты"),
                # and named with its ensemble; "Флигель", "Ограда" stay inside the ensemble object
                if not stems(m["name"]) or similar(m["name"], heads[m["complex"]]):
                    st["wv_complex_parts_skipped"] += 1
                    continue
                m = dict(m, name=clip(f"{m['name']} ({heads[m['complex']]})", NAME_MAX))
                st["wv_complex_parts_kept"] += 1
            house = pl.house_at(m["lat"], m["lon"])
            fid = pl.find_existing(m["name"], m["lat"], m["lon"], m.get("wdid"), house)
            text = _wv_text(m, with_address=house is None)
            if fid is None:
                kind = "monument" if m.get("type") == "monument" else "house"
                name = m["name"]
                if not stems(name) and m.get("address"):   # "Доходный дом" -> "Доходный дом, Остоженка, 7"
                    name = f"{name}, {clip(m['address'], 60)}"
                fid = pl.create(kind, name, m["lat"], m["lon"], house)
                st[f"wv_new_{kind}"] += 1
            else:
                st["wv_matched"] += 1
                if fid in pl.lm and not similar(m["name"], pl.lm[fid]["ka"]):
                    text = clip(m["name"], 90) + (": " + text if text else "")
            pl.add_ref(fid, "wikivoyage", text)
        # --- notable buildings of the street articles
        for res, meta, feats in research:
            sids = [f["id"] for f in feats]
            for b in (res.get("buildings") or [])[:80]:
                if not isinstance(b, dict) or not b.get("house") or not b.get("name"):
                    continue
                st["buildings"] += 1
                house = pl.house_by_number(sids, str(b["house"]))
                if house is None:
                    st["buildings_no_address"] += 1
                    continue
                name = clip(b["name"], NAME_MAX)
                parts = []
                if b.get("year"):
                    parts.append("Год: " + clip(str(b["year"]), 40))
                if b.get("architect"):
                    parts.append("Архитектор: " + clip(str(b["architect"]), 90))
                if b.get("fact"):
                    parts.append(clip(str(b["fact"]), 220))
                fid = pl.find_existing(name, house["lat"], house["lon"], None, house)
                if fid is None:
                    if not b.get("fact") and len(stems(name)) == 0:
                        st["buildings_generic"] += 1
                        continue
                    shown = name if stems(name) or not feats else clip(f"{name}, {feats[0]['ka']}, {b['house']}", NAME_MAX)
                    fid = pl.create("house", shown, house["lat"], house["lon"], house)
                    st["buildings_new"] += 1
                    text = "; ".join(parts)
                else:
                    st["buildings_matched"] += 1
                    known = pl.lm[fid]["ka"] if fid in pl.lm else pl.created[fid]["name"]
                    text = "; ".join(parts)
                    if not similar(name, known):
                        text = name + (": " + text if text else "")
                pl.add_ref(fid, "ruwiki", text)
            # --- facts about the street itself
            facts = [clip(str(x), 170) for x in (res.get("facts") or []) if isinstance(x, str) and x.strip()][:3]
            for f in feats[:6]:
                for t in facts:
                    pl.add_ref(f["id"], "ruwiki", t)
                    st["street_facts"] += 1
        # --- Wikidata facts of landmarks tagged with their own item
        for q, fid in pl.lm_of_qid.items():
            d = ref["items"].get(q)
            if not d:
                continue
            parts = []
            if d.get("year"):
                parts.append(f"Год: {d['year']}")
            if d.get("architects"):
                parts.append("Архитектор: " + ", ".join(d["architects"][:3]))
            if parts:
                pl.add_ref(fid, "wikidata", "; ".join(parts))
                st["wikidata_notes"] += 1
        st["features_created"] = len(pl.created)
    return dict(st)


def run(con: sqlite3.Connection) -> dict:
    from pipeline.osm import feature_area

    with stage(log, "ru.merge") as out:
        ref = load_ref()
        research = _researcher(con)
        out["researcher_results"] = len(research)
        out["places"] = merge_places(con, ref, research)   # first: it deletes and re-creates features
        out["history"] = merge_history(con, research)
        out["eponyms"] = merge_eponyms(con, ref)
        out["feature_area"] = feature_area.run(con).get("rows")
        log.info("ru.merge: %s", json.dumps(out, ensure_ascii=False))
        return out
