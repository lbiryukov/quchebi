"""Stage ``rules``: rule-based (no LLM) guess of the eponym kind of every family.

Signals: gazetteers (places in Georgia/abroad, Tbilisi areas), Georgian given names (Wikidata +
data-driven), titles and regnal numbers, surname suffixes, and whether name:ru/en is a
transliteration (proper noun) or a translation (common noun). Prototype check on 192 OSM-tagged
families: person recall 96.7%, precision 100%.

Output ``data/enrich/stages/rules.json`` ({family.key: {...}}) and ``family.kind_hint`` =
``"<eponym kind>:<sub>"``.
"""
from __future__ import annotations

import collections
import re
import sqlite3
from typing import Iterable

from pipeline.enrich import ka as K
from pipeline.enrich.common import Family, stage_path, write_json
from pipeline.enrich.gazetteer import Gazetteer
from pipeline.log import get_logger, stage

log = get_logger(__name__)

NUM_WORDS = {"ორი", "სამი", "ოთხი", "ხუთი", "ექვსი", "შვიდი", "რვა", "ცხრა", "ათი", "თერთმეტი", "თორმეტი", "ცამეტი",
             "თოთხმეტი", "ოცი", "ასი", "სამასი", "ორმოცი"}
KIN_WORDS = {"ძმები", "ძმა", "ძმების", "დები", "და-ძმა", "დედა-შვილი", "მამა-შვილი", "ოჯახის"}
COMMON_ATTR = {"კუნძულ", "ქალაქ", "სოფელ", "მდინარე", "მთა", "ტბა", "ზღვა", "ხეობა", "ველი", "ძველი", "ახალი", "დიდი",
               "პატარა", "ზემო", "ქვემო", "შუა", "პირველი", "მეორე", "თეთრი", "შავი", "წითელი", "მწვანე", "ლურჯი"}
ADJ_STOP = {"ძველი", "ახალი", "დიდი", "პატარა", "ზემო", "ქვემო", "შუა", "თეთრი", "შავი", "წითელი", "მწვანე", "ლურჯი",
            "პირველი", "მეორე", "კუნძულ", "ქალაქ", "სოფელ", "მდინარე", "მთა", "ტბა", "ზღვა"}
GROUP_WORDS = {"გმირთა", "გმირის", "გმირების", "მამის", "მამათა", "მოციქულთა", "მეომართა", "იუნკერთა", "იუნკრების",
               "ჯარისკაცთა", "დედათა", "ქალთა", "მოწამეთა", "დივიზიას", "დივიზიის", "არაგველის", "არაგველთა",
               "კადეტთა", "მებრძოლთა"}
PERSON_SUBS = {"full_name", "full_name_weak", "title_epithet", "mononym", "surname_only"}

# rules class -> eponym kind (migrations/001_init.sql)
KIND_OF = {"person": "person", "multi": "persons_multi", "group": "group", "toponym": "toponym",
           "date_event": "event_date", "concept": "concept", "proper_unknown": "unknown", "numbered": "no_eponym",
           "other": "unknown"}


def data_given_names(fams: Iterable[Family], known: set[str]) -> set[str]:
    """First tokens of 2+ token eponyms whose last token has a surname suffix (seen >= 2 times)."""
    cnt: collections.Counter = collections.Counter()
    for f in fams:
        names = {m.ka for m in f.members} or {f.base_ka}
        toks = f.base_ka.split()
        if len(toks) >= 2 and toks[-1].endswith(K.SURNAME_SUFFIX_ALL) and not toks[0].endswith("ის") \
                and not re.match(r"^\d", toks[0]) and toks[0] not in K.TITLES and not toks[0].endswith("."):
            cnt[toks[0]] += len(names)
    return {t for t, c in cnt.items() if c >= 2 and t not in known and t not in ADJ_STOP}


def _titles_in(toks: list[str]) -> list[str]:
    out = [K.TITLES[t] for t in toks if t in K.TITLES]
    if any(t in K.EPITHET for t in toks):
        out.append("epithet")
    return out


def classify(f: Family, gaz: Gazetteer, gn_all: set[str]) -> dict:
    core = f.base_ka
    rb, en = f.base_ru, f.base_en
    res: dict = {"cls": None, "sub": None, "evidence": [], "geo_hits": [], "gaz_qids": [], "titles": [],
                 "flags": [], "translit": None}
    ph = f.parser_hint
    if ph in ("non_ka", "route", "numbered") or not core:
        ph = ph or "numbered"
        res["cls"] = {"non_ka": "other", "route": "toponym", "numbered": "numbered"}.get(ph, "other")
        res["sub"] = ph
        res["evidence"].append("parser:" + ph)
        return res
    toks = core.split()
    first = toks[0]
    tc = K.translit_consistent(core, rb, en)
    res["translit"] = tc
    sc = K.surname_consistent(f.nom_ka or K.nominative(core), rb, en)
    res["titles"] = _titles_in(toks)

    def done(cls: str, sub: str, ev: str) -> dict:
        res.update(cls=cls, sub=sub)
        res["evidence"].append(ev)
        if cls == "person" and sc is False:
            res["flags"].append("ka_ru_en_mismatch")
        return res

    if re.match(r"^\d+$", first) and len(toks) >= 2 and toks[1] in K.MONTHS:
        return done("date_event", "date", "digit+month")
    if "სწორი" in toks or "მოციქულთასწორი" in toks:
        res["titles"].append("saint")
        return done("person", "title_epithet", "saint epithet")
    if any(t in KIN_WORDS for t in toks) or (len(toks) == 1 and toks[0].endswith(("ეების", "შვილების", "იანების", "ძეების"))):
        return done("multi", "multi", "kin word / plural surname")
    if (re.match(r"^\d+(-ე)?$", first) or first in NUM_WORDS) or any(t in GROUP_WORDS for t in toks) \
            or (len(toks) == 1 and toks[0].endswith("თა")):
        if " და " not in core:
            return done("group", "collective", "number/group word")
    if " და " in core or (len(toks) >= 2 and toks[-1].endswith(("ების", "ეების")) and first in gn_all):
        if any(t in gn_all for t in toks) or any(t in K.TITLES for t in toks):
            return done("multi", "multi", "'და'/plural surname")
    regnal = any(t in K.REGNAL and i > 0 and (toks[i - 1] in gn_all or toks[i - 1] in K.TITLES)
                 for i, t in enumerate(toks))
    roman = len(toks) >= 2 and re.search(r"(^|\s)[IVX]+(-ის)?(\s|$)", core)
    if any(t in K.TITLES for t in toks) or regnal or any(t in K.EPITHET for t in toks) or roman:
        if regnal or roman:
            res["titles"].append("regnal")
        return done("person", "title_epithet", "title/regnal/epithet")
    if len(toks) >= 2 and (first in gn_all or first.rstrip(".") in gn_all or re.match(r"^[ა-ჿ]\.$", first)) \
            and (toks[-1].endswith(("ის", "ს")) or first in gn_all):
        return done("person", "full_name", "given_name:" + ("wd" if first in gaz.given_names else "data"))
    hits = gaz.lookup(core)
    if hits:
        grp = sorted({h[0] for h in hits})
        scope = sorted({h[1] for h in hits})
        res["geo_hits"] = sorted({f"{h[0]}/{h[1]}/{h[2]}" for h in hits})[:8]
        res["gaz_qids"] = sorted({h[2] for h in hits if h[2].startswith("Q")})
        if tc is False and not (rb and K.ru_is_adjective(rb)) and not any(h[1] == "abroad" for h in hits):
            res["evidence"].append("gazetteer hit but ru/en is a translation")
        else:
            def pick(prefix: str) -> str:
                g = [x for x in ("region", "settlement", "water", "relief", "ethnos") if x in grp]
                return prefix + (g[0] if g else "place")
            if "battle" in grp:
                sub = "battle_place"
            elif "country" in grp:
                sub = "country"
            elif "tbilisi" in scope or "neighbourhood" in grp:
                sub = "tbilisi_district"
            elif "georgia" in scope:
                sub = pick("georgia_")
            else:
                sub = pick("abroad_")
            return done("toponym", sub, "gazetteer")
    if len(toks) == 1 and toks[0].endswith(K.SURNAME_SUFFIX_STRONG):
        return done("person", "surname_only", "surname suffix")
    if len(toks) == 1:
        nom = K.stem_gen(toks[0])
        parts = [nom] + nom.split("-")
        if any(x in gn_all or (x + "ი") in gaz.given_names or x.rstrip("ი") in gn_all for x in parts if len(x) > 2):
            return done("person", "mononym", "given-name mononym")
    if 2 <= len(toks) <= 3 and toks[-1].endswith(("ის", "ს")) and first[-1] not in "აეიოუ" and \
            not first.endswith("ის") and first not in COMMON_ATTR and tc is not False and not re.match(r"^\d", first):
        return done("person", "full_name_weak", "truncated first token")
    if len(toks) >= 2 and toks[-1].endswith(K.SURNAME_SUFFIX_ALL) and tc is not False and not first.endswith("ის") \
            and first not in COMMON_ATTR and first not in ADJ_STOP:
        return done("person", "full_name_weak", "surname-like last token")
    if tc is False:
        return done("concept", "translated_common_noun", "ru/en is a translation")
    if rb and K.ru_is_adjective(rb):
        return done("concept", "ru_adjective", "ru adjective")
    if toks[-1].endswith("ების"):
        return done("concept", "plural_or_abstract", "-ების")
    if tc is True:
        return done("proper_unknown", "transliterated", "ru/en transliteration; not in gazetteer")
    return done("proper_unknown", "no_ru_en", "no ru/en")


def kind_hint(r: dict) -> str:
    return f"{KIND_OF.get(r['cls'], 'unknown')}:{r['sub']}"


def run(con: sqlite3.Connection, fams: list[Family], gaz: Gazetteer, out_dir=None) -> dict[str, dict]:
    with stage(log, "enrich.rules", families=len(fams)) as st:
        gn_data = data_given_names(fams, gaz.given_names)
        gn_all = gaz.given_names | gn_data
        out: dict[str, dict] = {}
        for f in fams:
            r = classify(f, gaz, gn_all)
            r["kind"] = KIND_OF.get(r["cls"], "unknown")
            r["hint"] = kind_hint(r)
            r["single_token"] = len(K.core_tokens(f.base_ka)) <= 1 if f.base_ka else False
            out[f.key] = r
        with con:
            con.executemany("UPDATE family SET kind_hint=? WHERE key=?", [(r["hint"], k) for k, r in out.items()])
        write_json(stage_path("rules", out_dir), out)
        cnt = collections.Counter(r["kind"] for r in out.values())
        st.update(given_names_data=len(gn_data), **{f"kind_{k}": v for k, v in sorted(cnt.items())})
        for k, v in cnt.most_common():
            log.info("rules: %-14s %5d (%4.1f%%)", k, v, 100 * v / max(1, len(out)))
    return out


def evaluate(rules: dict[str, dict], truth_person: dict[str, bool]) -> dict:
    """Person recall/precision of the rules against families with a known QID (P31=Q5 or not)."""
    tp = fn = fp = tn = 0
    errors = []
    for key, is_person in truth_person.items():
        r = rules.get(key)
        if r is None:
            continue
        pred = r["kind"] in ("person", "persons_multi")
        if is_person and pred:
            tp += 1
        elif is_person:
            fn += 1
            errors.append({"key": key, "truth": "person", "pred": r["hint"]})
        elif pred:
            fp += 1
            errors.append({"key": key, "truth": "non_person", "pred": r["hint"]})
        else:
            tn += 1
    return {"tp": tp, "fn": fn, "fp": fp, "tn": tn, "recall": round(tp / max(1, tp + fn), 4),
            "precision": round(tp / max(1, tp + fp), 4), "errors": errors[:50]}
