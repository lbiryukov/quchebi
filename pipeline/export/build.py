"""Build the phone pack (docs/pack-format.md) from ``data/runcity.sqlite``.

Entry point: :func:`export_pack`. Feature order is deterministic: ``(kind index, ru or ka, ka,
point, osm ids)``; database ids never reach the pack, every reference is re-mapped to an index.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from pipeline.config import APP_DATA, APP_TILES, BUILD, PACK_FORMAT_VERSION
from pipeline.db import get_meta
from pipeline.export import geom as G
from pipeline.export.kinds import group_codes, load_kinds
from pipeline.export.polyline import e5
from pipeline.export.taxonomy import load_taxonomy, taxonomy_ids
from pipeline.log import get_logger, stage

log = get_logger(__name__)

DATA_FILES = ("core.json", "geom.json", "houses.json", "areas.json", "eponyms.json")
TILES_MANIFEST = APP_TILES / "tiles-manifest.json"
EXPORT_STATS = BUILD / "export_stats.json"

# A house row is exported when it has a leading number and a point. Golden counts reuse this.
HOUSE_EXPORT_WHERE = "h.n IS NOT NULL AND h.lat IS NOT NULL AND h.lon IS NOT NULL"
# Former names below this confidence are not shipped (history stage recommendation).
OLD_NAME_MIN_CONF = 0.5
ALT_KIND_ORDER = {"official": 0, "alt": 1, "addr_variant": 2}
ORIGIN_CODES = {"georgian": "ge", "foreign": "fo"}
GENDER_CODES = {"m": "m", "f": "f", "м": "m", "ж": "f", "male": "m", "female": "f"}
# Kind group of landmarks (places); their house rows carry the place index as the 9th element.
PLACE_GROUP = "place*"
_OSM_REF = re.compile(r"^([nwr])(\d+)$")


def dumps(obj: Any) -> bytes:
    """Compact UTF-8 JSON as shipped in the pack."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _fold(s: str | None) -> str:
    return (s or "").casefold().replace("ё", "е")


def _bbox_e5(text: str | None) -> list[int] | None:
    if not text:
        return None
    try:
        vals = [float(v) for v in text.replace(";", ",").split(",")]
    except ValueError:
        return None
    if len(vals) != 4:
        return None
    return [e5(v) for v in vals]


def _rows(con: sqlite3.Connection, sql: str, *args) -> list[sqlite3.Row]:
    cur = con.execute(sql, args)
    cur.row_factory = sqlite3.Row
    return cur.fetchall()


class _Problems:
    """Counters of data problems found during export (logged and written into the pack stats)."""

    def __init__(self) -> None:
        self.c: Counter[str] = Counter()
        self.samples: dict[str, list[str]] = defaultdict(list)

    def add(self, key: str, sample: str | None = None) -> None:
        self.c[key] += 1
        if sample is not None and len(self.samples[key]) < 10:
            self.samples[key].append(sample)


# --------------------------------------------------------------------------- areas
def _areas(con: sqlite3.Connection, prob: _Problems):
    rows = _rows(con, "SELECT * FROM area")
    by_id = {r["id"]: r for r in rows}

    def key(r):
        return (_fold(r["ka"]), _fold(r["ru"]), _fold(r["en"]), r["osm_type"] or "", r["osm_id"] or 0, r["id"])

    districts = sorted((r for r in rows if r["kind"] == "district"), key=key)
    places = sorted((r for r in rows if r["kind"] != "district"), key=key)
    d_idx = {r["id"]: i for i, r in enumerate(districts)}
    p_idx = {r["id"]: i for i, r in enumerate(places)}

    def district_of(area_id: int | None) -> int | None:
        seen = set()
        while area_id is not None and area_id not in seen:
            seen.add(area_id)
            if area_id in d_idx:
                return d_idx[area_id]
            r = by_id.get(area_id)
            area_id = r["parent_id"] if r is not None else None
        return None

    core_d, core_p, areas_d, areas_p = [], [], [], []
    for r in districts:
        e = {k: r[k] for k in ("ka", "ru", "en") if r[k]}
        if "ka" not in e:
            prob.add("district_without_ka", str(r["id"]))
        core_d.append(e)
        g = G.load_wkt(r["geom_wkt"])
        if g is None:
            prob.add("district_without_polygon", r["ka"] or r["ru"] or str(r["id"]))
        areas_d.append({"poly": [G.encode_parts(poly) for poly in G.area_polys(g)]})
    for r in places:
        e: dict[str, Any] = {k: r[k] for k in ("ka", "ru", "en") if r[k]}
        e["kind"] = r["kind"]
        parent = district_of(r["parent_id"])
        if parent is not None:
            e["parent"] = parent
        g = G.load_wkt(r["geom_wkt"])
        lat, lon = r["lat"], r["lon"]
        if (lat is None or lon is None) and g is not None:
            lat, lon = G.representative_point(g)
        if lat is not None and lon is not None:
            e["c"] = [e5(lat), e5(lon)]
        else:
            prob.add("place_without_point", r["ka"] or str(r["id"]))
        if r["area_km2"] is not None:
            e["km2"] = round(float(r["area_km2"]), 2)
        core_p.append(e)
        areas_p.append({"poly": [G.encode_parts(poly) for poly in G.area_polys(g)]})
    return core_d, core_p, areas_d, areas_p, d_idx, p_idx


# --------------------------------------------------------------------------- main
def export_pack(
    con: sqlite3.Connection,
    out_dir: Path | str = APP_DATA,
    *,
    tiles_manifest: Path | str | None = TILES_MANIFEST,
    now: dt.datetime | None = None,
    taxonomy_paths: tuple[Path, ...] | None = None,
    stats_path: Path | str | None = EXPORT_STATS,
) -> dict[str, Any]:
    """Write the pack into ``out_dir``; return ``{"manifest": ..., "stats": ...}``.

    ``stats`` (export problems, sizes) is also written to ``stats_path`` (not part of the pack).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    now = now or dt.datetime.now()
    prob = _Problems()

    with stage(log, "export_pack", out=str(out_dir)) as st:
        kinds = load_kinds()
        kind_idx = {k["code"]: i for i, k in enumerate(kinds)}
        tax, tax_src, ep_kinds = load_taxonomy(taxonomy_paths) if taxonomy_paths is not None else load_taxonomy()
        tax_ids = taxonomy_ids(tax)

        core_d, core_p, areas_d, areas_p, d_idx, p_idx = _areas(con, prob)

        # ---------------------------------------------------------- features: order + index
        frows = _rows(con, "SELECT * FROM feature")
        other = kind_idx.get("other", len(kinds) - 1)

        def kidx(r) -> int:
            k = kind_idx.get(r["kind"])
            if k is None:
                prob.add("unknown_kind", f"{r['kind']}: {r['ka']}")
                return other
            return k

        fkind = {r["id"]: kidx(r) for r in frows}

        def fkey(r):
            return (fkind[r["id"]], _fold(r["ru"] or r["ka"]), _fold(r["ka"]),
                    round(r["lat"] or 0.0, 6), round(r["lon"] or 0.0, 6), r["osm_ids"] or "", r["id"])

        frows.sort(key=fkey)
        f_idx = {r["id"]: i for i, r in enumerate(frows)}
        n = len(frows)
        log.info("features: %d", n)

        # ---------------------------------------------------------- families
        fam_ids = sorted({r["family_id"] for r in frows if r["family_id"] is not None})
        fam_rows = {r["id"]: r for r in _rows(con, "SELECT * FROM family")}
        missing_fam = [i for i in fam_ids if i not in fam_rows]
        for i in missing_fam:
            prob.add("feature_family_missing", str(i))
        fam_ids = sorted((i for i in fam_ids if i in fam_rows), key=lambda i: (fam_rows[i]["key"], i))
        fam_idx = {fid: j for j, fid in enumerate(fam_ids)}

        # ---------------------------------------------------------- former names (name_history + feature_name old)
        hist = defaultdict(list)
        for r in _rows(con, "SELECT * FROM name_history WHERE feature_id IS NOT NULL ORDER BY id"):
            if r["feature_id"] not in f_idx:
                continue
            if r["confidence"] is not None and r["confidence"] < OLD_NAME_MIN_CONF:
                prob.add("old_name_low_confidence")
                continue
            hist[r["feature_id"]].append(r)
        n_unlinked_hist = con.execute("SELECT COUNT(*) FROM name_history WHERE feature_id IS NULL").fetchone()[0]
        if n_unlinked_hist:
            prob.c["old_name_unlinked"] += n_unlinked_hist

        names = defaultdict(list)
        for r in _rows(con, "SELECT * FROM feature_name ORDER BY feature_id, kind, lang, name"):
            if r["feature_id"] in f_idx:
                names[r["feature_id"]].append(r)

        # ---------------------------------------------------------- eponyms referenced
        ep_ref = {fam_rows[i]["eponym_id"] for i in fam_ids if fam_rows[i]["eponym_id"] is not None}
        ep_ref |= {r["eponym_id"] for rs in hist.values() for r in rs if r["eponym_id"] is not None}
        ep_rows = {r["id"]: r for r in _rows(con, "SELECT * FROM eponym")}
        for i in sorted(ep_ref - ep_rows.keys()):
            prob.add("eponym_missing", str(i))
        ep_ids = sorted((i for i in ep_ref if i in ep_rows), key=lambda i: (
            _fold(ep_rows[i]["ru"] or ep_rows[i]["ka"] or ep_rows[i]["en"]), ep_rows[i]["qid"] or "",
            ep_rows[i]["key"] or "", i))
        ep_idx = {eid: j for j, eid in enumerate(ep_ids)}
        cats = defaultdict(list)
        for r in _rows(con, "SELECT * FROM eponym_category ORDER BY eponym_id, rank, category"):
            cats[r["eponym_id"]].append(r)

        # ---------------------------------------------------------- feature -> areas
        fd, fp = defaultdict(list), defaultdict(list)
        for r in _rows(con, "SELECT * FROM feature_area"):
            fid, aid, share = r["feature_id"], r["area_id"], r["share"] if r["share"] is not None else 0.0
            if fid not in f_idx:
                continue
            if aid in d_idx:
                fd[fid].append((-share, d_idx[aid]))
            elif aid in p_idx:
                fp[fid].append((-share, p_idx[aid]))
            else:
                prob.add("feature_area_missing", str(aid))

        # ---------------------------------------------------------- houses
        lm_of_osm = _landmark_osm_index(frows, set(group_codes(kinds, PLACE_GROUP)), f_idx)
        h_by_f: dict[int, list] = defaultdict(list)
        h_by_p: dict[int, list] = defaultdict(list)
        n_house_total = n_house_skipped = n_house_landmark = 0
        for r in _rows(con, "SELECT * FROM house h ORDER BY id"):
            n_house_total += 1
            if r["n"] is None or r["lat"] is None or r["lon"] is None:
                n_house_skipped += 1
                prob.add("house_not_exportable", r["raw"])
                continue
            row: list[Any] = [r["raw"], int(r["n"]), r["lit"] or "", r["sub"] or "", e5(r["lat"]), e5(r["lon"])]
            lm = _house_landmark(r, lm_of_osm, prob)
            if r["n_to"] is not None or r["corp"] or lm is not None:
                row.append(int(r["n_to"]) if r["n_to"] is not None else None)
            if r["corp"] or lm is not None:
                row.append(str(r["corp"]) if r["corp"] else None)
            if lm is not None:
                row.append(lm)                  # 9th element: index of the place at this address
                n_house_landmark += 1
            if r["feature_id"] is not None and r["feature_id"] in f_idx:
                h_by_f[f_idx[r["feature_id"]]].append(row)
            elif r["feature_id"] is None and r["area_id"] in p_idx and (r["match"] in (None, "place")):
                h_by_p[p_idx[r["area_id"]]].append(row)
            else:
                prob.add("house_unattached", f"{r['addr_street'] or ''} {r['raw']}".strip())

        def hsort(rows: list) -> list:
            uniq = {json.dumps(x, ensure_ascii=False): x for x in rows}
            return sorted(uniq.values(), key=lambda x: (x[1], x[2], x[3], x[6] if len(x) > 6 and x[6] is not None else -1,
                                                        x[7] if len(x) > 7 and x[7] is not None else "", x[0], x[4], x[5],
                                                        x[8] if len(x) > 8 else -1))

        houses_h = {str(i): hsort(v) for i, v in sorted(h_by_f.items())}
        houses_p = {str(i): hsort(v) for i, v in sorted(h_by_p.items())}
        n_dup = sum(len(v) for v in h_by_f.values()) + sum(len(v) for v in h_by_p.values()) \
            - sum(len(v) for v in houses_h.values()) - sum(len(v) for v in houses_p.values())
        if n_dup:
            prob.c["house_duplicate_rows"] += n_dup

        # ---------------------------------------------------------- features -> core.f + geom.g
        core_f: list[dict[str, Any]] = []
        geom_g: list[list[str]] = []
        n_old = n_alt = 0
        for i, r in enumerate(frows):
            fid = r["id"]
            g = None
            try:
                g = G.load_wkt(r["geom_wkt"])
            except Exception:
                prob.add("geometry_invalid_wkt", r["ka"])
            e: dict[str, Any] = {"ka": r["ka"]}
            if r["ru"]:
                e["ru"] = r["ru"]
            if r["en"]:
                e["en"] = r["en"]
            e["k"] = fkind[fid]
            if r["ord"] is not None:
                e["o"] = int(r["ord"])
            if r["family_id"] in fam_idx:
                e["fam"] = fam_idx[r["family_id"]]
            if r["parent_id"] is not None:
                if r["parent_id"] in f_idx and r["parent_id"] != fid:
                    e["par"] = f_idx[r["parent_id"]]
                else:
                    prob.add("parent_missing", r["ka"])
            if fd.get(fid):
                e["d"] = [j for _, j in sorted(fd[fid])]
            if fp.get(fid):
                e["p"] = [j for _, j in sorted(fp[fid])]
            if g is not None and not G.has_lines(g):
                length = 0.0                     # points and polygons: len 0 (docs/pack-format.md)
            else:
                length = r["len_m"] if r["len_m"] is not None else G.metric_length(g)
            e["len"] = int(round(length or 0))
            lat, lon = r["lat"], r["lon"]
            if (lat is None or lon is None) and g is not None:
                lat, lon = G.representative_point(g)
            if lat is None or lon is None:
                prob.add("feature_without_point", r["ka"])
                lat, lon = G.LAT0, G.LON0
            e["c"] = [e5(lat), e5(lon)]
            bb = _bbox_e5(r["bbox"])
            if bb is None and g is not None:
                minx, miny, maxx, maxy = g.bounds
                bb = [e5(miny), e5(minx), e5(maxy), e5(maxx)]
            if bb is not None and not (bb[0] == bb[2] and bb[1] == bb[3]):
                e["bb"] = bb

            main = {_fold(r["ka"]), _fold(r["ru"]), _fold(r["en"])}
            alts, seen = [], set(main)
            olds_fn = []
            for nm in sorted(names.get(fid, []), key=lambda x: (ALT_KIND_ORDER.get(x["kind"], 9), x["lang"], x["name"])):
                if nm["kind"] == "old":
                    olds_fn.append(nm)
                    continue
                k = _fold(nm["name"])
                if k and k not in seen:
                    seen.add(k)
                    alts.append(nm["name"])
            if alts:
                e["alt"] = alts
                n_alt += len(alts)

            old = _old_names(hist.get(fid, []), olds_fn, ep_idx)
            if old:
                e["old"] = old
                n_old += len(old)
            e["hn"] = len(houses_h.get(str(i), []))
            if r["wikidata"]:
                qids = [v.strip() for v in str(r["wikidata"]).split(";")]
                good = [v for v in qids if re.fullmatch(r"Q[0-9]+", v)]
                if good:
                    e["q"] = ";".join(good)
                if len(good) != len([v for v in qids if v]):
                    prob.add("feature_bad_wikidata", f"{r['ka']}: {r['wikidata']}")
            code = kinds[fkind[fid]]["code"]
            if code in ("district", "neighbourhood") and r["area_id"] is not None:
                table = d_idx if code == "district" else p_idx
                if r["area_id"] in table:
                    e["ar"] = table[r["area_id"]]
                else:
                    prob.add("area_feature_mismatch", r["ka"])
            core_f.append(e)

            parts = G.feature_parts(g)
            if not parts:
                prob.add("feature_without_geometry", r["ka"])
                parts = [[(e["c"][0], e["c"][1])]]
            geom_g.append(G.encode_parts(parts))

        # ---------------------------------------------------------- families -> core.fam
        core_fam = []
        for fid in fam_ids:
            fr = fam_rows[fid]
            e = {}
            if fr["base_ka"]:
                e["ka"] = fr["base_ka"]
            if fr["base_ru"]:
                e["ru"] = fr["base_ru"]
            if fr["eponym_id"] in ep_idx:
                e["e"] = ep_idx[fr["eponym_id"]]
            core_fam.append(e)

        # ---------------------------------------------------------- crossings, near pairs
        xs = []
        for r in _rows(con, "SELECT * FROM crossing"):
            if r["a"] not in f_idx or r["b"] not in f_idx or r["a"] == r["b"]:
                prob.add("crossing_bad_ref", f"{r['a']}-{r['b']}")
                continue
            a, b, da, db = f_idx[r["a"]], f_idx[r["b"]], r["dir_a"] or 0, r["dir_b"] or 0
            if a > b:
                a, b, da, db = b, a, db, da
            xs.append((a, b, e5(r["lat"]), e5(r["lon"]), da & 0xFF, db & 0xFF))
        xs = sorted(set(xs))
        x, xp, xd = [], [], []
        for a, b, la, lo, da, db in xs:
            x += [a, b]
            xp += [la, lo]
            xd += [da, db]
        near_set = set()
        for r in _rows(con, "SELECT a, b FROM near_pair"):
            if r["a"] in f_idx and r["b"] in f_idx and r["a"] != r["b"]:
                a, b = sorted((f_idx[r["a"]], f_idx[r["b"]]))
                near_set.add((a, b))
        near = [v for ab in sorted(near_set) for v in ab]

        # ---------------------------------------------------------- eponyms.e
        eps = []
        n_ai = 0
        for eid in ep_ids:
            er = ep_rows[eid]
            e = {k2: er[k1] for k1, k2 in (("ka", "ka"), ("ru", "ru"), ("en", "en"), ("qid", "q")) if er[k1]}
            e["k"] = er["kind"]
            cl = []
            for c in cats.get(eid, []):
                if c["category"] in tax_ids:
                    if c["category"] not in cl:
                        cl.append(c["category"])
                else:
                    prob.add("category_not_in_taxonomy", c["category"])
            if cl:
                e["cat"] = cl
            occ = _json_list(er["occupations_ru"], prob)
            if occ:
                e["occ"] = occ
            gcode = GENDER_CODES.get((er["gender"] or "").lower())
            if gcode:
                e["g"] = gcode
            if er["birth_year"] is not None:
                e["b"] = int(er["birth_year"])
            if er["death_year"] is not None:
                e["d"] = int(er["death_year"])
            ocode = ORIGIN_CODES.get(er["origin"] or "")
            if ocode:
                e["o"] = ocode
            if er["short_ru"]:
                e["desc"] = er["short_ru"]
            e["src"] = er["source"]
            if er["confidence"] is not None:
                e["conf"] = round(float(er["confidence"]), 2)
            ai = int(er["source"] == "llm" or any(c["source"] == "llm" for c in cats.get(eid, [])))
            e["ai"] = ai
            n_ai += ai
            eps.append(e)

        # ---------------------------------------------------------- assemble files
        core = {"v": PACK_FORMAT_VERSION, "kinds": kinds, "districts": core_d, "places": core_p, "f": core_f,
                "fam": core_fam, "x": x, "xp": xp, "xd": xd, "near": near}
        geom = {"v": PACK_FORMAT_VERSION, "prec": 5, "g": geom_g}
        houses = {"v": PACK_FORMAT_VERSION, "h": houses_h, "place": houses_p}
        areas = {"v": PACK_FORMAT_VERSION, "districts": areas_d, "places": areas_p}
        eponyms = {"v": PACK_FORMAT_VERSION, "taxonomy": tax, "kinds": ep_kinds, "e": eps}
        payloads = {"core.json": dumps(core), "geom.json": dumps(geom), "houses.json": dumps(houses),
                    "areas.json": dumps(areas), "eponyms.json": dumps(eponyms)}

        files = {}
        total_raw = total_gz = 0
        for name, data in payloads.items():
            _atomic_write(out_dir / name, data)
            gz = len(gzip.compress(data, 9))
            files[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            total_raw += len(data)
            total_gz += gz
            log.info("pack %-13s raw %9.1f KB  gzip %8.1f KB", name, len(data) / 1024, gz / 1024)
        log.info("pack TOTAL         raw %9.1f KB  gzip %8.1f KB", total_raw / 1024, total_gz / 1024)

        street_codes = {k["code"] for k in kinds if k["group"] in ("street*", "lane*")}
        n_streets = sum(1 for e in core_f if kinds[e["k"]]["code"] in street_codes)
        n_houses = sum(len(v) for v in houses_h.values()) + sum(len(v) for v in houses_p.values())
        counts = {"features": n, "streets": n_streets, "houses": n_houses, "crossings": len(x) // 2,
                  "eponyms": len(eps), "families": len(core_fam), "districts": len(core_d), "places": len(core_p),
                  "near": len(near) // 2, "old_names": n_old, "alt_names": n_alt, "eponyms_ai": n_ai}

        manifest: dict[str, Any] = {"v": PACK_FORMAT_VERSION, "version": _next_version(out_dir, now),
                                    "built": now.strftime("%d/%m/%Y")}
        osm = get_meta(con, "osm_snapshot")
        if osm:
            manifest["osm"] = osm
        else:
            log.warning("meta.osm_snapshot is not set; manifest.osm omitted")
        manifest["files"] = files
        tiles, tiles_attr = _tiles(tiles_manifest)
        if tiles:
            manifest["tiles"] = tiles
        manifest["counts"] = counts
        manifest["attribution"] = _attribution(con, (tiles_attr or "") if tiles else None)
        _atomic_write(out_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"))

        stats = {"version": manifest["version"], "built": manifest["built"], "taxonomy_source": tax_src,
                 "houses_db_total": n_house_total, "houses_skipped": n_house_skipped,
                 "houses_landmark": n_house_landmark,
                 "sizes": {k: {"raw": len(v), "gzip": len(gzip.compress(v, 9))} for k, v in payloads.items()},
                 "problems": dict(prob.c), "problem_samples": dict(prob.samples)}
        for k, v in sorted(prob.c.items()):
            log.warning("export problem %s: %d (e.g. %s)", k, v, prob.samples.get(k, [])[:3])
        if stats_path:
            Path(stats_path).parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(Path(stats_path), json.dumps(stats, ensure_ascii=False, indent=1).encode("utf-8"))
        st.update(counts)
        st["gzip_kb"] = round(total_gz / 1024, 1)
    return {"manifest": manifest, "stats": stats}


def _landmark_osm_index(frows: list, place_codes: set[str], f_idx: dict[int, int]) -> dict[tuple[str, int], int]:
    """(osm type, osm id) -> pack index of the landmark feature (kind group ``place*``) made of it."""
    out: dict[tuple[str, int], int] = {}
    for r in frows:
        if r["kind"] not in place_codes or not r["osm_ids"]:
            continue
        try:
            refs = json.loads(r["osm_ids"])
        except (TypeError, ValueError):
            continue
        for ref in refs if isinstance(refs, list) else []:
            m = _OSM_REF.match(str(ref))
            if m:
                out.setdefault((m.group(1), int(m.group(2))), f_idx[r["id"]])
    return out


def _house_landmark(r: sqlite3.Row, lm_of_osm: dict[tuple[str, int], int], prob: _Problems) -> int | None:
    """Pack index of the place whose OSM object carries this address (house flag ``landmark:<kind>``)."""
    if "landmark:" not in (r["flags"] or ""):
        return None
    t, oid = r["osm_type"], r["osm_id"]
    lm = lm_of_osm.get((str(t)[:1], int(oid))) if t and oid is not None else None
    if lm is None:
        prob.add("house_landmark_unresolved", f"{r['addr_street'] or ''} {r['raw']}".strip())
    return lm


def _old_names(hrows: list, fn_rows: list, ep_idx: dict[int, int]) -> list[dict[str, Any]]:
    """Merge name_history rows and feature_name(kind=old) into ``old[]``, newest first."""
    merged: dict[tuple, dict[str, Any]] = {}
    order: list[tuple] = []

    def nyears(d):
        return (d.get("from") is not None) + (d.get("to") is not None)

    for r in hrows:
        d: dict[str, Any] = {}
        for col, k in (("name_ka", "ka"), ("name_ru", "ru"), ("name_lat", "lat")):
            if r[col]:
                d[k] = r[col]
        if not d:
            continue
        if r["from_year"] is not None:
            d["from"] = int(r["from_year"])
        if r["to_year"] is not None:
            d["to"] = int(r["to_year"])
        if r["eponym_id"] in ep_idx:
            d["e"] = ep_idx[r["eponym_id"]]
        d["src"] = r["source"]
        key = (_fold(d.get("ka")), _fold(d.get("ru")), _fold(d.get("lat")))
        if key not in merged:
            merged[key] = d
            order.append(key)
        else:
            cur = merged[key]
            best, other = (d, cur) if nyears(d) > nyears(cur) else (cur, d)
            for k, v in other.items():
                best.setdefault(k, v)
            merged[key] = best
    known = {v for key in order for v in key if v}
    for r in fn_rows:
        nm = _fold(r["name"])
        if not nm or nm in known:
            continue
        lang = {"ka": "ka", "ru": "ru"}.get(r["lang"], "lat")
        d = {lang: r["name"], "src": r["source"]}
        key = (_fold(d.get("ka")), _fold(d.get("ru")), _fold(d.get("lat")))
        known.add(nm)
        merged[key] = d
        order.append(key)
    items = [merged[k] for k in order]

    def newest(d):
        y = d.get("to", d.get("from"))
        return (0, -y) if y is not None else (1, 0)

    return sorted(items, key=newest)  # stable: same-year entries keep insertion order


def _json_list(text: str | None, prob: _Problems) -> list[str]:
    if not text:
        return []
    try:
        v = json.loads(text)
    except ValueError:
        prob.add("eponym_occupations_not_json", text[:40])
        return [s.strip() for s in text.split(",") if s.strip()]
    if isinstance(v, str):
        return [v]
    return [str(s) for s in v if s] if isinstance(v, list) else []


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _next_version(out_dir: Path, now: dt.datetime) -> str:
    day = now.strftime("%Y%m%d")
    seq = 1
    try:
        prev = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8")).get("version", "")
        pday, _, pseq = prev.partition("-")
        if pday == day and pseq.isdigit():
            seq = int(pseq) + 1
    except (OSError, ValueError):
        pass
    return f"{day}-{seq}"


TILES_PREFIX = "tiles/"
TILES_EXCLUDE = {"tbilisi-center.pmtiles"}       # being moved out of app/public
TILES_INFO_KEYS = ("minzoom", "maxzoom", "bbox", "center", "tileset_version", "source_build", "built")


def _tiles(path: Path | str | None) -> tuple[dict[str, Any], str | None]:
    """``manifest.tiles`` from ``app/public/tiles/tiles-manifest.json`` (basemap stage).

    Result shape (paths relative to the app root, i.e. ``app/public``)::

        {"basemap": "tiles/tbilisi.pmtiles", "glyphs": "tiles/fonts/{fontstack}/{range}.pbf",
         "sprites": {"light": "tiles/sprites/light", ...}, "minzoom": 0, "maxzoom": 15, "bbox": [...],
         "center": [...], "bytes": <sum of files>, "files": {"tiles/tbilisi.pmtiles": {"bytes", "sha256"}, ...}}

    Returns ``(tiles, attribution_line)``; ``({}, None)`` when there is no usable tiles manifest.
    """
    if not path:
        return {}, None
    path = Path(path)
    if not path.exists():
        log.info("no tiles manifest at %s; manifest.tiles omitted", path)
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        log.exception("tiles manifest %s is not JSON; ignored", path)
        return {}, None
    if not isinstance(data, dict):
        log.warning("tiles manifest %s is not an object; ignored", path)
        return {}, None
    files = data.get("files", {})
    if isinstance(files, list):
        files = {f.get("path") or f.get("name"): f for f in files if isinstance(f, dict)}

    def pref(p: str) -> str:
        p = str(p).replace("\\", "/").lstrip("/")
        return p if p.startswith(TILES_PREFIX) else TILES_PREFIX + p

    out_files: dict[str, Any] = {}
    for name, meta in sorted(files.items()):
        if Path(str(name)).name in TILES_EXCLUDE:
            continue
        if isinstance(meta, dict) and "sha256" in meta and ("bytes" in meta or "size" in meta):
            out_files[pref(name)] = {"bytes": int(meta.get("bytes", meta.get("size"))), "sha256": str(meta["sha256"])}
    if not out_files:
        log.warning("tiles manifest %s has no {path: {bytes, sha256}} entries; ignored", path)
        return {}, None
    tiles: dict[str, Any] = {}
    basemap = data.get("basemap")
    if basemap and Path(str(basemap)).name not in TILES_EXCLUDE:
        tiles["basemap"] = pref(basemap)
    if data.get("glyphs"):
        tiles["glyphs"] = pref(data["glyphs"])
    if isinstance(data.get("sprites"), dict):
        tiles["sprites"] = {k: pref(v) for k, v in data["sprites"].items()}
    for k in TILES_INFO_KEYS:
        if data.get(k) is not None:
            tiles[k] = data[k]
    tiles["bytes"] = sum(f["bytes"] for f in out_files.values())
    tiles["files"] = out_files
    if "basemap" in tiles and tiles["basemap"] not in out_files:
        log.warning("tiles: basemap %s is not listed in files", tiles["basemap"])
    log.info("tiles: %d files, %.1f MB from %s", len(out_files), tiles["bytes"] / 1e6, path)
    attr = data.get("attribution")
    if isinstance(attr, list):
        attr = "; ".join(str(a) for a in attr)
    return tiles, (str(attr) if attr else None)


def _attribution(con: sqlite3.Connection, tiles_attr: str | None) -> list[str]:
    out = ["© OpenStreetMap contributors, ODbL"]
    n_wd = con.execute(
        "SELECT COUNT(*) FROM eponym WHERE qid IS NOT NULL OR source LIKE 'wd%'").fetchone()[0]
    if n_wd:
        out.append("Wikidata, CC0")
    srcs = {r[0] for r in con.execute("SELECT DISTINCT source FROM name_history")}
    if srcs & {"kawiki", "ruwiki"}:
        out.append("Википедия (факты: годы и прежние названия), CC BY-SA")
    if any(s.startswith("enc2008") for s in srcs):
        out.append("Энциклопедия «Тбилиси», 2008 (факты: годы и прежние названия)")
    if tiles_attr is not None:
        out.append(f"Подложка: {tiles_attr or 'Protomaps © OpenStreetMap contributors, ODbL'}")
    return out
