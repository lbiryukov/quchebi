"""Reference stage of Russian-language cities: wikitext helpers, heritage lists, matching."""
from pipeline.ru.batches import article_text, infobox
from pipeline.ru.fetch import clean_wiki, looks_like_street_article, norm, parse_monuments
from pipeline.ru.merge import clip, similar, stems, year_ok

ARTICLE = """{{Улица
|название = Улица Примерная
|прежние названия = Старый переулок
|название в честь = [[Иванов, Иван Иванович|И. И. Иванова]]
|Изображение = Example.jpg
}}
'''Примерная улица''' — улица в [[Хамовники|Хамовниках]], [[Москва]].<ref>Источник</ref>

== Происхождение названия ==
Названа в 1922 году в честь [[Иванов, Иван Иванович|И. И. Иванова]]{{sfn|Справочник|с=5}}.

== Примечательные здания и сооружения ==
* № 5, стр. 1 — [[Доходный дом]] (1911, архитектор [[Струков, Николай Дмитриевич|Н. Д. Струков]]).
* № 7 — жилой дом.

== Примечания ==
{{примечания}}

== Ссылки ==
* [http://example.org Сайт]
"""


def test_infobox_and_article_text():
    box = infobox(ARTICLE)
    assert box["прежние названия"] == "Старый переулок"
    assert box["название в честь"] == "И. И. Иванова"
    assert "изображение" not in box
    box2, text = article_text(ARTICLE)
    assert box2 == box
    assert "## Происхождение названия" in text and "Названа в 1922 году в честь И. И. Иванова." in text
    assert "- № 5, стр. 1 — Доходный дом (1911, архитектор Н. Д. Струков)." in text
    assert "Примечания" not in text and "Ссылки" not in text and "Источник" not in text and "example.org" not in text


def test_clean_wiki_and_norm():
    assert clean_wiki("[[Толстой, Лев Николаевич|Л. Н. Толстой]] жил ''здесь''<ref>1</ref> {{sfn|x}}") == "Л. Н. Толстой жил здесь"
    assert norm("Улица Льва Толстого (Москва)") == "улица льва толстого"
    assert norm("Усачёва  улица") == "усачева улица"


def test_looks_like_street_article():
    assert looks_like_street_article(ARTICLE)
    assert not looks_like_street_article("'''Примерная''' — неоднозначность: {{неоднозначность}} улица, Москва")
    assert not looks_like_street_article("Статья про реку в Сибири.")


def test_parse_monuments():
    text = """{{monument
|name = Главный дом усадьбы [[Лопухины|Лопухиных]]
|type = architecture
|address = Пречистенка, 11/8
|year = 1817—1822
|author = [[Григорьев, Афанасий Григорьевич|А. Г. Григорьев]]
|lat = 55.7434 |long = 37.5973
|knid = 7710702000
|protection = Ф
|wdid = Q123
}}
{{monument
|name = Без координат
|lat = |long =
}}"""
    mons = parse_monuments(text)
    assert len(mons) == 1
    m = mons[0]
    assert m["name"] == "Главный дом усадьбы Лопухиных" and m["author"] == "А. Г. Григорьев"
    assert m["year"] == "1817—1822" and m["protection"] == "Ф" and m["wdid"] == "Q123"
    assert abs(m["lat"] - 55.7434) < 1e-6


def test_matching_helpers():
    assert similar("Музей-усадьба Л. Н. Толстого «Хамовники»", "Усадьба Л. Н. Толстого в Хамовниках")
    assert not similar("Доходный дом Исакова", "Особняк Кекушевой")
    assert stems("Жилой дом") == set()
    assert year_ok("1922") == 1922 and year_ok(3000) is None and year_ok(None) is None
    assert clip("а" * 50, 10).endswith("…") and len(clip("а" * 50, 10)) == 10
