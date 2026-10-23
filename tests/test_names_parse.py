"""Street-name parsing: hand-made cases covering the hard constructions found in Tbilisi OSM data."""
import pytest

from pipeline.names.parse import (
    ParsedName,
    key_tok,
    nominative_tok,
    normalize_ka,
    normalize_ru_name,
    parse_name,
    strip_type,
)

# (ka, ru, en, expected subset of ParsedName fields)
CASES = [
    # --- plain types and ordinals
    ("ლადო ასათიანის ქუჩა", None, None,
     dict(kind="street", type_word="ქუჩა", ord=None, regnal=None, base_ka="ლადო ასათიანის",
          eponym_nom_ka="ლადო ასათიანი", is_derived=False, parent_family_key=None)),
    ("ლადო ასათიანის II შესახვევი", "2-й переулок Ладо Асатиани", "Lado Asatiani 2nd Lane",
     dict(kind="lane", type_word="შესახვევი", ord=2, regnal=None, base_ka="ლადო ასათიანის",
          is_derived=True, base_ru="Ладо Асатиани", base_en="Lado Asatiani")),
    ("ილია ჭავჭავაძის IV ჩიხი", None, None, dict(kind="dead_end", ord=4, is_derived=True)),
    ("რუსთაველის II ჩიხი", None, None, dict(kind="dead_end", ord=2, base_ka="რუსთაველის")),
    ("ფარნავაზ მეფის I ჩიხი", None, None, dict(kind="dead_end", ord=1, regnal=None)),
    ("მარიამ უგრელიძის 1-ლი შეს.", "Мариам Угрелидзе 1-й Пер.", None,
     dict(kind="lane", type_word="შესახვევი", ord=1, base_ka="მარიამ უგრელიძის")),
    ("ვალენტინი  თოფურიძის მე-2 შესასვლელი", None, None,
     dict(kind="passage", type_word="შესასვლელი", ord=2, base_ka="ვალენტინი თოფურიძის")),
    ("ლადო ასათიანის 30-ე ჩიხი", None, None, dict(kind="dead_end", ord=30)),
    ("კვიპაროსის ქუჩა 1. შესახვევი", None, None,
     dict(kind="lane", ord=1, base_ka="კვიპაროსის", parent_kind="street")),
    ("ნავთლუღის მეორე შესახვევი", None, None, dict(kind="lane", ord=2, regnal=None, base_ka="ნავთლუღის")),
    ("XII შესახვევი", "12-й переулок", None,
     dict(kind="lane", ord=12, base_ka="", eponym_nom_ka=None, is_derived=False)),
    ("მე-12 ქუჩა", None, None, dict(kind="street", ord=12, base_ka="")),
    ("გიგო ზაზიაშვილი III შესახვევი", None, None,
     dict(kind="lane", ord=3, regnal=None, base_ka="გიგო ზაზიაშვილი")),   # nominative surname, not regnal
    ("ფერმწერთა III ჩიხი", None, None, dict(kind="dead_end", ord=3, regnal=None)),  # genitive plural
    ("რომან გვენცაძე V ჩიხი", None, None, dict(ord=5, regnal=None)),
    # --- regnal numbers vs lane ordinals
    ("ერეკლე II შესახვევი", None, None,
     dict(kind="lane", ord=None, regnal="II", base_ka="ერეკლე II", eponym_nom_ka="ერეკლე II")),
    ("ერეკლე II შესახვევი", "2-ой переулок Эрекле Меори", None,
     dict(ord=None, regnal="II", flags_has=["regnal_mismatch_ru"])),
    ("ერეკლე I ჩიხი", "тупик Эрекле Пирвели", None, dict(kind="dead_end", ord=None, regnal="I")),
    ("ვახტანგ VI ქუჩა", None, None, dict(kind="street", regnal="VI", ord=None)),
    ("თეიმურაზ I ქუჩა", "улица Теймураза I", None, dict(regnal="I", ord=None)),
    ("მეფე ფარნავაზი I ქუჩა", "1-я улица Мепе Парнавази", None,
     dict(kind="street", ord=1, regnal=None, base_ka="მეფე ფარნავაზი")),  # ru resolves the ambiguity
    ("თეიმურაზ I-ის ქუჩა", None, None, dict(regnal="I", ord=None, eponym_nom_ka="თეიმურაზ I")),
    ("წმინდა ვახტანგ III-ის ქუჩა", None, None, dict(regnal="III", ord=None)),
    ("მეფე სოლომონ II-ის I შესახვევი", None, None, dict(kind="lane", regnal="II", ord=1)),
    ("ერეკლე მეორის ქუჩა", None, None, dict(regnal="II", ord=None, eponym_nom_ka="ერეკლე მეორე")),
    ("მეფე თეიმურაზ პირველის I შესახვევი", None, None, dict(regnal="I", ord=1)),
    ("ფარსმან II ქველის ქუჩა", None, None,
     dict(regnal="II", ord=None, base_ka="ფარსმან II ქველის", eponym_nom_ka="ფარსმან II ქველი")),
    ("პირველი რესპუბლიკის მოედანი", None, None, dict(kind="square", regnal=None, ord=None)),
    # --- nested parents and settlements
    ("ზიგზაგის I ქუჩის ჩიხი", "тупик 1-й улицы Зигзаги", None,
     dict(kind="dead_end", ord=None, base_ka="ზიგზაგის", is_derived=True, parent_ord=1,
          parent_kind="street", base_ru="Зигзаги")),
    ("აეროდრომის დასახლება III ქუჩის I გასასვლელი", None, None,
     dict(kind="passage", ord=1, parent_ord=3, parent_kind="street", base_ka="აეროდრომის დასახლება",
          eponym_nom_ka="აეროდრომი")),
    ("თბილისი-კოჯორის გზატკეცილის III ჩიხი", None, None,
     dict(kind="dead_end", ord=3, parent_kind="highway", base_ka="თბილისი-კოჯორის")),
    ("ნავთლუღის I დასახლების VII შესახვევი", "8-й переулок Навтлуги-1 Дасахлеба", None,
     dict(kind="lane", ord=7, eponym_nom_ka="ნავთლუღი", flags_has=["ord_mismatch_ru"])),
    ("ზვიად გამსახურდიას სახელობის მტკვრის მარჯვენა სანაპიროს შესახვევი", None, None,
     dict(kind="lane", parent_kind="embankment", eponym_nom_ka="ზვიად გამსახურდია")),
    # --- "named after" construction, aliases, alternatives
    ("ჰეიდარ ალიევის სახელობის მარჯვენა სანაპირო", None, None,
     dict(kind="embankment", eponym_nom_ka="ჰეიდარ ალიევი")),
    ("ნუგზარ ენდელაძის სახელობის მოედანი", None, None,
     dict(kind="square", eponym_nom_ka="ნუგზარ ენდელაძე")),
    ("ალექსანდრე (საშა) იოსელიანის IV შესახვევი", None, None,
     dict(kind="lane", ord=4, base_ka="ალექსანდრე (საშა) იოსელიანის", eponym_nom_ka="ალექსანდრე იოსელიანი",
          aliases=["საშა"], flags_has=["alias"])),
    ("დომინიკა ერისთავის (განდეგილის) ქუჩა", None, None,
     dict(base_ka="დომინიკა ერისთავის (განდეგილის)", aliases=["განდეგილის"], eponym_nom_ka="დომინიკა ერისთავი")),
    ("სიმონ ავალიანის ქუჩა / მე-12 ქუჩა", None, None,
     dict(kind="street", base_ka="სიმონ ავალიანის", alt_ka="მე-12 ქუჩა", flags_has=["alt_name"])),
    # --- typos, Mtavruli, mixed scripts
    ("Მეფე Მირიანის ქუჯა", None, None,
     dict(kind="street", type_word="ქუჩა", base_ka="მეფე მირიანის", flags_has=["mtavruli", "typo_type"])),
    ("ᲛᲔᲤᲔ ᲛᲘᲠᲘᲐᲜᲘᲡ ქუჩა", None, None, dict(base_ka="მეფე მირიანის", flags_has=["mtavruli"])),
    ("ჭავჭავაძის ქყჩა", None, None, dict(kind="street", type_word="ქუჩა", flags_has=["typo_type"])),
    ("პატარიძის ქ.", None, None, dict(kind="street", base_ka="პატარიძის", flags_has=["typo_type"])),
    ("პაატა ჯანიაშვილის ქუჩას", None, None, dict(kind="street", flags_has=["typo_type"])),
    ("ახალდაბის I შეს.", None, None, dict(kind="lane", ord=1, flags_has=["typo_type"])),
    ("მიხეილ მესხის III შესასვლეი", None, None, dict(kind="passage", ord=3, flags_has=["typo_type"])),
    ("Trail of Meliqidze - მელიქიძის ბილიკი", None, None,
     dict(kind="other", type_word="ბილიკი", base_ka="მელიქიძის", flags_has=["latin_in_ka"])),
    ("Onofre rise road (ონოფრეს აღმართი)", None, None,
     dict(kind="ascent", base_ka="ონოფრეს", flags_has=["latin_in_ka"])),
    ("giorgi odishvili street", None, None,
     dict(kind="street", eponym_nom_ka=None, flags_has=["non_ka"], family_key_prefix="#")),
    ("Steps to Ilia Sukhishvili Street", None, None, dict(kind="other", flags_has=["non_ka"])),
    ("1-й проход 1-я улица посёлок Самрецвело", None, None, dict(kind="passage", flags_has=["non_ka"])),
    # --- routes and names without a type word
    ("თბილისი — თიანეთი", None, None, dict(kind="road", flags_has=["route"], family_key_prefix="#")),
    ("ფონიჭალა — მარნეული — გუგუთი", None, None, dict(kind="road", flags_has=["route"])),
    ("ფონიჭალა 3", "улица Поничала-3", None, dict(kind="street", type_word=None, flags_has=["type_from_ru"])),
    ("მზიური", None, "Mziuri", dict(kind="other", type_word=None, flags_has=["no_type"])),
    # --- other kinds
    ("26 მაისის მოედანი", None, None, dict(kind="square", base_ka="26 მაისის", eponym_nom_ka="26 მაისი")),
    ("9 ძმა ხერხეულიძის I ჩიხი", None, None, dict(kind="dead_end", ord=1, base_ka="9 ძმა ხერხეულიძის")),
    ("დოლაბაურის I ხევი", None, None, dict(kind="ravine", ord=1)),
    ("მშრალი ხიდი", None, None, dict(kind="bridge", base_ka="მშრალი", eponym_nom_ka="მშრალი")),
    ("ვაკის პარკი", None, None, dict(kind="park")),
    ("მრგვალი ბაღი", None, None, dict(kind="garden")),
    ("ფიქრის გორის სკვერი", None, None, dict(kind="garden")),
    ("მდინარე ვერე", None, None, dict(kind="river", type_word="მდინარე", base_ka="ვერე")),
    ("კუს ტბა", None, None, dict(kind="lake", eponym_nom_ka="კუ")),
    ("სამგორის რაიონი", None, None, dict(kind="district", base_ka="სამგორის")),
    ("სოლოლაკის ქედი", None, None, dict(kind="other", type_word="ქედი")),
    ("დავით აღმაშენებლის გამზირი", None, None,
     dict(kind="avenue", eponym_nom_ka="დავით აღმაშენებელი")),
    ("ქეთევან დედოფლის გამზირი", None, None, dict(eponym_nom_ka="ქეთევან დედოფალი")),
    ("თბილისი-კოჯორის შესახვევის I ჩიხი", None, None, dict(kind="dead_end", ord=1, parent_kind="lane")),
    ("", None, None, dict(kind="other", base_ka="", flags_has=["empty"])),
]


@pytest.mark.parametrize("ka, ru, en, exp", CASES, ids=[f"{c[0]}|{c[1]}" for c in CASES])
def test_parse_cases(ka, ru, en, exp):
    p = parse_name(ka, ru, en)
    assert isinstance(p, ParsedName)
    for k, v in exp.items():
        if k == "flags_has":
            for f in v:
                assert f in p.flags, (f, p.flags)
        elif k == "family_key_prefix":
            assert p.family_key.startswith(v), p.family_key
        else:
            assert getattr(p, k) == v, (k, getattr(p, k), v)


def test_enough_manual_cases():
    assert len(CASES) >= 40


# ------------------------------------------------------------------------------ family keys
FAMILY_EQUAL = [
    # genitive variants
    ("ლებანიძეს ქუჩა", "ლებანიძის ქუჩა"),
    # syncope
    ("დავით აღმაშენებელის ქუჩა", "დავით აღმაშენებლის გამზირი"),
    ("ვახტანგ გორგასალის III შესახვევი", "ვახტანგ გორგასლის ქუჩა"),
    ("ქეთევან დედოფალის ქუჩა", "ქეთევან დედოფლის გამზირი"),
    # hyphen / en dash
    ("ვაჟა–ფშაველას I შესახვევი", "ვაჟა-ფშაველას გამზირი"),
    # word order + title (the ru name tells that "I" is the street ordinal here)
    (("მეფე ფარნავაზი I ქუჩა", "1-я улица Мепе Парнавази"), "ფარნავაზ მეფის გამზირი"),
    ("მეფე ფარნავაზის ქუჩა", "ფარნავაზ მეფის გამზირი"),
    ("თამარ მეფის ქუჩა", "მეფე თამარის ქუჩა"),
    # optional title / saint when two tokens remain, regnal word vs roman
    ("მეფე ერეკლე მეორეს ქუჩა", "ერეკლე მეორის ქუჩა"),
    ("ერეკლე II შესახვევი", "ერეკლე მეორის ქუჩა"),
    ("მეფე თეიმურაზ პირველის ქუჩა", "თეიმურაზ I-ის ქუჩა"),
    ("წმინდა ქეთევან დედოფლის გამზირი", "ქეთევან დედოფლის გამზირი"),
    ("მარშალ იოზეფ პილსუდსკის გამზირი", "იოზეფ პილსუდსკის ქუჩა"),
    # lanes share the key of their street, aliases and "named after" ignored
    ("ლადო ასათიანის II შესახვევი", "ლადო ასათიანის ქუჩა"),
    ("ზიგზაგის I ქუჩის ჩიხი", "ზიგზაგის ქუჩა"),
    ("ალექსანდრე (საშა) იოსელიანის ქუჩა", "ალექსანდრე იოსელიანის ქუჩა"),
    ("ჰეიდარ ალიევის სახელობის მარჯვენა სანაპირო", "ჰეიდარ ალიევის ქუჩა"),
    ("ნავთლუღის I დასახლების VII შესახვევი", "ნავთლუღის ქუჩა"),
    # spelling: Mtavruli, typo in the type word
    ("Მეფე Მირიანის ქუჯა", "მეფე მირიანის ქუჩა"),
]

FAMILY_DIFFERENT = [
    ("ერეკლე მეორის ქუჩა", "ერეკლე I ჩიხი"),            # different kings
    ("ერეკლე მეორის ქუჩა", "ერეკლე ტატიშვილის ქუჩა"),
    ("მარშალ გელოვანის გამზირი", "მირზა გელოვანის ქუჩა"),
    ("ზვიად გამსახურდიას სანაპირო", "კონსტანტინე გამსახურდიას ქუჩა"),
    ("ლადო ასათიანის ქუჩა", "ლევან ასათიანის ქუჩა"),
]


def _p(x):
    return parse_name(*x) if isinstance(x, tuple) else parse_name(x)


@pytest.mark.parametrize("a, b", FAMILY_EQUAL)
def test_family_key_equal(a, b):
    assert _p(a).family_key == _p(b).family_key


@pytest.mark.parametrize("a, b", FAMILY_DIFFERENT)
def test_family_key_different(a, b):
    assert parse_name(a).family_key != parse_name(b).family_key


def test_initial_kept_for_cross_name_resolution():
    p = parse_name("პრეზიდენტ ზ. გამსახურდიას სანაპირო")
    assert "ზ." in p.family_key.split(" ")
    assert "პრეზიდენტ" not in p.family_key


def test_parent_key_equals_family_key_for_derived():
    p = parse_name("ზიგზაგის I ქუჩის ჩიხი")
    assert p.parent_family_key == p.family_key == parse_name("ზიგზაგის ქუჩა").family_key
    assert parse_name("ზიგზაგის ქუჩა").parent_family_key is None


def test_result_is_fresh_object():
    a = parse_name("ლადო ასათიანის ქუჩა")
    a.flags.append("x")
    assert "x" not in parse_name("ლადო ასათიანის ქუჩა").flags


# ---------------------------------------------------------------------------- helpers
@pytest.mark.parametrize("raw, out", [
    ("Მეფე Მირიანის ქუჯა", "მეფე მირიანის ქუჩა"),
    ("ჭავჭავაძის ქყჩა", "ჭავჭავაძის ქუჩა"),
    ("პატარიძის ქ.", "პატარიძის ქუჩა"),
    ("ახალდაბის I შეს.", "ახალდაბის I შესახვევი"),
    ("პაატა ჯანიაშვილის ქუჩას", "პაატა ჯანიაშვილის ქუჩა"),
    ("  ვაჟა–ფშაველას   გამზირი ", "ვაჟა-ფშაველას გამზირი"),
    ("თბილისი - თიანეთი", "თბილისი — თიანეთი"),
    ("ზ.გამსახურდიას სანაპირო", "ზ. გამსახურდიას სანაპირო"),
    ("„გმირთა“ მოედანი", "გმირთა მოედანი"),
    ("ქუჩა", "ქუჩა"),
    ("", ""),
])
def test_normalize_ka(raw, out):
    assert normalize_ka(raw) == out
    assert normalize_ka(out) == out   # idempotent


@pytest.mark.parametrize("raw, out", [
    ("1-ый переулок Ладо Асатиани", "1-й переулок Ладо Асатиани"),
    ("1-ой тупик", "1-й тупик"),
    ("2-ой переулок Эрекле Меори", "2-й переулок Эрекле Меори"),
    ("3-ий проезд", "3-й проезд"),
    ("1й переулок", "1-й переулок"),
    ("1-ая улица", "1-я улица"),
    ("Мариам Угрелидзе 1-й Пер.", "Мариам Угрелидзе 1-й переулок"),
    ("Площадь Первой Республики", "площадь Первой Республики"),
    ("площадь 26 Мая", "площадь 26 Мая"),
    ("1-й переулок 9-го Апреля", "1-й переулок 9-го Апреля"),
    ("", ""),
])
def test_normalize_ru_name(raw, out):
    assert normalize_ru_name(raw) == out


@pytest.mark.parametrize("raw, out", [
    ("ლადო ასათიანის II შესახვევი", "ლადო ასათიანის"),
    ("ზიგზაგის I ქუჩის ჩიხი", "ზიგზაგის"),
    ("ერეკლე II შესახვევი", "ერეკლე II"),
    ("XII შესახვევი", ""),
    ("მზიური", "მზიური"),
    ("2-й переулок Ладо Асатиани", "Ладо Асатиани"),
    ("тупик 1-й улицы Зигзаги", "Зигзаги"),
    ("1-ый переулок 9-го Апреля", "9-го Апреля"),
    ("площадь 26 Мая", "26 Мая"),
    ("Lado Asatiani 2nd Lane", "Lado Asatiani"),
    ("Tengiz Abuladze 1st Dead End", "Tengiz Abuladze"),
    ("Rustaveli Ave.", "Rustaveli"),
    ("", ""),
])
def test_strip_type(raw, out):
    assert strip_type(raw) == out


@pytest.mark.parametrize("tok, key", [
    ("ასათიანის", key_tok("ასათიანი")),
    ("ლებანიძეს", "ლებანიძ"),
    ("ლებანიძის", "ლებანიძ"),
    ("აღმაშენებლის", key_tok("აღმაშენებელი")),
    ("მეორეს", "II"),
    ("III-ის", "III"),
    ("ზ.", "ზ."),
])
def test_key_tok(tok, key):
    assert key_tok(tok) == key


@pytest.mark.parametrize("tok, nom", [
    ("ასათიანის", "ასათიანი"),
    ("ჭავჭავაძის", "ჭავჭავაძე"),
    ("ფშაველას", "ფშაველა"),
    ("გამსახურდიას", "გამსახურდია"),
    ("ნინოს", "ნინო"),
    ("აღმაშენებლის", "აღმაშენებელი"),
    ("დედოფლის", "დედოფალი"),
    ("მეორის", "მეორე"),
    ("I-ის", "I"),
    ("ერეკლე", "ერეკლე"),
])
def test_nominative_tok(tok, nom):
    assert nominative_tok(tok) == nom


# ------------------------------------------------------------------- generated names
@pytest.mark.parametrize("ka, ru, en", [
    ("ლადო ასათიანის II შესახვევი", "2-й переулок Ладо Асатиани", "Lado Asatiani 2nd Lane"),
    ("ერეკლე II შესახვევი", "переулок Эрекле II", "Erekle II Lane"),
    ("ზიგზაგის I ქუჩის ჩიხი", "тупик 1-й улицы Зигзаги", "Zigzagi 1st Street Dead End"),
    ("შოთა რუსთაველის გამზირი", "проспект Шота Руставели", "Shota Rustaveli Avenue"),
    ("ვაჟა-ფშაველას I შესახვევი", "1-й переулок Важа-Пшавела", "Vazha-Pshavela 1st Lane"),
    ("ლადო ასათიანის I ქუჩა", "1-я улица Ладо Асатиани", "Lado Asatiani 1st Street"),
    ("XII შესახვევი", "12-й переулок", "12th Lane"),
    ("მზიური", "Мзиури", "Mziuri"),
])
def test_generated_names(ka, ru, en):
    from pipeline.names.parse import make_en_name, make_ru_name

    p = parse_name(ka)
    assert make_ru_name(p) == ru
    assert make_en_name(p) == en


def test_generated_names_none_for_routes():
    from pipeline.names.parse import make_en_name, make_ru_name

    p = parse_name("თბილისი — თიანეთი")
    assert make_ru_name(p) is None and make_en_name(p) is None
