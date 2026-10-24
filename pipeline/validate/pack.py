"""Pack invariants: manifest hashes, JSON schemas, index ranges, crossing pairs, houses.

:func:`validate_pack` never raises on bad data; it returns a :class:`PackCheck` with ``errors``
(the pack must not ship), ``warnings`` (suspicious but usable) and ``stats`` for the report.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon

from pipeline.export import geom as G
from pipeline.export.polyline import decode_ints
from pipeline.log import get_logger

log = get_logger(__name__)

SCHEMAS = Path(__file__).parent / "schemas"
DATA_FILES = ("core.json", "geom.json", "houses.json", "areas.json", "eponyms.json")
MAX_SCHEMA_ERRORS = 20
# Generous Tbilisi envelope in e5 (lat 41.55..41.95, lon 44.50..45.20).
BBOX_E5 = (4155000, 4450000, 4195000, 4520000)
CROSSING_MAX_DIST_M = 30.0     # crossing point -> each street's geometry
HOUSE_FAR_M = 300.0            # house point -> its street's geometry
SAMPLES = 15


@dataclass
class PackCheck:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    pack: dict[str, Any] = field(default_factory=dict)  # parsed files by name (without .json)

    @property
    def ok(self) -> bool:
        return not self.errors

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def load_schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))


def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    """Inline local ``#/$defs/...`` references (2x faster validation on 40k house rows)."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            target = _inline_refs(defs[ref[len("#/$defs/"):]], defs)
            rest = {k: _inline_refs(v, defs) for k, v in node.items() if k != "$ref"}
            return {"allOf": [target], **rest} if rest else target
        return {k: _inline_refs(v, defs) for k, v in node.items() if k != "$defs"}
    if isinstance(node, list):
        return [_inline_refs(v, defs) for v in node]
    return node


def _schema_check(res: PackCheck, name: str, obj: Any) -> None:
    schema = load_schema(name)
    validator = jsonschema.Draft202012Validator(_inline_refs(schema, schema.get("$defs", {})))
    errs = sorted(validator.iter_errors(obj), key=lambda e: list(e.absolute_path))
    for e in errs[:MAX_SCHEMA_ERRORS]:
        path = "/".join(str(p) for p in e.absolute_path) or "(root)"
        res.err(f"schema {name}.json: {path}: {e.message[:200]}")
    if len(errs) > MAX_SCHEMA_ERRORS:
        res.err(f"schema {name}.json: ... and {len(errs) - MAX_SCHEMA_ERRORS} more schema errors")


def _in_bbox(lat: int, lon: int) -> bool:
    return BBOX_E5[0] <= lat <= BBOX_E5[2] and BBOX_E5[1] <= lon <= BBOX_E5[3]


def _idx_ok(v: Any, n: int) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v < n


def validate_pack(pack_dir: Path | str) -> PackCheck:
    """Check the pack in ``pack_dir`` (manifest.json + data files)."""
    pack_dir = Path(pack_dir)
    res = PackCheck()

    # ------------------------------------------------------------------ manifest + hashes
    mpath = pack_dir / "manifest.json"
    if not mpath.exists():
        res.err(f"manifest.json missing in {pack_dir}")
        return res
    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except ValueError as exc:
        res.err(f"manifest.json is not valid JSON: {exc}")
        return res
    _schema_check(res, "manifest", manifest)
    res.pack["manifest"] = manifest
    files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    sizes = {}
    for name in DATA_FILES:
        p = pack_dir / name
        if not p.exists():
            res.err(f"{name} missing")
            continue
        data = p.read_bytes()
        sizes[name] = len(data)
        meta = files.get(name) or {}
        if meta.get("bytes") != len(data):
            res.err(f"{name}: size {len(data)} != manifest bytes {meta.get('bytes')}")
        if meta.get("sha256") != hashlib.sha256(data).hexdigest():
            res.err(f"{name}: sha256 does not match manifest")
        try:
            obj = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            res.err(f"{name}: not valid UTF-8 JSON: {exc}")
            continue
        _schema_check(res, name[:-5], obj)
        res.pack[name[:-5]] = obj
    res.stats["sizes"] = sizes
    _tiles_check(res, manifest, pack_dir.parent)
    if not all(k in res.pack for k in ("core", "geom", "houses", "areas", "eponyms")):
        return res
    try:
        _invariants(res)
    except Exception as exc:  # malformed data that slipped past the schema
        log.exception("invariant check crashed")
        res.err(f"invariant check crashed: {type(exc).__name__}: {exc}")
    log.info("validate_pack: %d errors, %d warnings", len(res.errors), len(res.warnings))
    return res


def _tiles_check(res: PackCheck, manifest: dict[str, Any], app_root: Path) -> None:
    """``manifest.tiles.files`` paths are relative to the app root (``app/public``)."""
    tiles = manifest.get("tiles")
    if not isinstance(tiles, dict):
        return
    files = tiles.get("files") if isinstance(tiles.get("files"), dict) else {}
    missing, bad = [], []
    for rel, meta in files.items():
        p = app_root / rel
        if not p.is_file():
            missing.append(rel)
            continue
        if not isinstance(meta, dict) or p.stat().st_size != meta.get("bytes"):
            bad.append(rel)
            continue
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != meta.get("sha256"):
            bad.append(rel)
    if missing:
        res.err(f"tiles: {len(missing)} files listed in manifest.tiles are missing (e.g. {missing[:3]})")
    if bad:
        res.err(f"tiles: {len(bad)} files differ from manifest.tiles bytes/sha256 (e.g. {bad[:3]})")
    if tiles.get("basemap") and tiles["basemap"] not in files:
        res.err(f"tiles: basemap {tiles['basemap']} is not listed in manifest.tiles.files")
    if isinstance(tiles.get("bytes"), int) and tiles["bytes"] != sum(
            m.get("bytes", 0) for m in files.values() if isinstance(m, dict)):
        res.err("tiles: manifest.tiles.bytes is not the sum of its files")
    res.stats["tiles_files"] = len(files)
    res.stats["tiles_bytes"] = tiles.get("bytes")


def _invariants(res: PackCheck) -> None:
    core, geom, houses, areas, eps = (res.pack[k] for k in ("core", "geom", "houses", "areas", "eponyms"))
    manifest = res.pack["manifest"]
    kinds, F, fam, E = core["kinds"], core["f"], core["fam"], eps["e"]
    n, nk, nfam, ne = len(F), len(kinds), len(fam), len(E)
    nd, npl = len(core["districts"]), len(core["places"])
    codes = [k["code"] for k in kinds]
    if len(set(codes)) != len(codes):
        res.err("core.kinds: duplicate codes")
    for need in ("district", "neighbourhood", "other"):
        if need not in codes:
            res.err(f"core.kinds: missing kind {need!r}")

    # ------------------------------------------------------------------ features
    bad: dict[str, list[str]] = {}

    def flag(key: str, sample: str) -> None:
        bad.setdefault(key, []).append(sample)

    for i, f in enumerate(F):
        if not _idx_ok(f.get("k"), nk):
            flag("k out of range", str(i))
            continue
        code = codes[f["k"]]
        if "fam" in f and not _idx_ok(f["fam"], nfam):
            flag("fam out of range", str(i))
        if "par" in f and (not _idx_ok(f["par"], n) or f["par"] == i):
            flag("par out of range or self", str(i))
        for j in f.get("d", []):
            if not _idx_ok(j, nd):
                flag("d out of range", str(i))
        for j in f.get("p", []):
            if not _idx_ok(j, npl):
                flag("p out of range", str(i))
        for o in f.get("old", []):
            if "e" in o and not _idx_ok(o["e"], ne):
                flag("old[].e out of range", str(i))
        if f.get("ar") is not None:
            lim = nd if code == "district" else npl if code == "neighbourhood" else -1
            if lim < 0:
                flag("ar on a kind other than district/neighbourhood", str(i))
            elif not _idx_ok(f["ar"], lim):
                flag("ar out of range", str(i))
        c = f.get("c", [0, 0])
        if not _in_bbox(*c):
            res.warn(f"core.f[{i}] ({f.get('ka')}): point {c} outside Tbilisi envelope")
        bb = f.get("bb")
        if bb and (bb[0] > bb[2] or bb[1] > bb[3]):
            flag("bb min > max", str(i))
    # parent chains must not loop
    for i, f in enumerate(F):
        seen, j = set(), i
        while j is not None and j not in seen:
            seen.add(j)
            j = F[j].get("par") if _idx_ok(F[j].get("par"), n) else None
        if j is not None:
            flag("par cycle", str(i))
            break
    for j, fm in enumerate(fam):
        if "e" in fm and not _idx_ok(fm["e"], ne):
            flag("fam[].e out of range", str(j))
    for j, p in enumerate(core["places"]):
        if "parent" in p and not _idx_ok(p["parent"], nd):
            flag("places[].parent out of range", str(j))
    for key, samples in bad.items():
        res.err(f"core: {key}: {len(samples)} (e.g. {samples[:5]})")

    # ------------------------------------------------------------------ crossings / near
    x, xp, xd = core["x"], core["xp"], core["xd"]
    if len(x) % 2:
        res.err("core.x has odd length")
    if len(xp) != len(x) or len(xd) != len(x):
        res.err(f"core.x/xp/xd lengths differ: {len(x)}/{len(xp)}/{len(xd)}")
    pairs_bad = [t for t in range(len(x) // 2) if not (_idx_ok(x[2 * t], n) and _idx_ok(x[2 * t + 1], n)
                                                       and x[2 * t] < x[2 * t + 1])]
    if pairs_bad:
        res.err(f"core.x: {len(pairs_bad)} pairs out of range or not a < b (e.g. t={pairs_bad[:5]})")
    seen_pts = set()
    dup = 0
    for t in range(min(len(x), len(xp)) // 2):
        k = (x[2 * t], x[2 * t + 1], xp[2 * t], xp[2 * t + 1])
        dup += k in seen_pts
        seen_pts.add(k)
    if dup:
        res.warn(f"core.x: {dup} duplicate crossings (same pair and point)")
    near = core["near"]
    if len(near) % 2:
        res.err("core.near has odd length")
    nb = [t for t in range(len(near) // 2) if not (_idx_ok(near[2 * t], n) and _idx_ok(near[2 * t + 1], n)
                                                   and near[2 * t] < near[2 * t + 1])]
    if nb:
        res.err(f"core.near: {len(nb)} pairs out of range or not a < b")

    # ------------------------------------------------------------------ geometry
    g = geom["g"]
    if len(g) != n:
        res.err(f"geom.g has {len(g)} entries, core.f has {n}")
    shapes: list[Any] = [None] * n
    n_vert = n_out = 0
    for i, parts in enumerate(g[:n]):
        geoms = []
        for s in parts:
            try:
                pts = decode_ints(s)
            except ValueError as exc:
                res.err(f"geom.g[{i}]: undecodable polyline: {exc}")
                continue
            n_vert += len(pts)
            n_out += sum(1 for p in pts if not _in_bbox(*p))
            xy = [(p[1] / 1e5, p[0] / 1e5) for p in pts]
            if len(xy) == 1:
                geoms.append(Point(xy[0]))
            elif len(xy) >= 4 and xy[0] == xy[-1] and codes[F[i]["k"]] in ("square", "garden", "park", "district",
                                                                         "neighbourhood", "lake"):
                geoms.append(Polygon(xy))
            else:
                geoms.append(LineString(xy))
        if geoms:
            shapes[i] = G.to_metric(shapely.union_all(geoms) if len(geoms) > 1 else geoms[0])
    if n_out:
        res.warn(f"geom: {n_out} of {n_vert} vertices outside the Tbilisi envelope")
    res.stats["geom_vertices"] = n_vert

    # crossing point close to both geometries (catches index re-mapping bugs)
    far = []
    for t in range(min(len(x), len(xp)) // 2):
        a, b = x[2 * t], x[2 * t + 1]
        if not (_idx_ok(a, n) and _idx_ok(b, n)) or shapes[a] is None or shapes[b] is None:
            continue
        pt = G.to_metric(Point(xp[2 * t + 1] / 1e5, xp[2 * t] / 1e5))
        da, db = shapes[a].distance(pt), shapes[b].distance(pt)
        if max(da, db) > CROSSING_MAX_DIST_M:
            far.append((max(da, db), t, F[a].get("ka"), F[b].get("ka")))
    far.sort(reverse=True)
    if far:
        res.warn(f"crossings: {len(far)} points farther than {CROSSING_MAX_DIST_M:.0f} m from a street "
                 f"(worst {far[0][0]:.0f} m: {far[0][2]} x {far[0][3]})")
    res.stats["crossings_far"] = [{"dist": round(d), "t": t, "a": a, "b": b} for d, t, a, b in far[:SAMPLES]]

    # ------------------------------------------------------------------ houses
    h, hp = houses["h"], houses["place"]
    nh = 0
    bad_keys = [k for k in h if not _idx_ok(int(k), n)]
    if bad_keys:
        res.err(f"houses.h: {len(bad_keys)} keys are not feature indices (e.g. {bad_keys[:5]})")
    bad_pkeys = [k for k in hp if not _idx_ok(int(k), npl)]
    if bad_pkeys:
        res.err(f"houses.place: {len(bad_pkeys)} keys are not place indices (e.g. {bad_pkeys[:5]})")
    hn_bad = [i for i, f in enumerate(F) if f.get("hn", 0) != len(h.get(str(i), []))]
    if hn_bad:
        res.err(f"core.f[].hn differs from houses.h row count for {len(hn_bad)} features (e.g. {hn_bad[:5]})")
    range_bad = out_box = 0
    lm_bad: list[str] = []
    n_lm = 0
    place_kinds = {i for i, k in enumerate(kinds) if k.get("group") == "place*"}
    house_far: list[tuple[float, str, str]] = []
    pts_idx, pts = [], []
    for k, rows in list(h.items()) + list(hp.items()):
        for r in rows:
            if len(r) > 8:
                n_lm += 1
                lm = r[8]
                if not (_idx_ok(lm, n) and F[lm].get("k") in place_kinds):
                    lm_bad.append(f"{k}:{r[0]}->{lm}")
    if lm_bad:
        res.err(f"houses: {len(lm_bad)} rows whose 9th element is not a landmark (place*) feature "
                f"(e.g. {lm_bad[:5]})")
    res.stats["houses_landmark"] = n_lm
    for k, rows in h.items():
        nh += len(rows)
        for r in rows:
            if len(r) > 6 and r[6] is not None and r[6] <= r[1]:
                range_bad += 1
            if not _in_bbox(r[4], r[5]):
                out_box += 1
            if _idx_ok(int(k), n) and shapes[int(k)] is not None:
                pts_idx.append(int(k))
                pts.append((r[5] / 1e5, r[4] / 1e5, r[0]))
    for rows in hp.values():
        nh += len(rows)
    if range_bad:
        res.warn(f"houses: {range_bad} rows with n_to <= n")
    if out_box:
        res.warn(f"houses: {out_box} rows outside the Tbilisi envelope")
    if pts:
        P = G.to_metric(shapely.points(np.array([(p[0], p[1]) for p in pts])))
        S = np.array([shapes[i] for i in pts_idx], dtype=object)
        D = shapely.distance(P, S)
        for j in np.argsort(-D)[: max(SAMPLES, 1)]:
            if D[j] > HOUSE_FAR_M:
                house_far.append((float(D[j]), F[pts_idx[j]].get("ka"), pts[j][2]))
        res.stats["houses_far_count"] = int((D > HOUSE_FAR_M).sum())
        res.stats["houses_dist_p95"] = round(float(np.percentile(D, 95)), 1)
        if res.stats["houses_far_count"]:
            res.warn(f"houses: {res.stats['houses_far_count']} rows farther than {HOUSE_FAR_M:.0f} m "
                     f"from their street")
    res.stats["houses_far"] = [{"dist": round(d), "street": s, "raw": r} for d, s, r in house_far]

    # ------------------------------------------------------------------ areas
    if len(areas["districts"]) != nd:
        res.err(f"areas.districts has {len(areas['districts'])} entries, core.districts has {nd}")
    if len(areas["places"]) != npl:
        res.err(f"areas.places has {len(areas['places'])} entries, core.places has {npl}")
    for name in ("districts", "places"):
        for i, a in enumerate(areas[name]):
            for poly in a["poly"]:
                for ring in poly:
                    try:
                        pts_r = decode_ints(ring)
                    except ValueError as exc:
                        res.err(f"areas.{name}[{i}]: undecodable ring: {exc}")
                        continue
                    if len(pts_r) < 4 or pts_r[0] != pts_r[-1]:
                        res.err(f"areas.{name}[{i}]: ring not closed or < 4 points")
    no_poly = sum(1 for a in areas["places"] if not a["poly"])
    no_c = [i for i, p in enumerate(core["places"]) if not areas["places"][i]["poly"] and "c" not in p] \
        if len(areas["places"]) == npl else []
    if no_c:
        res.err(f"core.places: {len(no_c)} places have neither a polygon nor a point")
    res.stats["places_without_polygon"] = no_poly

    # ------------------------------------------------------------------ eponyms
    tax_ids = set()
    for grp in eps["taxonomy"]:
        for node in [grp] + grp.get("children", []):
            if node["id"] in tax_ids:
                res.err(f"eponyms.taxonomy: duplicate id {node['id']}")
            tax_ids.add(node["id"])
            if not node.get("aliases"):
                res.warn(f"eponyms.taxonomy: {node['id']} has no aliases")
    unknown_cat = sorted({c for e in E for c in e.get("cat", []) if c not in tax_ids})
    if unknown_cat:
        res.err(f"eponyms.e: categories not in taxonomy: {unknown_cat[:10]}")
    for e in E:
        if "b" in e and "d" in e and e["d"] < e["b"]:
            res.warn(f"eponyms.e: death before birth: {e.get('ru') or e.get('ka')}")

    # ------------------------------------------------------------------ manifest counts
    street_codes = {k["code"] for k in kinds if k["group"] in ("street*", "lane*")}
    actual = {"features": n, "streets": sum(1 for f in F if _idx_ok(f.get("k"), nk) and codes[f["k"]] in street_codes),
              "houses": nh, "crossings": len(x) // 2, "eponyms": ne}
    for k, v in actual.items():
        if manifest.get("counts", {}).get(k) != v:
            res.err(f"manifest.counts.{k} = {manifest.get('counts', {}).get(k)} but pack has {v}")
    res.stats["actual_counts"] = actual
