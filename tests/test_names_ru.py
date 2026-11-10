"""Russian names and house numbers (cities with ``lang = "ru"``, e.g. Moscow, Khamovniki)."""
import pytest

from pipeline.names.houses import HousePart, format_house_ru, parse_house_ru
from pipeline.names.parse_ru import key_word, parse_name_ru, strip_en


@pytest.mark.parametrize("name, kind, ordinal, base, key", [
    ("улица Льва Толстого", "street", None, "Льва Толстого", "льва толстого"),
    ("Большая Пироговская улица", "street", None, "Большая Пироговская", "пироговск"),
    ("Малый Саввинский переулок", "lane", None, "Малый Саввинский", "саввинск"),
    ("Саввинская набережная", "embankment", None, "Саввинская", "саввинск"),
    ("1-й Неопалимовский переулок", "lane", 1, "Неопалимовский", "неопалимовск"),
    ("2-я Фрунзенская улица", "street", 2, "Фрунзенская", "фрунзенск"),
    ("Фрунзенская набережная", "embankment", None, "Фрунзенская", "фрунзенск"),
    ("1-й переулок Тружеников", "lane", 1, "Тружеников", "тружеников"),
    ("Комсомольский проспект (дублёр)", "avenue", None, "Комсомольский", "комсомольск"),
    ("Гоголевский бульвар", "boulevard", None, "Гоголевский", "гоголевск"),
    ("Большой Чудов переулок", "lane", None, "Большой Чудов", "чудов"),
    ("улица Хамовнический Вал", "street", None, "Хамовнический Вал", "хамовнический вал"),
    ("площадь Пречистенские Ворота", "square", None, "Пречистенские Ворота", "пречистенские ворота"),
    ("Лужнецкий метромост", "bridge", None, "Лужнецкий", "лужнецк"),
    ("улица 10-летия Октября", "street", None, "10-летия Октября", "10-летия октября"),
    ("Усачёва улица", "street", None, "Усачёва", "усачева"),
])
def test_parse_name_ru(name, kind, ordinal, base, key):
    p = parse_name_ru(name)
    assert (p.kind, p.ord, p.base_ka, p.family_key) == (kind, ordinal, base, key)
    assert p.is_derived is False and p.parent_family_key is None


def test_parse_name_ru_flags_and_pseudo_keys():
    assert "service_road" in parse_name_ru("Зубовский бульвар (дублёр)").flags
    p = parse_name_ru("проектируемый проезд № 6023")
    assert p.kind == "passage" and p.family_key.startswith("#") and "numbered" in p.flags
    q = parse_name_ru("Остоженка")
    assert q.kind == "other" and "no_type" in q.flags and q.family_key == "остоженка"
    assert parse_name_ru("1-й Неопалимовский переулок").family_key == parse_name_ru("3-й Неопалимовский переулок").family_key


def test_key_word_and_strip_en():
    assert key_word("Саввинская", True) == key_word("Саввинский", True) == "саввинск"
    assert key_word("Толстого", False) == "толстого"
    assert strip_en("Lva Tolstogo Street") == "Lva Tolstogo"
    assert strip_en("1st Neopalimovsky Lane") == "Neopalimovsky"
    assert strip_en(None) is None


@pytest.mark.parametrize("raw, n, lit, sub, n_to, corp, shown", [
    ("14", 14, "", "", None, "", "14"),
    ("9А", 9, "а", "", None, "", "9А"),
    ("6a", 6, "а", "", None, "", "6А"),
    ("11К", 11, "к", "", None, "", "11К"),
    ("1 с24", 1, "", "", None, "с24", "1 с24"),
    ("11 c1", 11, "", "", None, "с1", "11 с1"),          # Latin "c"
    ("37С2", 37, "", "", None, "с2", "37 с2"),
    ("2 к14", 2, "", "", None, "к14", "2 к14"),
    ("32 корпус 2", 32, "", "", None, "к2", "32 к2"),
    ("37 к7 с3", 37, "", "", None, "к7 с3", "37 к7 с3"),
    ("10А с23", 10, "а", "", None, "с23", "10А с23"),
    ("19 с1А", 19, "", "", None, "с1а", "19 с1А"),
    ("23/25 с3", 23, "", "25", None, "с3", "23/25 с3"),
    ("12/2", 12, "", "2", None, "", "12/2"),
    ("23-25", 23, "", "", 25, "", "23-25"),
    ("2-4-6 с14", 2, "", "", 6, "с14", "2-4-6 с14"),
    ("вл53", 53, "", "", None, "вл", "вл53"),
])
def test_parse_house_ru(raw, n, lit, sub, n_to, corp, shown):
    parts = parse_house_ru(raw)
    assert len(parts) == 1
    p = parts[0]
    assert (p.n, p.lit, p.sub, p.n_to, p.corp) == (n, lit, sub, n_to, corp)
    assert format_house_ru(p, raw) == shown


@pytest.mark.parametrize("raw", ["", "стр. 5", "11 с10 фл8", "14/23 c", "офис 208"])
def test_parse_house_ru_junk(raw):
    assert parse_house_ru(raw) == []


def test_format_house_ru_from_parts():
    assert format_house_ru(HousePart(12, "а", "", None, "к2 с1")) == "12А к2 с1"
    assert format_house_ru(HousePart(5, "", "7", None, "с2")) == "5/7 с2"
