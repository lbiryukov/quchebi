"""Coverage report ``data/build/report.html`` (Russian, self-contained, no external resources).

Sections: pack summary and sizes, counts with diff against the previous build
(``data/build/last_counts.json``), coverage by kind and by district, eponym coverage, golden counts,
top problems (validation, export, database: unattached addresses, mismatches).
"""
from __future__ import annotations

import datetime as dt
import gzip
import html
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from pipeline.log import get_logger
from pipeline.validate.golden import QUERIES
from pipeline.validate.pack import PackCheck

log = get_logger(__name__)

GZIP_BUDGET_KB = 600  # plan: pack <= 600 KB gzip
TOP = 20


def esc(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def pct(a: int, b: int) -> str:
    return f"{100.0 * a / b:.1f}%" if b else "—"


def fmt_int(v: Any) -> str:
    return f"{v:,}".replace(",", " ") if isinstance(v, int) else esc(v)


def bar(a: int, b: int) -> str:
    w = 100.0 * a / b if b else 0.0
    return (f'<span class="bar"><span style="width:{w:.1f}%"></span></span>'
            f'<span class="pc">{pct(a, b)}</span>')


# --------------------------------------------------------------------------- metrics from the pack
def pack_metrics(res: PackCheck) -> dict[str, Any]:
    core, houses, eps = res.pack["core"], res.pack["houses"], res.pack["eponyms"]
    kinds, F, fam, E = core["kinds"], core["f"], core["fam"], eps["e"]

    def ep(f):
        j = f.get("fam")
        return E[fam[j]["e"]] if j is not None and j < len(fam) and "e" in fam[j] and fam[j]["e"] < len(E) else None

    by_kind: dict[str, Counter] = {}
    for f in F:
        k = kinds[f["k"]]
        c = by_kind.setdefault(k["code"], Counter())
        e = ep(f)
        c["n"] += 1
        c["ru"] += "ru" in f
        c["houses"] += f.get("hn", 0) > 0
        c["fam"] += "fam" in f
        c["ep"] += e is not None
        c["cat"] += bool(e and e.get("cat"))
        c["ai"] += bool(e and e.get("ai"))
        c["old"] += "old" in f
    by_district = []
    for j, d in enumerate(core["districts"]):
        c = Counter()
        for f in F:
            if j in f.get("d", []):
                c["n"] += 1
                c["streets"] += kinds[f["k"]]["group"] in ("street*", "lane*")
                c["hn"] += f.get("hn", 0)
                c["ep"] += ep(f) is not None
        by_district.append((d.get("ru") or d.get("ka") or f"#{j}", c))
    no_district = sum(1 for f in F if not f.get("d"))
    streets = [f for f in F if kinds[f["k"]]["group"] in ("street*", "lane*")]
    person_fams = [fm for fm in fam if "e" in fm and E[fm["e"]].get("k") in ("person", "persons_multi")]
    tot = {
        "features": len(F), "ru": sum("ru" in f for f in F), "streets": len(streets),
        "streets_houses": sum(f.get("hn", 0) > 0 for f in streets),
        "ep": sum(ep(f) is not None for f in F), "cat": sum(bool((ep(f) or {}).get("cat")) for f in F),
        "ai": sum(bool((ep(f) or {}).get("ai")) for f in F),
        "families": len(fam), "families_ep": sum("e" in fm for fm in fam),
        "person_families": len(person_fams),
        "person_families_cat": sum(bool(E[fm["e"]].get("cat")) for fm in person_fams),
        "eponyms": len(E), "eponyms_ai": sum(e.get("ai", 0) for e in E),
        "eponyms_cat": sum(bool(e.get("cat")) for e in E),
        "old": sum(len(f.get("old", [])) for f in F), "with_old": sum("old" in f for f in F),
        "houses_place": sum(len(v) for v in houses["place"].values()), "no_district": no_district,
    }
    cat_counts = Counter(c for e in E for c in e.get("cat", []))
    return {"by_kind": by_kind, "by_district": by_district, "tot": tot, "cat_counts": cat_counts,
            "kind_ru": {k["code"]: k["ru"] for k in kinds}}


# --------------------------------------------------------------------------- problems from the DB
def _kind_ka_words(kinds: list[dict[str, Any]]) -> dict[str, tuple[str, ...]]:
    """Georgian type words per kind code (all of them, e.g. garden: სკვერი and ბაღი)."""
    words = {k["code"]: (k["ka"],) if k.get("ka") else () for k in kinds}
    words["garden"] = ("სკვერი", "ბაღი")
    try:
        from pipeline.names.parse import KIND_TABLE  # type: ignore[import-not-found]
        for e in KIND_TABLE:
            code, ka = (e.code, e.ka) if hasattr(e, "code") else (e[0], e[1])
            words[code] = tuple(ka) if isinstance(ka, (list, tuple)) else ((ka,) if ka else ())
    except Exception:  # names stage not available: the core.kinds words are enough
        pass
    return words


def db_problems(con: sqlite3.Connection, kinds: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    q = lambda sql, *a: con.execute(sql, a).fetchall()  # noqa: E731
    out["house_total"] = q("SELECT COUNT(*) FROM house")[0][0]
    out["house_attached"] = q("SELECT COUNT(*) FROM house WHERE feature_id IS NOT NULL")[0][0]
    out["house_place"] = q("SELECT COUNT(*) FROM house WHERE feature_id IS NULL AND match = 'place'")[0][0]
    out["house_match"] = [(r[0] or "—", r[1]) for r in q(
        "SELECT match, COUNT(*) FROM house GROUP BY match ORDER BY 2 DESC")]
    out["unattached_top"] = [(r[0] or "(без addr:street)", r[1]) for r in q(
        "SELECT addr_street, COUNT(*) FROM house WHERE feature_id IS NULL AND COALESCE(match, '') <> 'place' "
        "GROUP BY addr_street ORDER BY 2 DESC, 1 LIMIT ?", TOP)]
    out["weak_match_top"] = [(r[0], r[1], r[2]) for r in q(
        "SELECT f.ka, h.match, COUNT(*) FROM house h JOIN feature f ON f.id = h.feature_id "
        "WHERE h.match IN ('fuzzy', 'homonym_swap', 'no_type') GROUP BY f.ka, h.match ORDER BY 3 DESC, 1 LIMIT ?", TOP)]
    lane_codes = [k["code"] for k in kinds if k["group"] == "lane*"]
    ph = ",".join("?" * len(lane_codes)) or "''"
    out["lanes_no_parent"] = q(f"SELECT COUNT(*) FROM feature WHERE kind IN ({ph}) AND parent_id IS NULL",
                               *lane_codes)[0][0]
    out["lanes_no_parent_samples"] = [r[0] for r in q(
        f"SELECT ka FROM feature WHERE kind IN ({ph}) AND parent_id IS NULL ORDER BY ka LIMIT 10", *lane_codes)]
    out["ru_generated"] = q("SELECT COUNT(*) FROM feature WHERE ru_generated = 1")[0][0]
    out["no_ru"] = q("SELECT COUNT(*) FROM feature WHERE ru IS NULL OR ru = ''")[0][0]
    out["no_geom"] = q("SELECT COUNT(*) FROM feature WHERE geom_wkt IS NULL")[0][0]
    out["fam_no_eponym_top"] = [(r[0], r[1]) for r in q(
        "SELECT COALESCE(fm.base_ka, fm.key), COUNT(f.id) FROM family fm JOIN feature f ON f.family_id = fm.id "
        "WHERE fm.eponym_id IS NULL GROUP BY fm.id ORDER BY 2 DESC, 1 LIMIT ?", TOP)]
    out["fam_no_eponym"] = q("SELECT COUNT(*) FROM family fm WHERE fm.eponym_id IS NULL AND EXISTS "
                             "(SELECT 1 FROM feature f WHERE f.family_id = fm.id)")[0][0]
    out["eponym_review"] = q("SELECT COUNT(*) FROM eponym WHERE needs_review = 1")[0][0]
    out["history_unlinked"] = q("SELECT COUNT(*) FROM name_history WHERE feature_id IS NULL")[0][0]
    # name / kind mismatch: the name does not end with a type word of its kind
    words = _kind_ka_words(kinds)
    named_kinds = {k["code"] for k in kinds if k["group"] in ("street*", "lane*")} | {"square", "bridge", "tunnel"}
    mism: list[tuple[str, str]] = []
    n_mism = 0
    for kind, ka in q("SELECT kind, ka FROM feature ORDER BY kind, ka"):
        w = (words.get(kind) or ()) if kind in named_kinds else ()
        if w and not any(ka.rstrip().endswith(x) for x in w):
            n_mism += 1
            if len(mism) < TOP:
                mism.append((kind, ka))
    out["kind_mismatch"] = n_mism
    out["kind_mismatch_samples"] = mism
    return out


# --------------------------------------------------------------------------- last build diff
def snapshot(res: PackCheck, m: dict[str, Any], golden: dict[str, int] | None) -> dict[str, Any]:
    man = res.pack.get("manifest", {})
    return {"version": man.get("version"), "built": man.get("built"), "counts": dict(man.get("counts", {})),
            "kinds": {k: c["n"] for k, c in m["by_kind"].items()},
            "districts": {name: c["n"] for name, c in m["by_district"]},
            "golden": golden or {},
            "sizes_gzip": res.stats.get("sizes_gzip", {})}


def load_baseline(path: Path, version: str | None) -> dict[str, Any] | None:
    """Previous build snapshot. Re-validating the same version keeps diffing against its predecessor."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    cur = data.get("current")
    if cur and cur.get("version") == version:
        return data.get("previous")
    return cur


def save_baseline(path: Path, current: dict[str, Any], previous: dict[str, Any] | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"current": current, "previous": previous}, ensure_ascii=False, indent=1),
                    encoding="utf-8")


# --------------------------------------------------------------------------- HTML
CSS = """
:root{--bg:#fff;--fg:#1d2330;--mut:#5b6474;--line:#e3e6ec;--card:#f6f7f9;--acc:#2f6fd6;--ok:#1f8a4c;
--warn:#b06a00;--err:#c0392b;--barbg:#e6e9ef}
@media (prefers-color-scheme:dark){:root{--bg:#14171c;--fg:#e6e8ec;--mut:#9aa3b2;--line:#2a2f38;--card:#1b1f26;
--acc:#6aa0ff;--ok:#4cc27e;--warn:#e0a040;--err:#ff6b5b;--barbg:#2a2f38}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,"Noto Sans Georgian",sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}h1{font-size:24px;margin:0 0 4px}
h2{font-size:18px;margin:32px 0 10px;padding-top:8px;border-top:1px solid var(--line)}
.mut{color:var(--mut)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:14px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.card b{display:block;font-size:20px;font-variant-numeric:tabular-nums}.card span{color:var(--mut);font-size:12px}
.tbl{overflow-x:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:5px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{font-weight:600;color:var(--mut);font-size:12px}td.n,th.n{text-align:right;white-space:nowrap}
.bar{display:inline-block;width:70px;height:7px;background:var(--barbg);border-radius:4px;vertical-align:middle;
margin-right:6px;overflow:hidden}.bar span{display:block;height:100%;background:var(--acc)}
.pc{display:inline-block;min-width:46px;text-align:right}.ok{color:var(--ok)}.warn{color:var(--warn)}.err{color:var(--err)}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;border:1px solid currentColor}
ul.list{margin:6px 0;padding-left:20px}code{font-family:ui-monospace,Consolas,monospace;font-size:13px}
.up{color:var(--ok)}.down{color:var(--err)}.ka{font-family:"Noto Sans Georgian","Sylfaen",sans-serif}
"""


def _delta(cur: Any, prev: Any) -> str:
    if not isinstance(cur, int) or not isinstance(prev, int):
        return '<span class="mut">—</span>'
    d = cur - prev
    if d == 0:
        return '<span class="mut">0</span>'
    cls = "up" if d > 0 else "down"
    rel = f" ({100.0 * d / prev:+.1f}%)" if prev else ""
    return f'<span class="{cls}">{d:+d}{rel}</span>'


def _table(head: list[str], rows: list[list[str]], num_cols: set[int] = frozenset()) -> str:
    th = "".join(f'<th class="{"n" if i in num_cols else ""}">{h}</th>' for i, h in enumerate(head))
    body = "".join("<tr>" + "".join(f'<td class="{"n" if i in num_cols else ""}">{c}</td>'
                                    for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<div class="tbl"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def render_report(res: PackCheck, m: dict[str, Any] | None, *, golden_sql: dict[str, int] | None,
                  golden_pack: dict[str, int] | None, dbp: dict[str, Any] | None,
                  export_stats: dict[str, Any] | None, baseline: dict[str, Any] | None,
                  now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now()
    man = res.pack.get("manifest", {})
    status = ('<span class="pill ok">пакет корректен</span>' if res.ok
              else f'<span class="pill err">ошибок: {len(res.errors)}</span>')
    if res.warnings:
        status += f' <span class="pill warn">предупреждений: {len(res.warnings)}</span>'
    parts = [f"<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
             f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>Отчёт покрытия Quchebi</title><style>{CSS}</style></head><body><main>",
             "<h1>Отчёт покрытия пакета</h1>",
             f"<p class='mut'>Версия <b>{esc(man.get('version'))}</b>, собран {esc(man.get('built'))}, "
             f"снимок OSM {esc(man.get('osm') or 'не указан')}. Отчёт сформирован {now.strftime('%d/%m/%Y %H:%M')}."
             f"{' Сравнение со сборкой ' + esc(baseline.get('version')) + ' от ' + esc(baseline.get('built')) + '.' if baseline else ' Прошлой сборки для сравнения нет.'}"
             f"</p><p>{status}</p>"]

    # ---------------------------------------------------------------- summary cards
    if m:
        t = m["tot"]
        cards = [("объектов", fmt_int(t["features"])), ("улиц и переулков", fmt_int(t["streets"])),
                 ("адресов в пакете", fmt_int(man.get("counts", {}).get("houses"))),
                 ("пересечений", fmt_int(man.get("counts", {}).get("crossings"))),
                 ("эпонимов", fmt_int(t["eponyms"])), ("с названием ru", pct(t["ru"], t["features"])),
                 ("улиц с домами", pct(t["streets_houses"], t["streets"])),
                 ("с эпонимом", pct(t["ep"], t["features"])), ("с категорией", pct(t["cat"], t["features"])),
                 ("«оценка ИИ»", pct(t["ai"], t["ep"]))]
        if dbp:
            cards.insert(3, ("адресов привязано", pct(dbp["house_attached"] + dbp["house_place"], dbp["house_total"])))
        parts.append('<div class="grid">' + "".join(f'<div class="card"><b>{v}</b><span>{k}</span></div>'
                                                    for k, v in cards) + "</div>")

    # ---------------------------------------------------------------- sizes
    sizes = res.stats.get("sizes", {})
    gz = res.stats.get("sizes_gzip", {})
    if sizes:
        rows = [[f"<code>{esc(k)}</code>", f"{v / 1024:.1f}", f"{gz.get(k, 0) / 1024:.1f}"] for k, v in sizes.items()]
        tg = sum(gz.values())
        cls = "ok" if tg / 1024 <= GZIP_BUDGET_KB else "err"
        rows.append(["<b>Всего</b>", f"<b>{sum(sizes.values()) / 1024:.1f}</b>",
                     f"<b class='{cls}'>{tg / 1024:.1f}</b>"])
        parts.append("<h2>Размер пакета</h2>")
        parts.append(_table(["Файл", "raw, KB", "gzip, KB"], rows, {1, 2}))
        tiles = man.get("tiles") or {}
        tiles_note = ""
        if tiles.get("bytes"):
            tiles_note = (f" Подложка карты: {tiles['bytes'] / 1e6:.1f} MB, файлов {len(tiles.get('files', {}))}"
                          f"{', зум ' + str(tiles.get('minzoom', 0)) + '–' + str(tiles['maxzoom']) if 'maxzoom' in tiles else ''}.")
        parts.append(f"<p class='mut'>Бюджет пакета — {GZIP_BUDGET_KB} KB gzip без подложки карты.{tiles_note}</p>")

    # ---------------------------------------------------------------- counts diff
    cnt = man.get("counts", {})
    prev = (baseline or {}).get("counts", {})
    labels = {"features": "объекты", "streets": "улицы и переулки", "houses": "адреса", "crossings": "пересечения",
              "eponyms": "эпонимы", "families": "семьи названий", "districts": "районы", "places": "микрорайоны",
              "near": "пары «рядом»", "old_names": "прежние названия", "alt_names": "другие написания",
              "eponyms_ai": "эпонимы с оценкой ИИ"}
    parts.append("<h2>Счётчики и сравнение с прошлой сборкой</h2>")
    parts.append(_table(["Что", "Было", "Стало", "Разница"],
                        [[esc(labels.get(k, k)), fmt_int(prev.get(k)) if prev else "—", fmt_int(v),
                          _delta(v, prev.get(k)) if prev else "—"] for k, v in cnt.items()], {1, 2, 3}))

    if m:
        # ------------------------------------------------------------ by kind
        bk = m["by_kind"]
        pk = (baseline or {}).get("kinds", {})
        rows = []
        for code, c in sorted(bk.items(), key=lambda kv: -kv[1]["n"]):
            rows.append([esc(m["kind_ru"].get(code, code)) + f" <span class='mut'>{esc(code)}</span>",
                         fmt_int(c["n"]), _delta(c["n"], pk.get(code)) if baseline else "—",
                         bar(c["ru"], c["n"]), bar(c["houses"], c["n"]), bar(c["ep"], c["n"]),
                         bar(c["cat"], c["n"]), bar(c["ai"], c["ep"]), bar(c["old"], c["n"])])
        parts.append("<h2>По видам объектов</h2>")
        parts.append(_table(["Вид", "Число", "Δ", "С ru", "С домами", "С эпонимом", "С категорией",
                             "«Оценка ИИ» (из эпонимов)", "С прежними названиями"], rows, {1, 2}))
        # ------------------------------------------------------------ by district
        pdist = (baseline or {}).get("districts", {})
        rows = [[esc(name), fmt_int(c["n"]), _delta(c["n"], pdist.get(name)) if baseline else "—",
                 fmt_int(c["streets"]), fmt_int(c["hn"]), bar(c["ep"], c["n"])] for name, c in m["by_district"]]
        rows.append(["<span class='mut'>вне районов</span>", fmt_int(m["tot"]["no_district"]), "", "", "", ""])
        parts.append("<h2>По районам</h2>")
        parts.append("<p class='mut'>Объект, задевающий несколько районов, учтён в каждом.</p>")
        parts.append(_table(["Район", "Объектов", "Δ", "Улиц и переулков", "Адресов", "С эпонимом"], rows,
                            {1, 2, 3, 4}))
        # ------------------------------------------------------------ eponyms
        t = m["tot"]
        parts.append("<h2>Эпонимы</h2>")
        parts.append(_table(["Показатель", "Значение", "Доля"], [
            ["Семьи названий с эпонимом", f"{fmt_int(t['families_ep'])} из {fmt_int(t['families'])}",
             bar(t["families_ep"], t["families"])],
            ["Персональные семьи с категорией (цель ≥ 90%)",
             f"{fmt_int(t['person_families_cat'])} из {fmt_int(t['person_families'])}",
             bar(t["person_families_cat"], t["person_families"])],
            ["Эпонимы с категорией", f"{fmt_int(t['eponyms_cat'])} из {fmt_int(t['eponyms'])}",
             bar(t["eponyms_cat"], t["eponyms"])],
            ["Эпонимы с «оценкой ИИ»", f"{fmt_int(t['eponyms_ai'])} из {fmt_int(t['eponyms'])}",
             bar(t["eponyms_ai"], t["eponyms"])],
            ["Объекты с прежними названиями", f"{fmt_int(t['with_old'])} (названий: {fmt_int(t['old'])})",
             bar(t["with_old"], t["features"])],
        ], {1}))
        top_cat = m["cat_counts"].most_common(TOP)
        if top_cat:
            parts.append("<p class='mut'>Самые частые категории: " + ", ".join(
                f"<code>{esc(c)}</code> {n}" for c, n in top_cat) + ".</p>")

    # ---------------------------------------------------------------- golden
    if golden_sql or golden_pack:
        pg = (baseline or {}).get("golden", {})
        rows = []
        for q in QUERIES:
            s = (golden_sql or {}).get(q)
            p = (golden_pack or {}).get(q)
            ok = s is None or p is None or s == p
            rows.append([f"<code>{esc(q)}</code>", fmt_int(s) if s is not None else "—",
                         fmt_int(p) if p is not None else "—",
                         _delta(s, pg.get(q)) if baseline and s is not None else "—",
                         '<span class="ok">совпадает</span>' if ok else '<span class="err">расходится</span>'])
        parts.append("<h2>Эталонные запросы (без зоны)</h2>")
        parts.append("<p class='mut'>SQL по базе против независимого подсчёта по пакету. Значения SQL записаны в "
                     "<code>data/build/golden_counts.json</code> для сверки с JS-движком DSL.</p>")
        parts.append(_table(["Запрос", "SQL", "Пакет", "Δ к прошлой", "Сверка"], rows, {1, 2, 3}))

    # ---------------------------------------------------------------- problems
    probs: list[tuple[int, str, str]] = []
    if dbp:
        un = dbp["house_total"] - dbp["house_attached"] - dbp["house_place"]
        probs.append((un, "Неприсоединённые адреса",
                      ", ".join(f"<span class='ka'>{esc(s)}</span> ({n})" for s, n in dbp["unattached_top"][:8])))
        probs.append((dbp["kind_mismatch"], "Улица, площадь или мост: название не кончается словом своего вида",
                      ", ".join(f"<span class='ka'>{esc(ka)}</span> <span class='mut'>{esc(k)}</span>"
                                for k, ka in dbp["kind_mismatch_samples"][:8])))
        weak = sum(c for mt, c in dbp["house_match"] if mt in ("fuzzy", "homonym_swap", "no_type"))
        probs.append((weak, "Адреса, привязанные неточно (fuzzy, homonym_swap, no_type)",
                      ", ".join(f"<span class='ka'>{esc(ka)}</span> {esc(mt)} ({n})"
                                for ka, mt, n in dbp["weak_match_top"][:8])))
        probs.append((dbp["lanes_no_parent"], "Переулки и тупики без родительской улицы",
                      ", ".join(f"<span class='ka'>{esc(s)}</span>" for s in dbp["lanes_no_parent_samples"][:8])))
        probs.append((dbp["fam_no_eponym"], "Семьи без эпонима",
                      ", ".join(f"<span class='ka'>{esc(s)}</span> ({n})" for s, n in dbp["fam_no_eponym_top"][:8])))
        probs.append((dbp["no_ru"], "Объекты без русского названия", ""))
        probs.append((dbp["ru_generated"], "Русские названия, построенные транслитерацией", ""))
        probs.append((dbp["no_geom"], "Объекты без геометрии", ""))
        probs.append((dbp["eponym_review"], "Эпонимы, ждущие проверки (needs_review)", ""))
        probs.append((dbp["history_unlinked"], "Прежние названия, не привязанные к объекту", ""))
    hf = res.stats.get("houses_far_count")
    if hf:
        probs.append((hf, "Адреса дальше 300 м от своей улицы",
                      ", ".join(f"<span class='ka'>{esc(x['street'])}</span> {esc(x['raw'])} — {x['dist']} м"
                                for x in res.stats.get("houses_far", [])[:8])))
    cf = res.stats.get("crossings_far", [])
    if cf:
        probs.append((len(cf), "Точки пересечений дальше 30 м от улиц (выборка)",
                      ", ".join(f"<span class='ka'>{esc(x['a'])} × {esc(x['b'])}</span> — {x['dist']} м" for x in cf[:5])))
    exp_labels = {"house_not_exportable": "Адреса без номера или координат (не в пакете)",
                  "house_duplicate_rows": "Повторяющиеся строки адресов (свёрнуты)",
                  "unknown_kind": "Неизвестный вид объекта (выгружен как «прочее»)",
                  "category_not_in_taxonomy": "Категории вне таксономии (отброшены)",
                  "old_name_low_confidence": "Прежние названия с уверенностью < 0,5 (отброшены)",
                  "feature_without_geometry": "Объекты без геометрии (выгружены точкой)",
                  "parent_missing": "Ссылка на несуществующего родителя",
                  "crossing_bad_ref": "Пересечения с битой ссылкой", "area_feature_mismatch":
                  "Район/микрорайон без своей области"}
    for k, v in sorted(((export_stats or {}).get("problems") or {}).items(), key=lambda kv: -kv[1]):
        if k in ("house_unattached", "old_name_unlinked", "feature_without_geometry") and dbp:
            continue
        samples = (export_stats or {}).get("problem_samples", {}).get(k, [])
        probs.append((v, esc(exp_labels.get(k, k)), ", ".join(f"<span class='ka'>{esc(s)}</span>" for s in samples[:6])))
    probs = [p for p in probs if p[0]]
    probs.sort(key=lambda p: -p[0])
    parts.append("<h2>Топ проблем</h2>")
    if probs:
        parts.append(_table(["Проблема", "Число", "Примеры"], [[t_, fmt_int(n), ex] for n, t_, ex in probs], {1}))
    else:
        parts.append("<p class='ok'>Проблем не найдено.</p>")
    if dbp and dbp["house_match"]:
        parts.append("<p class='mut'>Привязка адресов по способу: " + ", ".join(
            f"{esc(mt)} — {fmt_int(n)}" for mt, n in dbp["house_match"]) + ".</p>")
    if dbp and dbp["unattached_top"]:
        parts.append("<h2>Неприсоединённые адреса по улицам</h2>")
        parts.append(_table(["addr:street", "Адресов"], [[f"<span class='ka'>{esc(s)}</span>", fmt_int(n)]
                                                       for s, n in dbp["unattached_top"]], {1}))

    # ---------------------------------------------------------------- validation messages
    parts.append("<h2>Проверка инвариантов</h2>")
    if res.errors:
        parts.append("<p class='err'>Ошибки (пакет нельзя публиковать):</p><ul class='list'>" +
                     "".join(f"<li><code>{esc(e)}</code></li>" for e in res.errors[:100]) + "</ul>")
    else:
        parts.append("<p class='ok'>Ошибок нет: индексы в диапазоне, пары пересечений a &lt; b, sha256 совпадают, "
                     "у каждого дома есть объект, файлы соответствуют JSON-схемам.</p>")
    if res.warnings:
        parts.append("<p class='warn'>Предупреждения:</p><ul class='list'>" +
                     "".join(f"<li><code>{esc(w)}</code></li>" for w in res.warnings[:100]) + "</ul>")
    attr = man.get("attribution") or []
    if attr:
        parts.append("<p class='mut'>Источники: " + "; ".join(esc(a) for a in attr) + ".</p>")
    parts.append("</main></body></html>")
    return "\n".join(parts)


def gzip_sizes(pack_dir: Path, names: list[str]) -> dict[str, int]:
    out = {}
    for n in names:
        p = pack_dir / n
        if p.exists():
            out[n] = len(gzip.compress(p.read_bytes(), 9))
    return out
