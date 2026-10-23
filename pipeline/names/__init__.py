"""Name parsing library: transliteration, house numbers, street-name parsing, families.

Modules:

* :mod:`pipeline.names.translit` -- ``skeleton``, ``ka_to_ru``, ``ka_to_lat``;
* :mod:`pipeline.names.houses` -- ``HousePart``, ``parse_house``, ``normalize_letter``;
* :mod:`pipeline.names.parse` -- ``ParsedName``, ``parse_name``, ``normalize_ka``,
  ``normalize_ru_name``, ``strip_type``, ``KIND_TABLE``;
* :mod:`pipeline.names.families` -- ``group_families`` (cross-name key resolution).
"""
from pipeline.names.houses import HousePart, normalize_letter, parse_house
from pipeline.names.parse import (
    KIND_CODES,
    KIND_GROUP,
    KIND_INDEX,
    KIND_TABLE,
    Kind,
    ParsedName,
    kinds_for_pack,
    normalize_ka,
    normalize_ru_name,
    parse_name,
    strip_type,
)
from pipeline.names.translit import ka_to_lat, ka_to_ru, skeleton

__all__ = [
    "HousePart", "parse_house", "normalize_letter",
    "Kind", "KIND_TABLE", "KIND_CODES", "KIND_INDEX", "KIND_GROUP", "kinds_for_pack",
    "ParsedName", "parse_name", "normalize_ka", "normalize_ru_name", "strip_type",
    "skeleton", "ka_to_ru", "ka_to_lat",
]
