"""Fact parser of encyclopedia articles: 30 hand-labelled synthetic articles + helpers."""
import json
from pathlib import Path

import pytest

from pipeline.history import enc_parse as P
from pipeline.history import georgian as G

FIX = Path(__file__).parent / "fixtures" / "history" / "enc_articles.json"
CASES = json.loads(FIX.read_text(encoding="utf-8"))["cases"]


def test_there_are_30_labelled_articles():
    assert len(CASES) == 30


@pytest.mark.parametrize("case", CASES, ids=[c["title"] for c in CASES])
def test_labelled_article(case):
    facts = P.parse_article(case["title"], case["body"], case["bold"]).as_json()
    exp = case["expect"]
    got = facts.get("former", [])
    assert [g["name_ka"] for g in got] == [x["name_ka"] for x in exp["former"]]
    for g, x in zip(got, exp["former"]):
        for k in ("from_year", "to_year", "attested", "note"):
            assert g.get(k) == x.get(k), (x["name_ka"], k)
    if "named_year" in exp:
        assert (facts.get("named_year") or {}).get("year") == exp["named_year"]
    for k in ("district_2008", "place", "route_from", "route_to", "lanes", "sub_former"):
        if k in exp:
            assert facts.get(k) == exp[k], k
    if "origin_year" in exp:
        assert (facts.get("origin") or {}).get("year") == exp["origin_year"]
    if "between_contains" in exp:
        assert exp["between_contains"] in facts.get("between", "")


def test_overall_accuracy_on_labelled_set():
    """Name-level precision/recall over the labelled set must stay high."""
    tp = fp = fn = 0
    for c in CASES:
        got = {f.name_ka for f in P.parse_article(c["title"], c["body"], c["bold"]).former}
        exp = {f["name_ka"] for f in c["expect"]["former"]}
        tp += len(got & exp)
        fp += len(got - exp)
        fn += len(exp - got)
    assert tp / max(1, tp + fp) >= 0.95
    assert tp / max(1, tp + fn) >= 0.95


def test_time_expressions():
    t = G.find_times("1959-იდან – ისევ ატენის, 1946-მდე, 1925–26 წლების")
    assert [(x.year, x.rel) for x in t] == [(1959, "from"), (1946, "until"), (1925, "at")]
    assert t[2].year_to == 1926
    t = G.find_times("XX ს. 30-იან წლებში და XIX ს. II ნახევარში", 20)
    assert [(x.year, x.prec) for x in t] == [(1930, "decade"), (1875, "century")]
    t = G.find_times("XIX საუკუნის დასასრულს")
    assert t[0].year == 1895


def test_kind_forms():
    assert G.kind_of("მოედნიდან") == "მოედანი"
    assert G.kind_of("შეს.") == "შესახვევი"
    assert G.kind_of("ქუჩად") == "ქუჩა"
    assert G.to_nominative_phrase("ილია ჭავჭავაძის გამზირიდან") == "ილია ჭავჭავაძის გამზირი"


def test_title_abbreviation():
    rx = P.title_abbrev_regex("აფხაზის ქუჩა")
    assert rx.sub("⟨CUR⟩", "1938 – ა. ქ. ქუჩაზე, ა. ქ-ის #1-ში") == "1938 – ⟨CUR⟩ ქუჩაზე, ⟨CUR⟩ #1-ში"
    assert rx.sub("⟨CUR⟩", "აქვს 1 შეს.") == "აქვს 1 შეს."


def test_xref_and_lane_variants():
    x = P.parse_xref("აგურის ქუჩა", "იხ. ბესარიონ ბელინსკის ქუჩა, თამარ ჭოველიძის ქუჩა.", "(ვერე)")
    assert x == {"old": "აგურის ქუჩა", "place": "ვერე",
                 "targets": ["ბესარიონ ბელინსკის ქუჩა", "თამარ ჭოველიძის ქუჩა"]}
    assert P.lane_variants("ანტონ კათალიკოსის ქუჩის II ჩიხი") == [
        "ანტონ კათალიკოსის ქუჩის II ჩიხი", "ანტონ კათალიკოსის II ჩიხი"]


def test_bio_parse_and_genitives():
    b = P.parse_bio("ბებელი", "ავგუსტ (1840–1913), გერმანიისა და საერთაშ. მუშათა მოძრაობის მოღვაწე.")
    assert b["given"] == "ავგუსტ" and b["birth"] == 1840 and b["death"] == 1913
    b = P.parse_bio("აბაშიძე", "გრიგოლ გრიგოლის ძე (1914–94), პოეტი, პროზაიკოსი. საქართვ. მეცნ. აკად.")
    assert b["given"] == "გრიგოლ" and b["patronymic"] == "გრიგოლის ძე" and b["death"] == 1994
    assert b["occupation_ka"] == "პოეტი, პროზაიკოსი"
    assert "ტაბიძის" in P.bio_genitives("ტაბიძე")
    assert "წერეთლის" in P.bio_genitives("წერეთელი")
    assert "კოსტავას" in P.bio_genitives("კოსტავა")
    assert "ასათიანის" in P.bio_genitives("ასათიანი")


def test_same_name():
    assert P._same_name("ს. ხიმშიაშვილის ქუჩა", "სელიმ (სოლომონ) ხიმშიაშვილის ქუჩა")
    assert not P._same_name("ა. აბაშიძის ქუჩა", "ვასილ აბაშიძის ქუჩა")
    assert not P._same_name("მცხეთის I შესახვევი", "მცხეთის III შესახვევი")
    assert not P._same_name("ატენის შესახვევი", "ატენის ქუჩა")
