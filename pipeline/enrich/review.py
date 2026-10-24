"""Stage ``review``: manual review page and application of review decisions.

``data/build/review.html`` is a self-contained Russian page (no external resources) listing every
disputed eponym: the proposal, the reasons, the Wikidata candidates. Buttons "Принять /
Исправить / Отклонить" collect decisions in the page; "Скопировать JSON" puts the full decision list
(earlier decisions included) on the clipboard for ``data/enrich/review_decisions.json``.

Decision format (list)::

    {"entity": "family" | "name_history", "entity_key": "<family.key | old-name key>",
     "decision": "accept" | "fix" | "reject", "payload": {...}, "decided_at": "DD/MM/YYYY"}

``accept`` payload ``{"qid", "kind"}`` -- a snapshot of the accepted proposal (ignored if the proposal
changed since); ``fix`` payload: any of ``qid, kind, categories, ka, ru, en, gender, birth_year,
death_year, origin, short_ru, translation_ru`` (the translation goes to ``provenance``); ``reject`` -- the family keeps no eponym. Decisions survive rebuilds
(keyed by text keys) and are re-applied at the end of ``facts`` and ``merge``.
"""
from __future__ import annotations

import html
import json
import sqlite3
import time
from pathlib import Path

from pipeline.enrich.common import (add_provenance, paths, read_json, read_jsonl,
                                    stage_path)
from pipeline.enrich.store import set_categories, upsert_eponym
from pipeline.enrich.taxonomy import load as load_taxonomy
from pipeline.log import get_logger, stage

log = get_logger(__name__)

FIX_FIELDS = ("kind", "ka", "ru", "en", "gender", "birth_year", "death_year", "origin", "short_ru")
VALID_DECISIONS = {"accept", "fix", "reject"}
KINDS = ["person", "persons_multi", "group", "fictional_character", "toponym", "event_date", "nature", "concept",
         "object", "no_eponym", "unknown"]


def load_decisions(path: Path | None = None) -> list[dict]:
    data = read_json(path or paths.decisions, []) or []
    if isinstance(data, dict):
        data = data.get("decisions", [])
    out = []
    for d in data if isinstance(data, list) else []:
        if not isinstance(d, dict):
            continue
        if d.get("decision") not in VALID_DECISIONS or d.get("entity") not in ("family", "name_history") \
                or not isinstance(d.get("entity_key"), str):
            log.warning("review: skipping malformed decision %s", str(d)[:200])
            continue
        if d.get("payload") is not None and not isinstance(d["payload"], dict):
            log.warning("review: payload must be an object: %s", str(d)[:200])
            continue
        out.append(d)
    return out


def sync_table(con: sqlite3.Connection, decisions: list[dict]) -> None:
    con.execute("DELETE FROM review_decision")
    con.executemany(
        "INSERT OR REPLACE INTO review_decision(entity, entity_key, decision, payload, decided_at) VALUES (?,?,?,?,?)",
        [(d["entity"], d["entity_key"], d["decision"], json.dumps(d.get("payload") or {}, ensure_ascii=False),
          d.get("decided_at") or time.strftime("%d/%m/%Y")) for d in decisions])


def _targets(con: sqlite3.Connection, entity: str, key: str) -> list[tuple[str, int | None]]:
    """(table-specific id, current eponym id) rows the decision applies to."""
    if entity == "family":
        row = con.execute("SELECT key, eponym_id FROM family WHERE key=?", (key,)).fetchone()
        return [(row[0], row[1])] if row else []
    ids = old_name_ids(con).get(key, [])
    return [(str(i), con.execute("SELECT eponym_id FROM name_history WHERE id=?", (i,)).fetchone()[0]) for i in ids]


def old_name_ids(con: sqlite3.Connection) -> dict[str, list[int]]:
    st = read_json(stage_path("oldnames"), {}) or {}
    return {k: v.get("nh_ids", []) for k, v in st.get("names", {}).items()}


def _link(con: sqlite3.Connection, entity: str, target: str, eid: int | None) -> None:
    if entity == "family":
        con.execute("UPDATE family SET eponym_id=? WHERE key=?", (eid, target))
    else:
        con.execute("UPDATE name_history SET eponym_id=? WHERE id=?", (eid, int(target)))


def apply_decisions(con: sqlite3.Connection, builder=None, path: Path | None = None) -> int:
    """Apply review decisions from ``path``; returns the number applied."""
    decisions = load_decisions(path)
    tax = load_taxonomy()
    n = 0
    with con:
        sync_table(con, decisions)
        con.execute("DELETE FROM provenance WHERE source='manual'")
        for d in decisions:
            ent, key, dec, pl = d["entity"], d["entity_key"], d["decision"], d.get("payload") or {}
            targets = _targets(con, ent, key)
            if not targets:
                log.info("review: %s %s not found (skipped)", ent, key)
                continue
            if dec == "reject":
                for t, _ in targets:
                    _link(con, ent, t, None)
                add_provenance(con, ent, key, "eponym", None, "manual", "reject", 1.0)
                n += 1
                continue
            if dec == "accept":
                ok = False
                for _, eid in targets:
                    if eid is None:
                        continue
                    qid, kind = con.execute("SELECT qid, kind FROM eponym WHERE id=?", (eid,)).fetchone()
                    if pl.get("qid", qid) == qid and pl.get("kind", kind) == kind:
                        con.execute("UPDATE eponym SET needs_review=0, confidence=MAX(COALESCE(confidence,0), 0.9) "
                                    "WHERE id=?", (eid,))
                        ok = True
                if ok:
                    add_provenance(con, ent, key, "eponym", pl.get("qid"), "manual", "accept", 1.0)
                    n += 1
                else:
                    log.warning("review: stale accept for %s %s (proposal changed): needs a new review", ent, key)
                continue
            # fix
            qid = pl.get("qid") or None
            rec: dict = {}
            cats: list[str] = []
            if qid and builder is not None:
                f = builder.build([qid]).get(qid)
                if f is not None:
                    rec = f.record("manual", 1.0, False)
                    cats = f.categories
            if not rec:
                rec = {"qid": qid, "kind": "unknown", "source": "manual", "confidence": 1.0, "needs_review": False}
            if not qid:
                rec["qid"] = None
                rec["key"] = f"manual:{ent}:{key}"
            for fld in FIX_FIELDS:
                if fld in pl and pl[fld] not in ("", None):
                    rec[fld] = pl[fld]
            rec.update(source="manual", confidence=1.0, needs_review=False)
            if isinstance(pl.get("categories"), list) and pl["categories"]:
                cats = [c for c in pl["categories"] if tax.valid(c)]
            eid = upsert_eponym(con, rec)
            set_categories(con, eid, cats, "manual")
            for t, _ in targets:
                _link(con, ent, t, eid)
            add_provenance(con, ent, key, "eponym", qid or rec["key"], "manual", "fix", 1.0)
            if isinstance(pl.get("translation_ru"), str) and pl["translation_ru"].strip():
                add_provenance(con, ent, key, "translation_ru", pl["translation_ru"].strip(), "manual", "fix", 1.0)
            n += 1
    if decisions:
        log.info("review: %d of %d decisions applied", n, len(decisions))
    return n


# ---------------------------------------------------------------------------------- page
def review_items(con: sqlite3.Connection, merge_state: dict, dossier_rows: list[dict]) -> list[dict]:
    by_fid = {r["dossier"].get("family_id"): r for r in dossier_rows
              if r["meta"]["kind"] in ("queue", "old_name", "verify")}
    items = []
    seen = set()

    def eponym_row(eid: int | None) -> dict | None:
        if not eid:
            return None
        row = con.execute("SELECT id, qid, key, kind, ka, ru, en, short_ru, confidence, source, birth_year, "
                          "death_year FROM eponym WHERE id=?", (eid,)).fetchone()
        if row is None:
            return None
        cats = [c for (c,) in con.execute("SELECT category FROM eponym_category WHERE eponym_id=? ORDER BY rank",
                                          (eid,))]
        return {"id": row[0], "qid": row[1], "key": row[2], "kind": row[3], "ka": row[4], "ru": row[5], "en": row[6],
                "short_ru": row[7], "confidence": row[8], "source": row[9], "birth_year": row[10],
                "death_year": row[11], "categories": cats}

    def add(fid_: str, ent: str, key: str, reason: str, extra: dict | None = None) -> None:
        if (ent, key) in seen:
            return
        seen.add((ent, key))
        row = by_fid.get(fid_) or {}
        d = row.get("dossier", {})
        if ent == "family":
            r = con.execute("SELECT eponym_id FROM family WHERE key=?", (key,)).fetchone()
            eid = r[0] if r else None
        else:
            eid = (extra or {}).get("eponym_id")
        items.append({"entity": ent, "key": key, "fid": fid_, "reason": reason, "names": d.get("names", {}),
                      "eponym_ka": d.get("eponym_ka", ""), "eponym_ru": d.get("eponym_ru", ""),
                      "members": [m.get("ka") for m in d.get("members", [])][:8],
                      "old_names": d.get("old_names", [])[:4], "rules": d.get("rules", {}),
                      "candidates": d.get("candidates", []), "proposal": eponym_row(eid), **(extra or {})})

    for fid_, st in sorted(merge_state.get("families", {}).items(), key=lambda kv: kv[1].get("family_key") or ""):
        if st.get("route") in ("manual", "second_opinion", "verify_conflict"):
            ent = st.get("entity", "family")
            ent = "family" if ent == "family" else "name_history"
            add(fid_, ent, st.get("family_key") or st.get("old_key"), f"{st['route']}: {st.get('reason')}",
                {"triggers": st.get("triggers", []), "llm": st.get("answer"), "eponym_id": st.get("eponym_id")})
    for v in merge_state.get("verifier_disagreements", []):
        add(v["family_id"], "family", v["family_key"], "verifier: " + v["why"], {"llm": v.get("verifier")})
    for key, eid in con.execute("SELECT f.key, f.eponym_id FROM family f JOIN eponym e ON e.id=f.eponym_id "
                                "WHERE e.needs_review=1 AND e.source != 'llm'"):
        fid_ = next((i for i, r in by_fid.items() if r["meta"].get("family_key") == key), "")
        add(fid_, "family", key, "needs_review (" + "facts" + ")")
    return items


PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Проверка эпонимов</title>
<style>
:root{--bg:#fafaf7;--fg:#1d1d1b;--muted:#6b6b66;--card:#fff;--line:#e3e2dc;--acc:#2f6f4f;--warn:#a5521b;--bad:#a31d1d}
@media (prefers-color-scheme: dark){:root{--bg:#161615;--fg:#ecebe6;--muted:#a3a29b;--card:#202020;--line:#383836;--acc:#6fbf8f;--warn:#e0a060;--bad:#ef7a7a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 16px;z-index:2;display:flex;gap:12px;flex-wrap:wrap;align-items:center}
h1{font-size:18px;margin:0 12px 0 0}main{max-width:1000px;margin:0 auto;padding:12px 16px 80px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin:12px 0}
.card.done{opacity:.55}.ka{font-size:17px;font-weight:600}.muted{color:var(--muted)}.reason{color:var(--warn);font-size:13px}
.cands{margin:8px 0;padding:0;list-style:none}.cands li{border-top:1px dashed var(--line);padding:6px 0}
.chip{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:0 7px;font-size:12px;margin:0 4px 2px 0}
button{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:6px;padding:4px 10px;cursor:pointer}
button.acc{border-color:var(--acc);color:var(--acc)}button.bad{border-color:var(--bad);color:var(--bad)}
.fix{display:none;margin-top:8px;gap:6px;flex-direction:column}.fix.on{display:flex}
.fix input,.fix select{font:inherit;padding:3px 6px;border:1px solid var(--line);border-radius:4px;background:var(--bg);color:var(--fg);max-width:100%}
textarea{width:100%;height:120px;font:12px monospace;background:var(--card);color:var(--fg);border:1px solid var(--line)}
.state{font-weight:600}.intro{font-size:13px;color:var(--muted);margin-top:2px}
</style></head><body>
<header><h1>Проверка эпонимов</h1><span id="cnt" class="muted"></span>
<label><input type="checkbox" id="hideDone"> скрыть решённые</label>
<button id="copy" class="acc">Скопировать JSON</button></header>
<main><p class="muted">Создано __BUILT__. Решения копируются в буфер одним списком (вместе с прежними) — вставьте его в
<code>data/enrich/review_decisions.json</code> и запустите <code>uv run python -m pipeline.enrich merge</code>.</p>
<div id="list"></div><textarea id="out" readonly placeholder="JSON решений"></textarea></main>
<script id="data" type="application/json">__DATA__</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById("data").textContent);
const KEY = "quchebi-review-v1";
let dec = {};
for (const x of D.decisions) dec[x.entity + "|" + x.entity_key] = x;
try { const s = JSON.parse(localStorage.getItem(KEY) || "{}"); Object.assign(dec, s); } catch (e) {}
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const today = () => { const d = new Date(); return [d.getDate(), d.getMonth() + 1, d.getFullYear()].map((v, i) => i < 2 ? String(v).padStart(2, "0") : v).join("/"); };
function save() { try { localStorage.setItem(KEY, JSON.stringify(dec)); } catch (e) {} render(); }
function years(c) { return (c.born || c.died) ? " (" + (c.born || "?") + "–" + (c.died || "") + ")" : ""; }
function card(it, i) {
  const k = it.entity + "|" + it.key, d = dec[k], p = it.proposal || {};
  const cands = (it.candidates || []).map(c => `<li><label><input type="radio" name="q${i}" value="${esc(c.qid)}" ${p.qid === c.qid ? "checked" : ""}>
    <b>${esc(c.label_ru || c.label_en || c.label_ka)}</b> ${esc(c.label_ka)} <span class="muted">${esc(c.qid)}${esc(years(c))}</span></label>
    <div>${esc(c.description_ru || c.description_en || "")} ${(c.occupations_ru || []).map(o => `<span class="chip">${esc(o)}</span>`).join("")}
    ${c.georgian ? '<span class="chip">Грузия</span>' : ""}</div>${c.kawiki_intro || c.ruwiki_intro ? `<div class="intro">${esc((c.ruwiki_intro || c.kawiki_intro).slice(0, 300))}</div>` : ""}</li>`).join("");
  const cats = D.categories.map(c => `<option value="${c.id}" ${(p.categories || []).includes(c.id) ? "selected" : ""}>${esc(c.id)} — ${esc(c.ru)}</option>`).join("");
  const kinds = D.kinds.map(x => `<option ${p.kind === x ? "selected" : ""}>${x}</option>`).join("");
  return `<div class="card ${d ? "done" : ""}" data-i="${i}">
   <div class="ka">${esc(it.names.ka || it.eponym_ka || it.key)}</div>
   <div class="muted">${esc(it.names.ru || it.eponym_ru || "")} ${esc(it.names.en || "")}</div>
   <div class="reason">${esc(it.reason)} ${(it.triggers || []).map(t => `<span class="chip">${esc(t)}</span>`).join("")}</div>
   ${it.members && it.members.length > 1 ? `<div class="muted">Семейство: ${esc(it.members.join(", "))}</div>` : ""}
   ${(it.old_names || []).length ? `<div class="muted">Прежние: ${esc(it.old_names.map(o => (o.ru || o.ka || o.lat) + (o.from ? " (" + o.from + "–" + (o.to || "") + ")" : "")).join("; "))}</div>` : ""}
   <div>Предложение: <b>${esc(p.ru || p.ka || "—")}</b> <span class="chip">${esc(p.kind || "нет")}</span>
     ${p.qid ? `<span class="chip">${esc(p.qid)}</span>` : ""} ${(p.categories || []).map(c => `<span class="chip">${esc(c)}</span>`).join("")}
     <span class="muted">${esc(p.short_ru || "")} ${p.confidence != null ? "· " + Number(p.confidence).toFixed(2) : ""} ${esc(p.source || "")}</span></div>
   ${cands ? `<ul class="cands">${cands}<li><label><input type="radio" name="q${i}" value=""> ни один (без QID)</label></li></ul>` : ""}
   <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px">
     <button class="acc" data-a="accept">Принять</button><button data-a="fixopen">Исправить</button>
     <button class="bad" data-a="reject">Отклонить</button>
     ${d ? `<span class="state">Решение: ${esc(d.decision)}</span> <button data-a="undo">Отменить</button>` : ""}</div>
   <div class="fix"><label>QID <input name="qid" value="${esc(p.qid || "")}" placeholder="Q…"></label>
     <label>Тип <select name="kind">${kinds}</select></label>
     <label>Категории <select name="cats" multiple size="6">${cats}</select></label>
     <label>Имя по-русски <input name="ru" value="${esc(p.ru || "")}"></label>
     <label>Описание <input name="short_ru" value="${esc(p.short_ru || "")}" maxlength="240"></label>
     <label>Перевод (для понятий) <input name="translation_ru" value="${esc((it.llm || {}).translation_ru || "")}" maxlength="120"></label>
     <button class="acc" data-a="fix">Сохранить исправление</button></div></div>`;
}
function render() {
  const hide = document.getElementById("hideDone").checked;
  const list = document.getElementById("list");
  list.innerHTML = D.items.map((it, i) => (hide && dec[it.entity + "|" + it.key]) ? "" : card(it, i)).join("");
  const n = D.items.filter(it => dec[it.entity + "|" + it.key]).length;
  document.getElementById("cnt").textContent = `решено ${n} из ${D.items.length}`;
  document.getElementById("out").value = JSON.stringify(Object.values(dec), null, 1);
}
document.getElementById("list").addEventListener("click", ev => {
  const b = ev.target.closest("button"); if (!b) return;
  const el = b.closest(".card"), it = D.items[+el.dataset.i], k = it.entity + "|" + it.key, p = it.proposal || {};
  const a = b.dataset.a;
  if (a === "fixopen") { el.querySelector(".fix").classList.toggle("on"); return; }
  if (a === "undo") { delete dec[k]; save(); return; }
  const base = {entity: it.entity, entity_key: it.key, decided_at: today()};
  if (a === "accept") dec[k] = {...base, decision: "accept", payload: {qid: p.qid || null, kind: p.kind || null}};
  if (a === "reject") dec[k] = {...base, decision: "reject", payload: {}};
  if (a === "fix") {
    const f = el.querySelector(".fix"), r = el.querySelector('input[type=radio]:checked');
    let qid = f.querySelector('[name=qid]').value.trim() || (r ? r.value : "");
    if (qid && !/^Q[1-9][0-9]*$/.test(qid)) { alert("QID должен выглядеть как Q123"); return; }
    const cats = [...f.querySelector('[name=cats]').selectedOptions].map(o => o.value);
    const pl = {qid: qid || null, kind: f.querySelector('[name=kind]').value};
    if (cats.length) pl.categories = cats;
    for (const n of ["ru", "short_ru", "translation_ru"]) { const v = f.querySelector(`[name=${n}]`).value.trim(); if (v) pl[n] = v; }
    dec[k] = {...base, decision: "fix", payload: pl};
  }
  save();
});
document.getElementById("list").addEventListener("change", ev => {
  if (ev.target.type !== "radio") return;
  const el = ev.target.closest(".card"); el.querySelector(".fix").classList.add("on");
  el.querySelector('[name=qid]').value = ev.target.value;
});
document.getElementById("hideDone").addEventListener("change", render);
document.getElementById("copy").addEventListener("click", async () => {
  const txt = JSON.stringify(Object.values(dec), null, 1), ta = document.getElementById("out");
  ta.value = txt;
  try { await navigator.clipboard.writeText(txt); document.getElementById("copy").textContent = "Скопировано"; }
  catch (e) { ta.select(); try { document.execCommand("copy"); document.getElementById("copy").textContent = "Скопировано"; }
    catch (e2) { document.getElementById("copy").textContent = "Выделите текст ниже"; } }
  setTimeout(() => document.getElementById("copy").textContent = "Скопировать JSON", 2000);
});
render();
</script></body></html>
"""


def build_page(items: list[dict], decisions: list[dict]) -> str:
    tax = load_taxonomy()
    cats = [{"id": c["id"], "ru": c["ru"]} for g in tax.raw["categories"] for c in g["children"]]
    data = {"items": items, "decisions": decisions, "categories": cats, "kinds": KINDS}
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return PAGE.replace("__DATA__", blob).replace("__BUILT__", html.escape(time.strftime("%d/%m/%Y %H:%M")))


def run(con: sqlite3.Connection, dossier_paths: list[Path], out: Path | None = None) -> dict:
    out = out or paths.review_html
    with stage(log, "enrich.review") as st:
        merge_state = read_json(stage_path("merge"), {}) or {}
        rows: list[dict] = []
        for p in dossier_paths:
            rows += read_jsonl(p)
        items = review_items(con, merge_state, rows)
        decisions = load_decisions()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(build_page(items, decisions), encoding="utf-8")
        st.update(items=len(items), decisions=len(decisions), page=str(out))
    return {"items": len(items), "path": str(out)}
