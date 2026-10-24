"""Shared helpers of the enrichment stages: paths, JSON artefacts, loading families from SQLite."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from pipeline.config import BUILD, ENRICH
from pipeline.enrich import ka as K
from pipeline.log import get_logger
from pipeline.wikidata.entity import is_qid

log = get_logger(__name__)



class Paths:
    """Locations of enrichment artefacts; tests point them at a temporary directory with ``set``."""

    def __init__(self, enrich: Path = ENRICH, build: Path = BUILD):
        self.set(enrich, build)

    def set(self, enrich: Path, build: Path | None = None) -> None:
        self.enrich = Path(enrich)
        self.build = Path(build) if build is not None else self.enrich / "build"

    stages = property(lambda s: s.enrich / "stages")
    batches = property(lambda s: s.enrich / "batches")
    results = property(lambda s: s.enrich / "results")
    decisions = property(lambda s: s.enrich / "review_decisions.json")
    thresholds = property(lambda s: s.enrich / "thresholds.json")
    dossiers = property(lambda s: s.enrich / "dossiers.jsonl")
    dossiers_old = property(lambda s: s.enrich / "dossiers_oldnames.jsonl")
    review_html = property(lambda s: s.build / "review.html")
    calibration = property(lambda s: s.build / "calibration.json")


paths = Paths()
AGENTS_DIR = Path(__file__).resolve().parent.parent.parent / ".claude" / "agents"
SCHEMA_DIR = Path(__file__).with_name("schemas")

ROLES = ("classifier", "disambiguator", "verifier", "name_qa")
AGENT_FILES = {"classifier": "eponym-classifier.md", "disambiguator": "eponym-disambiguator.md",
               "verifier": "eponym-verifier.md", "name_qa": "name-qa.md"}
SOURCES_FACTS = ("osm", "wd_p138", "wd_match", "rules")
STREET_KINDS = {"street", "avenue", "highway", "road", "embankment", "ascent", "descent", "alley", "row", "square"}


def stage_path(name: str, base: Path | None = None) -> Path:
    return (Path(base) if base else paths.stages) / f"{name}.json"


def write_json(path: Path, data: Any, indent: int | None = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent, sort_keys=False, default=_default) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def _default(o: Any) -> Any:
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    raise TypeError(f"not JSON serializable: {type(o)}")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except ValueError as e:
        log.error("cannot parse %s: %s", path, e)
        return default


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
    os.replace(tmp, path)
    return n


def read_jsonl(path: Path) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError as e:
                    log.error("%s:%d bad JSON line: %s", path, i, e)
    except FileNotFoundError:
        pass
    return out


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def fid(key: str) -> str:
    """Stable short id of a family (or old-name) key used inside LLM batches."""
    return "F" + sha1(key)[:8]


def canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def split_qids(raw: str | None) -> list[str]:
    if not raw:
        return []
    out = []
    for part in raw.replace(",", ";").split(";"):
        q = part.strip()
        if is_qid(q) and q not in out:
            out.append(q)
    return out


@dataclass
class Member:
    id: int
    kind: str
    ka: str
    ru: str
    en: str
    parent_id: int | None
    wikidata: str | None
    osm_ids: list[str]
    len_m: float
    alt: list[str] = field(default_factory=list)


@dataclass
class Family:
    id: int
    key: str
    base_ka: str
    base_ru: str
    base_en: str
    nom_ka: str
    kind_hint: str | None
    eponym_id: int | None
    members: list[Member] = field(default_factory=list)

    @property
    def fid(self) -> str:
        return fid(self.key)

    @property
    def head(self) -> Member | None:
        if not self.members:
            return None
        return sorted(self.members, key=lambda m: (m.parent_id is not None, m.kind not in STREET_KINDS,
                                                   -(m.len_m or 0), m.ka))[0]

    @property
    def parser_hint(self) -> str | None:
        """Parser hint written by the OSM stage (a kind_hint without ':'), or derived from the name."""
        if self.kind_hint and ":" not in self.kind_hint:
            return self.kind_hint
        if self.kind_hint and ":" in self.kind_hint:
            sub = self.kind_hint.split(":", 1)[1]
            if sub in ("numbered", "route", "non_ka"):
                return sub
        if not self.base_ka:
            h = self.head
            if h is not None:
                k = K.split_ka_name(h.ka)["kind"]
                if k:
                    return k
            return "numbered"
        if not K.has_ka(self.base_ka):
            return "non_ka"
        return None

    def member_names(self, limit: int = 12) -> list[dict]:
        ms = sorted(self.members, key=lambda m: (m.parent_id is not None, -(m.len_m or 0), m.ka))
        out, seen = [], set()
        for m in ms:
            if m.ka in seen:
                continue
            seen.add(m.ka)
            d = {"ka": m.ka}
            if m.ru:
                d["ru"] = m.ru
            if m.en:
                d["en"] = m.en
            out.append(d)
            if len(out) >= limit:
                break
        return out


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def load_families(con: sqlite3.Connection) -> list[Family]:
    fams: dict[int, Family] = {}
    for r in con.execute("SELECT id, key, base_ka, base_ru, base_en, eponym_nom_ka, kind_hint, eponym_id FROM family"):
        fams[r[0]] = Family(id=r[0], key=r[1], base_ka=K.clean(r[2] or ""), base_ru=K.ru_base(r[3] or ""),
                            base_en=K.en_base(r[4] or ""), nom_ka=K.clean(r[5] or ""), kind_hint=r[6],
                            eponym_id=r[7])
    alt: dict[int, list[str]] = {}
    if table_exists(con, "feature_name"):
        for fid_, name in con.execute("SELECT feature_id, name FROM feature_name WHERE kind IN ('alt','official')"):
            alt.setdefault(fid_, []).append(name)
    for r in con.execute("SELECT id, kind, ka, ru, en, family_id, parent_id, wikidata, osm_ids, len_m "
                         "FROM feature WHERE family_id IS NOT NULL ORDER BY id"):
        f = fams.get(r[5])
        if f is None:
            continue
        try:
            osm = json.loads(r[8]) if r[8] else []
        except ValueError:
            osm = []
        f.members.append(Member(id=r[0], kind=r[1] or "", ka=r[2] or "", ru=r[3] or "", en=r[4] or "",
                                parent_id=r[6], wikidata=r[7], osm_ids=[str(x) for x in osm or []],
                                len_m=r[9] or 0.0, alt=alt.get(r[0], [])))
    out = sorted(fams.values(), key=lambda f: f.key)
    for f in out:
        h = f.head
        if h is not None:
            if not f.base_ka:
                f.base_ka = K.split_ka_name(h.ka)["base"] if K.has_ka(h.ka) else ""
            if not f.base_ru and h.ru:
                f.base_ru = K.ru_base(h.ru)
            if not f.base_en and h.en:
                f.base_en = K.en_base(h.en)
        if not f.nom_ka and f.base_ka and K.has_ka(f.base_ka):
            f.nom_ka = K.nominative(f.base_ka)
    return out


def family_by_key(fams: Iterable[Family]) -> dict[str, Family]:
    return {f.key: f for f in fams}


def agent_prompt_version(role: str) -> tuple[str, str]:
    """(prompt_version, model) of a subagent: sha1 of its .md file and the frontmatter model."""
    p = AGENTS_DIR / AGENT_FILES[role]
    try:
        txt = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "missing", "unknown"
    model = "unknown"
    if txt.startswith("---"):
        head = txt.split("---", 2)[1]
        for line in head.splitlines():
            if line.strip().startswith("model:"):
                model = line.split(":", 1)[1].strip()
    return sha1(txt.replace("\r\n", "\n"))[:10], model


def llm_cache_key(prompt_version: str, model: str, dossier: dict) -> str:
    return sha1(f"{prompt_version}|{model}|{canonical(dossier)}")


def add_provenance(con: sqlite3.Connection, entity: str, key: str, fld: str, value: Any, source: str,
                   ref: str | None = None, confidence: float | None = None) -> None:
    if value is not None and not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    con.execute("INSERT INTO provenance(entity, entity_key, field, value, source, ref, confidence) "
                "VALUES (?,?,?,?,?,?,?)", (entity, key, fld, value, source, ref, confidence))


def clear_provenance(con: sqlite3.Connection, fields: Iterable[str], sources: Iterable[str],
                     entity: str = "family") -> None:
    fl, sl = list(fields), list(sources)
    con.execute("DELETE FROM provenance WHERE entity=? AND field IN (%s) AND source IN (%s)"
                % (",".join("?" * len(fl)), ",".join("?" * len(sl))), [entity, *fl, *sl])
