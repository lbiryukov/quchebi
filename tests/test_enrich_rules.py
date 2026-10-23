"""Rule classifier on labelled examples (real Tbilisi names from the fixture + hand-made cases)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "enrich"))
from enrich_fake import fixture, make_db  # noqa: E402

from pipeline.config import BUILD, ENRICH
from pipeline.enrich import gazetteer as G
from pipeline.enrich import ka as K
from pipeline.enrich import rules as R
from pipeline.enrich.common import Family, load_families, paths


@pytest.fixture()
def env(tmp_path):
    paths.set(tmp_path / "enrich", tmp_path / "build")
    con = make_db()
    fams = load_families(con)
    gaz = G.build(con, None, rows_by_name=fixture("gazetteer.json"))
    yield con, fams, gaz
    paths.set(ENRICH, BUILD)


EXPECTED = {
    "26 მაის": "event_date:date",
    "#21-ე ქუჩა": "no_eponym:numbered",
    "#Tsitsamuri — Avchala": "unknown:non_ka",
    "17 გმირ შინდისლ": "group:collective",
    "იუნკერთ": "group:collective",
    "გაბუნიებ ლეო ნოდრ": "persons_multi:multi",
    "კოტ სოს წერეთლებ": "persons_multi:multi",
    "ლიბნ": "toponym:country",
    "ატოც": "toponym:georgia_settlement",
    "მოჭიქულ": "concept:translated_common_noun",
    "უნივერსიტეტ": "concept:ru_adjective",
    "ტირიფებ": "concept:plural_or_abstract",
    "გელოვნ მარშლ": "person:title_epithet",
    "მოციქულთ ნინ სწრ": "person:title_epithet",
    "ალექსანდრ პუშკინ": "person:full_name",
    "გივ კარტოზ": "person:full_name",
    "აშოტ კურაპალატ": "person:full_name_weak",
}


def test_fixture_families_classified_as_labelled(env):
    con, fams, gaz = env
    out = R.run(con, fams, gaz)
    for key, hint in EXPECTED.items():
        assert out[key]["hint"] == hint, (key, out[key])
    # kind_hint is written to the DB in the "<kind>:<sub>" format
    rows = dict(con.execute("SELECT key, kind_hint FROM family").fetchall())
    assert rows["26 მაის"] == "event_date:date"
    assert (paths.stages / "rules.json").exists()


def test_rules_vs_known_qids_person_precision(env):
    con, fams, gaz = env
    out = R.run(con, fams, gaz)
    ents = fixture("entities.json")
    truth = {}
    for f in fams:
        q = next((m.wikidata for m in f.members if m.wikidata), None)
        if q and ";" not in q and q in ents:
            p31 = [c["mainsnak"]["datavalue"]["value"]["id"] for c in ents[q]["claims"].get("P31", [])]
            truth[f.key] = "Q5" in p31
    ev = R.evaluate(out, truth)
    assert ev["fp"] == 0 and ev["precision"] == 1.0
    assert ev["recall"] >= 0.8


def fam(base_ka, ru="", en=""):
    return Family(id=1, key=base_ka, base_ka=base_ka, base_ru=ru, base_en=en, nom_ka=K.nominative(base_ka),
                  kind_hint=None, eponym_id=None)


@pytest.mark.parametrize("base,ru,en,hint", [
    ("ჭავჭავაძის", "Чавчавадзе", "Chavchavadze", "person:surname_only"),
    ("ერეკლე II", "Ираклия II", "", "person:title_epithet"),
    ("მეფე ფარნავაზის", "Царя Парнаваза", "", "person:title_epithet"),
    ("ვარდების", "Роз", "Roses", "concept:translated_common_noun"),
    ("9 აპრილის", "9 Апреля", "", "event_date:date"),
    ("ძმები ზდანევიჩების", "Братьев Зданевичей", "", "persons_multi:multi"),
    ("300 არაგველის", "300 Арагвинцев", "", "group:collective"),
])
def test_hand_made_examples(env, base, ru, en, hint):
    _, fams, gaz = env
    gn = gaz.given_names | R.data_given_names(fams, gaz.given_names)
    r = R.classify(fam(base, ru, en), gaz, gn)
    assert R.kind_hint(r) == hint, r


def test_single_token_flag_and_ka_ru_mismatch(env):
    con, fams, gaz = env
    out = R.run(con, fams + [fam("ჭავჭავაძის", "Чавчавадзе")], gaz)
    assert out["ჭავჭავაძის"]["single_token"] is True
    gn = gaz.given_names
    r = R.classify(fam("ილია ჭავჭავაძის", "Ивана Петрова", "Ivan Petrov"), gaz, gn | {"ილია"})
    assert r["sub"] == "full_name" and "ka_ru_en_mismatch" in r["flags"]


def test_translit_and_genitive_helpers():
    assert K.translit_consistent("გორის", "Гори", "Gori") is True
    assert K.translit_consistent("ვარდების", "Роз", "Roses") is False
    assert K.nominative("ილია ჭავჭავაძის") == "ილია ჭავჭავაძე"
    assert "ავლაბრის" in K.gen_forms("ავლაბარი")
    assert K.norm_key("ფარნავაზ მეფის") == K.norm_key("მეფე ფარნავაზი")
    assert K.split_ka_name("ლადო ასათიანის II შესახვევი")["base"] == "ლადო ასათიანის"
    assert K.split_ka_name("21-ე ქუჩა")["kind"] == "numbered"
    assert K.ru_base("2-й переулок Ладо Асатиани") == "Ладо Асатиани"
    assert K.en_base("Lado Asatiani 2nd Lane") == "Lado Asatiani"
