"""Read-only Wikidata / Wikipedia client used by the enrichment stages.

All calls go through :class:`pipeline.wikidata.http.Fetcher` (disk cache, pauses, back-off).
Entities are additionally kept in the ``entity`` table of the cache, keyed by QID, so a QID is
downloaded once whatever batch it was requested in.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from pipeline.config import RAW, WIKIDATA_API, WIKIDATA_SPARQL
from pipeline.log import get_logger
from pipeline.wikidata.entity import is_qid
from pipeline.wikidata.http import CacheMiss, Fetcher, HttpCache, HttpError, cache_key

log = get_logger(__name__)

FULL_PROPS = "labels|aliases|descriptions|claims|sitelinks"
LANGS = "ka|ru|en"
BATCH = 50
PROTOTYPE_CACHE = RAW / "prototype_cache" / "wikidata"


def _props_cover(have: str, want: str) -> bool:
    return set(want.split("|")) <= set(have.split("|"))


class WikidataClient:
    def __init__(self, fetcher: Fetcher | None = None, *, offline: bool = False):
        self.fetcher = fetcher or Fetcher(HttpCache(), offline=offline)
        self.cache = self.fetcher.cache
        self._mem: dict[str, tuple[str, dict]] = {}
        self.missing: set[str] = set()

    @property
    def offline(self) -> bool:
        return self.fetcher.offline

    # ------------------------------------------------------------------ entities
    def entities(self, ids: Iterable[str], props: str = FULL_PROPS, langs: str = LANGS) -> dict[str, dict]:
        """QID -> entity JSON. Missing/deleted items are absent; redirects resolve to the target
        but are returned under the requested id as well."""
        want = sorted({i for i in ids if is_qid(i)}, key=lambda q: int(q[1:]))
        out: dict[str, dict] = {}
        need: list[str] = []
        stored = self.cache.get_entities([q for q in want if q not in self._mem])
        self._mem.update(stored)
        for q in want:
            got = self._mem.get(q)
            if got and _props_cover(got[0], props):
                if got[1]:
                    out[q] = got[1]
            elif q not in self.missing:
                need.append(q)
        for i in range(0, len(need), BATCH):
            chunk = need[i:i + BATCH]
            params = {"action": "wbgetentities", "ids": "|".join(chunk), "props": props, "languages": langs,
                      "format": "json", "formatversion": "1"}
            try:
                r = self.fetcher.get_json(WIKIDATA_API, params, endpoint="api",
                                          validate=lambda d: isinstance(d, dict) and "entities" in d)
            except CacheMiss:
                log.debug("offline: %d entities not cached", len(chunk))
                self.missing.update(chunk)
                continue
            except HttpError as e:
                log.error("wbgetentities failed for %d ids (%s...): %s", len(chunk), chunk[:3], e)
                self.missing.update(chunk)
                continue
            got: dict[str, dict] = {}
            for key, e in (r.get("entities") or {}).items():
                if "missing" in e:
                    self.missing.add(key)
                    continue
                real = e.get("id", key)
                got[real] = e
                if real != key:
                    got[key] = e      # redirect: keep under the requested id too
            # ids that the API silently dropped
            for q in chunk:
                if q not in got and q not in self.missing:
                    self.missing.add(q)
            if got:
                self.cache.put_entities(got, props)
                for q, e in got.items():
                    self._mem[q] = (props, e)
            for q in chunk:
                if q in got:
                    out[q] = got[q]
            log.debug("wbgetentities %d/%d", min(i + BATCH, len(need)), len(need))
        if need:
            log.info("entities: requested %d, fetched/checked %d, missing %d", len(want), len(need),
                     len([q for q in want if q not in out]))
        return out

    def entity(self, qid: str, props: str = FULL_PROPS) -> dict | None:
        return self.entities([qid], props=props).get(qid)

    # ------------------------------------------------------------------ SPARQL
    def sparql(self, query: str, *, refresh: bool = False) -> list[dict]:
        r = self.fetcher.get_json(
            WIKIDATA_SPARQL, {"query": query, "format": "json"}, endpoint="sparql", method="POST",
            accept="application/sparql-results+json", refresh=refresh, timeout=180,
            validate=lambda d: isinstance(d, dict) and "results" in d,
        )
        return r["results"]["bindings"]

    # ------------------------------------------------------------------ humans by exact label
    def humans_by_label(self, labels: Iterable[tuple[str, str]], chunk: int = 100) -> dict[tuple[str, str], set[str]]:
        """(label, lang) -> QIDs of humans (P31=Q5) whose label or alias is exactly that string.

        Each pair is cached on its own (also empty answers), so reruns and other family sets reuse
        earlier SPARQL work. Pairs whose query failed are absent from the result.
        """
        pairs = sorted(set(labels))
        out: dict[tuple[str, str], set[str]] = {}
        need: list[tuple[str, str]] = []
        for lab, lang in pairs:
            hit = self.cache.get(cache_key("LABEL", "humans", {"lab": lab, "lang": lang}))
            if hit is not None:
                out[(lab, lang)] = set(hit)
            else:
                need.append((lab, lang))
        if need and self.offline:
            log.debug("offline: %d label pairs not cached", len(need))
            return out
        for i in range(0, len(need), chunk):
            part = need[i:i + chunk]
            vals = " ".join(f'("L{j}" {json.dumps(lab, ensure_ascii=False)}@{lang})' for j, (lab, lang) in enumerate(part))
            q = ("SELECT ?k ?item WHERE { VALUES (?k ?lab) { %s } ?item rdfs:label|skos:altLabel ?lab . "
                 "?item wdt:P31 wd:Q5 . }" % vals)
            try:
                rows = self.sparql(q)
            except (CacheMiss, HttpError) as e:
                log.error("humans_by_label: chunk %d failed: %s", i // chunk, e)
                continue
            got: dict[int, set[str]] = {}
            for r in rows:
                try:
                    j = int(r["k"]["value"][1:])
                    got.setdefault(j, set()).add(r["item"]["value"].rsplit("/", 1)[-1])
                except (KeyError, TypeError, ValueError):
                    continue
            for j, (lab, lang) in enumerate(part):
                res = got.get(j, set())
                out[(lab, lang)] = res
                self.cache.put(cache_key("LABEL", "humans", {"lab": lab, "lang": lang}), "LABEL", "humans",
                               {"lab": lab, "lang": lang}, sorted(res))
            log.info("humans_by_label: %d/%d pairs, %d rows", min(i + chunk, len(need)), len(need), len(rows))
        return out

    # ------------------------------------------------------------------ sitelinks
    def titles_to_qids(self, wiki: str, titles: Iterable[str]) -> dict[str, str]:
        """Wikipedia article titles (e.g. kawiki) -> QIDs, cached per title (redirects followed)."""
        titles = sorted({t.strip() for t in titles if t and t.strip()})
        out: dict[str, str] = {}
        need = []
        for t in titles:
            hit = self.cache.get(cache_key("TITLE", wiki, {"t": t}))
            if hit is not None:
                if hit:
                    out[t] = hit
            else:
                need.append(t)
        if need and self.offline:
            return out
        for i in range(0, len(need), BATCH):
            chunk = need[i:i + BATCH]
            params = {"action": "wbgetentities", "sites": wiki, "titles": "|".join(chunk), "props": "sitelinks",
                      "sitefilter": wiki, "redirects": "yes", "normalize": "yes" if len(chunk) == 1 else "no",
                      "format": "json"}
            try:
                r = self.fetcher.get_json(WIKIDATA_API, params, endpoint="api",
                                          validate=lambda d: isinstance(d, dict) and "entities" in d)
            except (CacheMiss, HttpError) as e:
                log.warning("titles_to_qids %s failed: %s", wiki, e)
                continue
            by_title = {}
            for q, e in (r.get("entities") or {}).items():
                if "missing" in e or not is_qid(e.get("id", "")):
                    continue
                t = ((e.get("sitelinks") or {}).get(wiki) or {}).get("title")
                if t:
                    by_title[t] = e["id"]
            for t in chunk:
                q = by_title.get(t) or by_title.get(t.replace("_", " "), "")
                self.cache.put(cache_key("TITLE", wiki, {"t": t}), "TITLE", wiki, {"t": t}, q)
                if q:
                    out[t] = q
        return out

    # ------------------------------------------------------------------ search
    def search(self, text: str, *, human: bool = False, limit: int = 8) -> list[str]:
        """CirrusSearch over Wikidata items; ``human`` adds ``haswbstatement:P31=Q5``."""
        text = re.sub(r"[\"\\]", " ", text or "").strip()
        if not text:
            return []
        q = text + (" haswbstatement:P31=Q5" if human else "")
        params = {"action": "query", "list": "search", "srsearch": q, "srlimit": str(limit), "srnamespace": "0",
                  "srprop": "", "format": "json"}
        try:
            r = self.fetcher.get_json(WIKIDATA_API, params, endpoint="api",
                                      validate=lambda d: isinstance(d, dict) and "query" in d)
        except CacheMiss:
            return []
        except HttpError as e:
            log.warning("search failed for %r: %s", text, e)
            return []
        return [h["title"] for h in r.get("query", {}).get("search", []) if is_qid(h.get("title"))]

    # ------------------------------------------------------------------ Wikipedia
    def extracts(self, lang: str, titles: Iterable[str], chars: int = 600) -> dict[str, str]:
        """Plain-text intro extracts (<= ``chars``) of Wikipedia pages: title -> text.

        Cached per title (empty string = page without an intro), requested 20 titles at a time.
        """
        out: dict[str, str] = {}
        titles = sorted({t for t in titles if t})
        need = []
        for t in titles:
            hit = self.cache.get(cache_key("EXTRACT", lang, {"t": t, "chars": chars}))
            if hit is not None:
                if hit:
                    out[t] = hit
            else:
                need.append(t)
        if need and self.offline:
            return out
        api = f"https://{lang}.wikipedia.org/w/api.php"
        for i in range(0, len(need), 20):
            chunk = need[i:i + 20]
            params = {"action": "query", "prop": "extracts", "exintro": "1", "explaintext": "1",
                      "exchars": str(chars), "exlimit": "20", "redirects": "1", "titles": "|".join(chunk),
                      "format": "json", "formatversion": "2"}
            try:
                r = self.fetcher.get_json(api, params, endpoint="wiki",
                                          validate=lambda d: isinstance(d, dict))
            except CacheMiss:
                continue
            except HttpError as e:
                log.warning("extracts %s failed: %s", lang, e)
                continue
            q = r.get("query") or {}
            back = {}
            for n in q.get("normalized", []) + q.get("redirects", []):
                back[n.get("to")] = back.get(n.get("from"), n.get("from"))
            got: dict[str, str] = {}
            for p in q.get("pages", []):
                t = p.get("title", "")
                txt = (p.get("extract") or "").strip()[:chars]
                orig = back.get(t, t)
                got[orig] = txt
                got[t] = txt
            for t in chunk:
                txt = got.get(t, "")
                self.cache.put(cache_key("EXTRACT", lang, {"t": t, "chars": chars}), "EXTRACT", lang,
                               {"t": t, "chars": chars}, txt)
                if txt:
                    out[t] = txt
        return out

    # ------------------------------------------------------------------ prototype seed
    def seed_from_prototype(self, root: Path = PROTOTYPE_CACHE) -> int:
        """Load entity JSON downloaded by the research prototype (same props/langs) into the cache."""
        n = 0
        for rel in ("wikidata_match/entities.json", "wikidata_osm_etym/osm_etym_entities.json"):
            p = root / rel
            if not p.exists():
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                log.warning("prototype seed %s unreadable: %s", p, e)
                continue
            items = {q: e for q, e in data.items()
                     if is_qid(q) and isinstance(e, dict) and "missing" not in e and "claims" in e}
            have = self.cache.get_entities(list(items))
            fresh = {q: e for q, e in items.items() if q not in have}
            # entities.json mixes full person records and labels|claims-only place records
            full = {q: e for q, e in fresh.items() if "sitelinks" in e}
            partial = {q: e for q, e in fresh.items() if "sitelinks" not in e}
            if full:
                self.cache.put_entities(full, FULL_PROPS, fetched_at="prototype")
            if partial:
                self.cache.put_entities(partial, "labels|claims", fetched_at="prototype")
            n += len(fresh)
        if n:
            log.info("seeded %d entities from the prototype cache", n)
        return n
