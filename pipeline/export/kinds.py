"""Canonical kind table for ``core.kinds`` (docs/pack-format.md, section "Kinds").

The source of truth is ``pipeline.names.parse.KIND_TABLE`` (owned by the names stage).
Until that module exists, ``FALLBACK_KIND_TABLE`` below mirrors the table in the docs.
TODO: drop the fallback once ``pipeline.names.parse.KIND_TABLE`` is stable.
"""
from __future__ import annotations

from typing import Any

from pipeline.log import get_logger

log = get_logger(__name__)

# (code, ka words, ru, ru synonyms, group) -- exactly the table in docs/pack-format.md.
FALLBACK_KIND_TABLE: list[dict[str, Any]] = [
    {"code": "street", "ka": "ქუჩა", "ru": "улица", "syn": [], "group": "street*"},
    {"code": "avenue", "ka": "გამზირი", "ru": "проспект", "syn": [], "group": "street*"},
    {"code": "highway", "ka": "გზატკეცილი", "ru": "шоссе", "syn": [], "group": "street*"},
    {"code": "road", "ka": "გზა", "ru": "дорога", "syn": [], "group": "street*"},
    {"code": "embankment", "ka": "სანაპირო", "ru": "набережная", "syn": [], "group": "street*"},
    {"code": "ascent", "ka": "აღმართი", "ru": "подъём", "syn": [], "group": "street*"},
    {"code": "descent", "ka": "დაღმართი", "ru": "спуск", "syn": [], "group": "street*"},
    {"code": "alley", "ka": "ხეივანი", "ru": "аллея", "syn": [], "group": "street*"},
    {"code": "row", "ka": "რიგი", "ru": "ряд", "syn": [], "group": "street*"},
    {"code": "lane", "ka": "შესახვევი", "ru": "переулок", "syn": [], "group": "lane*"},
    {"code": "dead_end", "ka": "ჩიხი", "ru": "тупик", "syn": [], "group": "lane*"},
    {"code": "passage", "ka": "გასასვლელი", "ru": "проезд", "syn": ["съезд"], "group": "lane*"},
    {"code": "square", "ka": "მოედანი", "ru": "площадь", "syn": [], "group": ""},
    {"code": "bridge", "ka": "ხიდი", "ru": "мост", "syn": [], "group": ""},
    {"code": "tunnel", "ka": "გვირაბი", "ru": "тоннель", "syn": [], "group": ""},
    {"code": "ravine", "ka": "ხევი", "ru": "балка", "syn": ["ущелье"], "group": ""},
    {"code": "garden", "ka": "სკვერი", "ru": "сквер", "syn": ["сад"], "group": ""},
    {"code": "park", "ka": "პარკი", "ru": "парк", "syn": [], "group": ""},
    {"code": "river", "ka": "მდინარე", "ru": "река", "syn": [], "group": "water*"},
    {"code": "stream", "ka": "ღელე", "ru": "ручей", "syn": [], "group": "water*"},
    {"code": "canal", "ka": "არხი", "ru": "канал", "syn": [], "group": "water*"},
    {"code": "lake", "ka": "ტბა", "ru": "озеро", "syn": [], "group": "water*"},
    {"code": "metro", "ka": "", "ru": "метро", "syn": ["станция"], "group": ""},
    {"code": "district", "ka": "რაიონი", "ru": "район", "syn": [], "group": "area*"},
    {"code": "neighbourhood", "ka": "", "ru": "микрорайон", "syn": ["квартал", "местность"], "group": "area*"},
    {"code": "other", "ka": "", "ru": "прочее", "syn": [], "group": ""},
]

_COLUMNS = ("code", "ka", "ru", "syn", "group")


def _norm_entry(raw: Any) -> dict[str, Any]:
    """Normalize one KIND_TABLE entry (dict, dataclass/namedtuple or plain tuple)."""
    if isinstance(raw, dict):
        d = dict(raw)
    elif hasattr(raw, "_asdict"):
        d = dict(raw._asdict())
    elif hasattr(raw, "__dataclass_fields__"):
        d = {k: getattr(raw, k) for k in raw.__dataclass_fields__}
    elif isinstance(raw, (list, tuple)):
        d = dict(zip(_COLUMNS, raw))
    else:
        raise TypeError(f"unsupported KIND_TABLE entry: {raw!r}")
    ka = d.get("ka") or d.get("ka_words") or d.get("words") or ""
    if isinstance(ka, (list, tuple)):
        ka = ka[0] if ka else ""
    syn = d.get("syn") or d.get("synonyms") or d.get("ru_syn") or []
    if isinstance(syn, str):
        syn = [s.strip() for s in syn.split(",") if s.strip()]
    group = d.get("group") or ""
    return {"code": str(d["code"]), "ka": str(ka), "ru": str(d.get("ru") or ""), "syn": list(syn), "group": str(group)}


def load_kinds() -> list[dict[str, Any]]:
    """Return ``core.kinds`` entries in canonical order."""
    try:
        from pipeline.names import parse as names_parse  # type: ignore[attr-defined]
    except Exception as exc:  # module may not exist yet (parallel development)
        log.warning("pipeline.names.parse unavailable (%s): using fallback kind table", exc)
        return [dict(k) for k in FALLBACK_KIND_TABLE]
    if hasattr(names_parse, "kinds_for_pack"):
        kinds = [_norm_entry(e) for e in names_parse.kinds_for_pack()]
        log.info("kinds: %d from pipeline.names.parse.kinds_for_pack()", len(kinds))
        return kinds
    KIND_TABLE = names_parse.KIND_TABLE
    table = KIND_TABLE.values() if isinstance(KIND_TABLE, dict) else KIND_TABLE
    if isinstance(KIND_TABLE, dict) and not all(isinstance(v, dict) and "code" in v for v in table):
        table = [{"code": k, **(v if isinstance(v, dict) else dict(zip(_COLUMNS[1:], v)))} for k, v in KIND_TABLE.items()]
    kinds = [_norm_entry(e) for e in table]
    codes = [k["code"] for k in kinds]
    if len(set(codes)) != len(codes):
        raise ValueError(f"duplicate kind codes in KIND_TABLE: {codes}")
    if "other" not in codes:
        log.warning("KIND_TABLE has no 'other' kind; appending it")
        kinds.append({"code": "other", "ka": "", "ru": "прочее", "syn": [], "group": ""})
    log.info("kinds: %d from pipeline.names.parse.KIND_TABLE", len(kinds))
    return kinds


def group_codes(kinds: list[dict[str, Any]], group: str) -> list[str]:
    """Kind codes of a group such as ``street*``."""
    return [k["code"] for k in kinds if k.get("group") == group]
