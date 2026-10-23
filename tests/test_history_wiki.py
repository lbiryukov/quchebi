"""ka/ru Wikipedia infobox and text parsers on synthetic wikitext."""
import json
from pathlib import Path

from pipeline.history import wiki_parse as W

FIX = json.loads((Path(__file__).parent / "fixtures" / "history" / "wiki_pages.json").read_text(encoding="utf-8"))


def test_infobox_and_markup():
    page = FIX["ka"]["ზაალ ბერიძის ქუჩა"]
    ib = W.infobox(page["text"], W.KA_INFOBOX)
    assert ib["რაიონი"] == "[[გლდანის რაიონი]]"
    assert W.strip_markup("[[ხევი|ხევიდან]] [[სხვიტორის ქუჩა]]მდე<ref>x</ref>") == "ხევიდან სხვიტორის ქუჩამდე"
    assert W.split_names("ა ქუჩა<br/>ბ ქუჩა (ერთ მონაკვეთს), გ ქუჩა") == ["ა ქუჩა", "ბ ქუჩა (ერთ მონაკვეთს)", "გ ქუჩა"]


def test_parse_ka_article():
    rec = W.parse_ka("ზაალ ბერიძის ქუჩა", FIX["ka"]["ზაალ ბერიძის ქუჩა"])
    f = rec["facts"]
    assert rec["source"] == "kawiki" and rec["page"] == "101" and f["qid"] == "Q900001"
    assert f["district"] == "გლდანის რაიონი" and f["quarter"] == "ავჭალა"
    names = {x["name_ka"]: x for x in f["former"]}
    # text parser: years; infobox: extra names (parenthetical alternative kept, note dropped)
    assert names["ს. ზალიკაშვილის ქუჩა"]["from_year"] == 1994
    assert names["ს. ზალიკაშვილის ქუჩა"]["to_year"] == 1995
    assert "ვ. ლენინის ქუჩა" in names
    assert names["ურთის ქუჩა"]["note"] == "ერთ მონაკვეთს"
    assert "ბაღის ქუჩა" in names and "ზემო ბაღის ქუჩა" in names
    assert f["named_after"]["name_ka"] == "ზაალ ბერიძე"
    assert (f.get("named_year") or {}).get("year") == 1995


def test_parse_ka_defunct_street():
    rec = W.parse_ka("ძველი ბაზრის ქუჩა (თბილისი)", FIX["ka"]["ძველი ბაზრის ქუჩა (თბილისი)"])
    f = rec["facts"]
    assert f.get("defunct") is True
    assert [x["name_ka"] for x in f["former"]] == ["ხარაზების რიგი"]
    assert f["former"][0]["attested"] == [1876]


def test_parse_ru_article():
    rec = W.parse_ru("Улица Заала Беридзе", FIX["ru"]["Улица Заала Беридзе"])
    f = rec["facts"]
    assert f["qid"] == "Q900001"
    assert f["former"][0] == {"name_ru": "Ленина", "confidence": 0.8, "to_year": 1994}
    assert f["former"][1]["name_ru"] == "Заликашвили"
    assert (f["former"][1]["from_year"], f["former"][1]["to_year"]) == (1994, 1995)
    assert f["named_after"]["name_ru"] == "Беридзе, Заал"
