"""Extra landmark kinds of Russian-language cities (stops, embassies, hotels ...) and OSM notes."""
from shapely.geometry import Point

from pipeline.osm.extract import extra_kind, extra_kind_late, is_stop
from pipeline.osm.landmarks import build_note
from pipeline.osm.model import OsmObj


def test_is_stop():
    assert is_stop({"highway": "bus_stop", "name": "Улица Льва Толстого"})
    assert is_stop({"public_transport": "platform", "bus": "yes", "name": "Парк культуры"})
    assert is_stop({"railway": "tram_stop", "name": "Шаболовская"})
    assert not is_stop({"public_transport": "stop_position", "bus": "yes", "name": "Парк культуры"})
    assert not is_stop({"public_transport": "platform", "subway": "yes", "name": "Парк культуры"})


def test_extra_kind_priority():
    assert extra_kind({"highway": "bus_stop", "name": "x"}) == "stop"
    assert extra_kind({"office": "diplomatic", "diplomatic": "embassy", "name": "Посольство"}) == "embassy"
    assert extra_kind({"amenity": "ferry_terminal", "name": "Причал"}) == "pier"
    assert extra_kind({"tourism": "hotel", "name": "Отель"}) is None        # late kinds are not in extra_kind
    assert extra_kind_late({"tourism": "hotel", "name": "Отель"}) == "hotel"
    assert extra_kind_late({"amenity": "hospital", "name": "ГКБ"}) == "hospital"
    assert extra_kind_late({"amenity": "clinic", "name": "Клиника"}) is None  # private clinics are not imported
    assert extra_kind_late({"amenity": "clinic", "operator:type": "public", "name": "Поликлиника"}) == "hospital"
    assert extra_kind_late({"office": "government", "name": "Министерство"}) == "government"
    assert extra_kind_late({"amenity": "kindergarten", "name": "Детский сад"}) == "kindergarten"
    assert extra_kind_late({"amenity": "cafe", "name": "Кафе"}) is None


def _obj(ref: str, tags: dict) -> OsmObj:
    return OsmObj(ref[0], int(ref[1:]), tags, Point(37.58, 55.73))


def test_build_note_stop_routes():
    routes = {"n1": [("bus", "м3"), ("bus", "15"), ("bus", "255")], "n2": [("bus", "15"), ("trolleybus", "Б")]}
    note = build_note("stop", [_obj("n1", {"name": "Усачёва"}), _obj("n2", {"name": "Усачёва"})], routes)
    assert note == "Автобусы: м3, 15, 255; Троллейбусы: Б"
    assert build_note("stop", [_obj("n9", {"name": "Усачёва"})], routes) is None


def test_build_note_monument_and_plaque():
    o = _obj("n5", {"name": "А. И. Абрикосову", "start_date": "1960", "artist_name": "скульптор А. Г. Постол",
                    "inscription": "Академик\nА. И. Абрикосов"})
    assert build_note("monument", [o]) == "Открыт: 1960; Автор: скульптор А. Г. Постол; Надпись: «Академик А. И. Абрикосов»"
    b = _obj("w7", {"name": "Дом", "start_date": "1911", "architect": "Н. Д. Струков", "inscription": "не для домов"})
    assert build_note("residential", [b]) == "Год: 1911; Архитектор: Н. Д. Струков"
    assert build_note("museum", [_obj("n6", {"name": "Музей"})]) is None
