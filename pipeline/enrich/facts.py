"""Stage ``facts``: Wikidata facts for every decided QID -> ``eponym`` + ``eponym_category``.

Decided QIDs: OSM etymology, P138 street items, unique label matches. For each QID:
labels ka/ru/en, descriptions, P106/P39/P410/P411/P97/P166/P1196/P31 (taxonomy via P279*),
P21 gender, P569/P570 years, origin (P27 / P19->P17 / P172 / P103 / P1412), sitelinks kawiki/ruwiki,
``occupations_ru`` = Russian labels of P106 (third taxonomy level: "хормейстер", "дирижёр").

Families without a QID but with a confident rule (toponym from the gazetteer, date, numbered
street, road direction) get a ``source='rules'`` eponym. Everything else goes to the LLM queue
(``data/enrich/stages/facts.json`` lists the queue reasons).
"""
from __future__ import annotations

import collections
import sqlite3
from dataclasses import dataclass, field

from pipeline.enrich import ka as K
from pipeline.enrich.common import (Family, add_provenance, clear_provenance, stage_path, write_json)
from pipeline.enrich.store import (delete_orphans, link_family, set_categories, unlink_stale, upsert_eponym)
from pipeline.enrich.taxonomy import GENDER, Taxonomy
from pipeline.enrich.taxonomy import load as load_taxonomy
from pipeline.log import get_logger, stage
from pipeline.wikidata.classes import ClassIndex
from pipeline.wikidata.client import WikidataClient
from pipeline.wikidata.entity import (claim_ids, description, is_human, label, sitelink, year)

log = get_logger(__name__)

CONF = {"osm": 0.98, "wd_p138": 0.95, "wd_match": 0.9, "wd_match_pref": 0.85, "rules_toponym_qid": 0.8,
        "rules_toponym": 0.75, "rules_date": 0.95, "rules_numbered": 0.95, "rules_route": 0.9, "conflict": 0.6}
ANIMAL_WORDS = ("bird", "mammal", "fish", "insect", "animal", "reptile", "species of", "genus of")
PLANT_WORDS = ("plant", "tree", "flower", "shrub", "grass", "fruit")
ORIGIN_ADJ = {"georgian": "грузинский", "foreign": ""}


@dataclass
class EponymFacts:
    qid: str
    kind: str
    ka: str = ""
    ru: str = ""
    en: str = ""
    gender: str | None = None
    birth_year: int | None = None
    death_year: int | None = None
    origin: str = "unknown"
    short_ru: str = ""
    occupations_ru: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    kawiki: str = ""
    ruwiki: str = ""
    citizenship_ru: list[str] = field(default_factory=list)
    birthplace_ru: str = ""
    in_georgia: bool = False
    works_ru: list[str] = field(default_factory=list)
    member_of_ru: list[str] = field(default_factory=list)

    def record(self, source: str, confidence: float, needs_review: bool) -> dict:
        return {"qid": self.qid, "kind": self.kind, "ka": self.ka or None, "ru": self.ru or None,
                "en": self.en or None, "gender": self.gender, "birth_year": self.birth_year,
                "death_year": self.death_year, "origin": self.origin, "short_ru": self.short_ru or None,
                "occupations_ru": self.occupations_ru or None, "confidence": confidence, "source": source,
                "needs_review": needs_review}


def display_name(lab: str, human: bool) -> str:
    """Wikidata ru labels of people are often "Фамилия, Имя Отчество" -> "Имя Отчество Фамилия"."""
    if human and lab and lab.count(",") == 1:
        last, first = (x.strip() for x in lab.split(","))
        if last and first and " " not in last:
            return f"{first} {last}"
    return lab


def _ka_to_ru(s: str) -> str:
    try:
        from pipeline.names.translit import ka_to_ru
        return ka_to_ru(s).title()
    except Exception:  # pragma: no cover - names package unavailable
        return K.translit(s, K.KA2RU).title()


NOISY_MEMBERSHIP = ("академи", "academy", "кпсс", "коммунистическ", "communist", "союз писателей", "union of",
                    "общество", "society", "университет", "university", "партия", "party", "союз")
SHORT_MAX = 240


def _short_ru(desc_ru: str, occ: list[str], origin: str, works: list[str] | None = None,
              member_of: list[str] | None = None) -> str:
    """Card line: Wikidata description (or occupations) + notable works and groups for ``био:``."""
    if desc_ru:
        base = desc_ru[:160]
    elif occ:
        adj = ORIGIN_ADJ.get(origin, "")
        base = ((adj + " " if adj else "") + ", ".join(occ[:3]))[:160]
    else:
        base = ""
    parts = [base] if base else []
    w = [x for x in (works or []) if x and x.lower() not in base.lower()][:3]
    if w:
        parts.append("известен: " + ", ".join(f"«{x}»" for x in w))
    m = [x for x in (member_of or []) if x and not any(n in x.lower() for n in NOISY_MEMBERSHIP)
         and x.lower() not in base.lower()][:2]
    if m:
        parts.append("участник: " + ", ".join(f"«{x}»" for x in m))
    out = "; ".join(parts)
    return out if len(out) <= SHORT_MAX else out[:SHORT_MAX - 1].rstrip(" ,;") + "…"


class EponymBuilder:
    """Turns QIDs into :class:`EponymFacts` (all Wikidata access is cached)."""

    def __init__(self, client: WikidataClient, tax: Taxonomy | None = None):
        self.client = client
        self.tax = tax or load_taxonomy()
        self.classes = ClassIndex(client)
        self._memo: dict[str, EponymFacts | None] = {}

    def build(self, qids: list[str]) -> dict[str, EponymFacts]:
        """QID -> facts (memoized per builder; all Wikidata calls are batched)."""
        qids = sorted(set(qids))
        todo = [q for q in qids if q not in self._memo]
        if todo:
            ents = self.client.entities(todo)
            vals: set[str] = set()
            tax_vals: set[str] = set()
            for e in ents.values():
                for p in self.tax.props:
                    tax_vals.update(claim_ids(e, p))
                for p in ("P27", "P19", "P17", "P800", "P463"):
                    vals.update(claim_ids(e, p))
            vents = self.client.entities(sorted(vals | tax_vals), props="labels|claims", langs="ru|en|ka")
            self.classes.ancestors(sorted(tax_vals))        # one level-batched BFS for everything
            for q in todo:
                e = ents.get(q)
                if e is None:
                    log.warning("facts: entity %s unavailable", q)
                    self._memo[q] = None
                    continue
                self._memo[q] = self.facts(q, e, vents)
        return {q: self._memo[q] for q in qids if self._memo.get(q) is not None}

    def origin(self, e: dict, vents: dict[str, dict]) -> str:
        gs = self.tax.georgian_states
        p27 = set(claim_ids(e, "P27"))
        sig = bool(p27 & gs)
        for bp in claim_ids(e, "P19"):
            if set(claim_ids(vents.get(bp), "P17")) & gs:
                sig = True
        if "Q18973" in claim_ids(e, "P172") or "Q8108" in claim_ids(e, "P103") or "Q8108" in claim_ids(e, "P1412"):
            sig = True
        if sig:
            return "georgian"
        return "foreign" if (p27 or claim_ids(e, "P19")) else "unknown"

    def facts(self, q: str, e: dict, vents: dict[str, dict]) -> EponymFacts:
        human = is_human(e)
        in_georgia = bool(set(claim_ids(e, "P17")) & self.tax.georgian_states) or "Q230" in claim_ids(e, "P131")
        cats, why = self.tax.categorize(e, self.classes.ancestors, in_georgia=in_georgia and not human)
        if not human and "nature.plant" in cats:
            d = description(e, "en").lower()
            if any(w in d for w in ANIMAL_WORDS) and not any(w in d for w in PLANT_WORDS):
                cats = ["nature.animal" if c == "nature.plant" else c for c in cats]
        if human:
            kind = "fictional_character" if "lit.character" in cats else "person"
            cats = [c for c in cats if not c.startswith(("place.", "event.", "nature.", "concept."))]
        else:
            kind = self.tax.kind_for_categories(cats) or ("fictional_character" if "lit.character" in cats else "unknown")
        occ = []
        for v in claim_ids(e, "P106"):
            lab = label(vents.get(v), "ru") or label(vents.get(v), "en")
            if lab and lab not in occ:
                occ.append(lab)
        origin = self.origin(e, vents) if human else ("georgian" if in_georgia else "foreign" if claim_ids(e, "P17") else "unknown")
        g = None
        for v in claim_ids(e, "P21"):
            g = GENDER.get(v)
            if g:
                break
        f = EponymFacts(
            qid=q, kind=kind, ka=label(e, "ka"), ru=display_name(label(e, "ru"), human),
            en=display_name(label(e, "en"), human), gender=g if human else None,
            birth_year=year(e, "P569"), death_year=year(e, "P570"), origin=origin,
            occupations_ru=occ[:8], categories=cats[:6], evidence=why[:20],
            kawiki=sitelink(e, "kawiki"), ruwiki=sitelink(e, "ruwiki"),
            citizenship_ru=[label(vents.get(c), "ru") or label(vents.get(c), "en") for c in claim_ids(e, "P27")][:3],
            birthplace_ru=next((label(vents.get(c), "ru") or label(vents.get(c), "en") for c in claim_ids(e, "P19")), ""),
            in_georgia=in_georgia,
        )
        def labs(prop: str) -> list[str]:
            out = []
            for v in claim_ids(e, prop):
                ve = vents.get(v)
                lab = label(ve, "ru") or label(ve, "en") or label(ve, "ka")
                if lab and lab not in out:
                    out.append(lab)
            return out
        if human and not f.ru and f.ka:
            f.ru = _ka_to_ru(f.ka)         # display fallback; Georgian surnames are not declined
        f.works_ru = labs("P800")
        f.member_of_ru = labs("P463")
        f.short_ru = _short_ru(description(e, "ru"), occ, origin, f.works_ru, f.member_of_ru)
        return f


def multi_record(key: str, parts: list[EponymFacts], source: str, conf: float, review: bool) -> tuple[dict, list[str]]:
    """One ``persons_multi`` eponym for a family named after several QIDs."""
    def join(vals: list[str], sep: str) -> str:
        vals = [v for v in vals if v]
        return sep.join(vals) if vals else ""
    humans = all(p.kind == "person" for p in parts)
    cats: list[str] = []
    for p in parts:
        for c in p.categories:
            if c not in cats:
                cats.append(c)
    genders = {p.gender for p in parts}
    births = [p.birth_year for p in parts if p.birth_year is not None]
    deaths = [p.death_year for p in parts if p.death_year is not None]
    occ: list[str] = []
    for p in parts:
        occ += [o for o in p.occupations_ru if o not in occ]
    rec = {"qid": None, "key": "multi:" + "|".join(sorted(p.qid for p in parts)),
           "kind": "persons_multi" if humans else "group", "ka": join([p.ka for p in parts], " და "),
           "ru": join([p.ru for p in parts], " и "), "en": join([p.en for p in parts], " and "),
           "gender": genders.pop() if len(genders) == 1 else None,
           "birth_year": min(births) if births else None, "death_year": max(deaths) if deaths else None,
           "origin": "georgian" if any(p.origin == "georgian" for p in parts) else
           ("foreign" if all(p.origin == "foreign" for p in parts) else "unknown"),
           "short_ru": "; ".join(p.short_ru for p in parts if p.short_ru)[:120] or None,
           "occupations_ru": occ[:8] or None, "confidence": conf, "source": source, "needs_review": review}
    return rec, (cats + ["group.family"] if humans and "group.family" not in cats else cats)[:6]


def confident_rule(r: dict) -> str | None:
    """Return the confidence key when a rules-only eponym is safe to write without the LLM."""
    hint = r.get("hint", "")
    if hint == "event_date:date":
        return "rules_date"
    if hint == "no_eponym:numbered":
        return "rules_numbered"
    if hint == "toponym:route":
        return "rules_route"
    if r.get("kind") == "toponym" and "gazetteer" in r.get("evidence", []):
        sub = r.get("sub", "")
        if r.get("translit") is True or sub == "country" or sub.startswith("abroad_") or sub == "battle_place":
            return "rules_toponym_qid" if len(r.get("gaz_qids", [])) == 1 else "rules_toponym"
        if r.get("translit") is None and len(r.get("gaz_qids", [])) == 1 and sub != "tbilisi_district":
            return "rules_toponym_qid"
        if sub == "tbilisi_district" and r.get("translit") is not False:
            return "rules_toponym"
    return None


def rules_record(f: Family, r: dict, tax: Taxonomy, conf_key: str) -> tuple[dict, list[str]]:
    hint = r["hint"]
    cats = tax.heuristic(hint)
    kind = tax.kind_for_categories(cats) or r["kind"]
    ka = K.nominative(f.base_ka) if f.base_ka and K.has_ka(f.base_ka) and kind != "no_eponym" else (f.base_ka or None)
    rec = {"qid": None, "key": "fam:" + f.key, "kind": kind, "ka": ka or None, "ru": f.base_ru or None,
           "en": f.base_en or None, "gender": None, "birth_year": None, "death_year": None,
           "origin": "georgian" if hint.startswith("toponym:georgia") or hint == "toponym:tbilisi_district" else
           ("foreign" if hint.startswith(("toponym:abroad", "toponym:country")) else "unknown"),
           "short_ru": tax.ru.get(cats[0]) if cats else None, "occupations_ru": None,
           "confidence": CONF[conf_key], "source": "rules", "needs_review": False}
    return rec, cats


def decide_families(fams: list[Family], rules: dict, qids: dict, match: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in fams:
        q = qids.get(f.key)
        m = match.get(f.key) or {}
        if q:
            src = q["source"]
            reasons = ["qid_conflict"] if q.get("conflict") else []
            if m.get("status") == "unique" and m.get("chosen") and m["chosen"] not in q["qids"]:
                reasons.append("osm_vs_match")      # e.g. the OSM tag points to a living namesake
            conf = CONF["conflict"] if reasons else CONF[src]
            out[f.key] = {"qids": q["qids"], "source": src, "confidence": conf, "needs_review": bool(reasons),
                          "reasons": reasons}
        elif m.get("status") == "unique" and "control" not in m:
            conf = CONF["wd_match"] if m["reason"] == "single name match" else CONF["wd_match_pref"]
            out[f.key] = {"qids": [m["chosen"]], "source": "wd_match", "confidence": conf, "needs_review": False,
                          "reasons": []}
    return out


def run(con: sqlite3.Connection, fams: list[Family], rules: dict, qids: dict, match: dict,
        client: WikidataClient, out_dir=None) -> dict[str, dict]:
    tax = load_taxonomy()
    with stage(log, "enrich.facts", families=len(fams)) as st:
        decided = decide_families(fams, rules, qids, match)
        builder = EponymBuilder(client, tax)
        all_q = sorted({q for d in decided.values() for q in d["qids"]})
        gaz_q = sorted({r["gaz_qids"][0] for k, r in rules.items() if k not in decided and r
                        and confident_rule(r) == "rules_toponym_qid"})
        facts = builder.build(all_q)
        builder.build(gaz_q)                     # prefetch toponym items found by the gazetteer
        st["qids"], st["qids_with_facts"] = len(all_q), len(facts)
        state: dict[str, dict] = {}
        kinds = collections.Counter()
        with con:
            clear_provenance(con, ["eponym"], ["osm", "wd_p138", "wd_match", "rules"])
            linked: set[str] = set()
            for f in fams:
                d = decided.get(f.key)
                r = rules.get(f.key) or {}
                if d:
                    parts = [facts[q] for q in d["qids"] if q in facts]
                    if not parts:
                        state[f.key] = {"status": "queue", "reasons": ["qid_unavailable"], "qids": d["qids"]}
                        continue
                    reasons = list(d["reasons"])
                    if len(parts) == 1:
                        p = parts[0]
                        rec = p.record(d["source"], d["confidence"], False)
                        cats = list(p.categories)
                        if r.get("hint") == "event_date:date" and not p.kind == "person":
                            # "9 აპრილის" with an OSM QID of the 1989 events: still a memorial date
                            rec["kind"] = "event_date"
                            cats = ["event.date"] + [c for c in cats if c.startswith("event.") and c != "event.date"]
                        review = d["needs_review"] or rec["kind"] == "unknown"
                        rec["needs_review"] = review
                        if rec["kind"] == "unknown":
                            reasons.append("qid_kind_unknown")
                        eid = upsert_eponym(con, rec)
                        set_categories(con, eid, cats, "wikidata")
                        kinds[rec["kind"]] += 1
                    else:
                        rec, cats = multi_record(f.key, parts, d["source"], d["confidence"], d["needs_review"])
                        eid = upsert_eponym(con, rec)
                        set_categories(con, eid, cats, "wikidata")
                        kinds[rec["kind"]] += 1
                    link_family(con, f.key, eid)
                    linked.add(f.key)
                    add_provenance(con, "family", f.key, "eponym", "|".join(d["qids"]), d["source"],
                                   None, d["confidence"])
                    state[f.key] = {"status": "queue" if reasons else "decided", "source": d["source"],
                                    "qids": d["qids"], "reasons": reasons}
                    continue
                ck = confident_rule(r) if r else None
                if ck:
                    rec, cats = rules_record(f, r, tax, ck)
                    if ck == "rules_toponym_qid":
                        gq = r["gaz_qids"][0]
                        pf = builder.build([gq]).get(gq)
                        if pf is not None and pf.kind in ("toponym", "event_date", "group"):
                            rec = pf.record("rules", CONF[ck], False)
                            cats = pf.categories or cats
                    eid = upsert_eponym(con, rec)
                    set_categories(con, eid, cats, "rules")
                    link_family(con, f.key, eid)
                    linked.add(f.key)
                    kinds[rec["kind"]] += 1
                    add_provenance(con, "family", f.key, "eponym", rec.get("qid") or rec["key"], "rules", r["hint"],
                                   rec["confidence"])
                    state[f.key] = {"status": "decided", "source": "rules", "hint": r["hint"], "reasons": []}
                    continue
                state[f.key] = {"status": "queue", "reasons": queue_reasons(r, match.get(f.key))}
            st["unlinked_stale"] = unlink_stale(con, linked, ("osm", "wd_p138", "wd_match", "rules"))
            st["orphans_deleted"] = delete_orphans(con)
        from pipeline.enrich.review import apply_decisions  # late import: review uses the builder
        st["review_applied"] = apply_decisions(con, builder)
        write_json(stage_path("facts", out_dir), state)
        cnt = collections.Counter(s["status"] for s in state.values())
        reasons = collections.Counter(r for s in state.values() for r in s.get("reasons", []))
        st.update(decided=cnt.get("decided", 0), queue=cnt.get("queue", 0),
                  **{f"kind_{k}": v for k, v in sorted(kinds.items())})
        for r, n in reasons.most_common():
            log.info("facts: queue reason %-22s %5d", r, n)
    return state


def queue_reasons(r: dict, m: dict | None) -> list[str]:
    kind = (r or {}).get("kind", "unknown")
    if kind == "person":
        if m:
            st = m.get("status")
            if st == "ambiguous" and "single-token" in m.get("reason", ""):
                return ["person_single_token"]
            return [f"person_{st}"] if st else ["person_unmatched"]
        return ["person_unmatched"]
    if kind == "persons_multi":
        return ["persons_multi"]
    if kind == "toponym":
        return ["toponym_uncertain"]
    return [kind or "unknown"]
