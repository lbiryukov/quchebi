"""Upward P279 (subclass of) closure computed through ``wbgetentities`` (no SPARQL).

``ancestors(["Q49757"])`` -> {"Q49757": {"Q49757", "Q36180", "Q482980", ...}}. Very generic classes
are not expanded further (their ancestors never matter for the taxonomy).
"""
from __future__ import annotations

from typing import Iterable

from pipeline.log import get_logger
from pipeline.wikidata.client import WikidataClient
from pipeline.wikidata.entity import claim_ids, is_qid

log = get_logger(__name__)

# upper-ontology classes: reaching them adds nothing useful and expanding them is expensive
STOP = {
    "Q35120", "Q488383", "Q7184903", "Q16889133", "Q24229398", "Q151885", "Q1190554", "Q99527517", "Q23958946",
    "Q2424752", "Q58415929", "Q223557", "Q4406616", "Q937228", "Q337060", "Q29651224", "Q16722960", "Q17008256",
    "Q21146257", "Q830077", "Q15474042", "Q1914636", "Q5127848", "Q8205328", "Q28813620", "Q115668764",
}
MAX_DEPTH = 12


class ClassIndex:
    def __init__(self, client: WikidataClient):
        self.client = client
        self._parents: dict[str, list[str]] = {}
        self._anc: dict[str, frozenset[str]] = {}

    def _load_parents(self, qids: Iterable[str]) -> None:
        need = [q for q in qids if q not in self._parents and is_qid(q)]
        if not need:
            return
        ents = self.client.entities(need, props="labels|claims", langs="ru|en")
        for q in need:
            e = ents.get(q)
            self._parents[q] = [] if q in STOP else claim_ids(e, "P279")

    def ancestors(self, qids: Iterable[str]) -> dict[str, frozenset[str]]:
        qids = [q for q in dict.fromkeys(qids) if is_qid(q)]
        todo = [q for q in qids if q not in self._anc]
        if todo:
            frontier = set(todo)
            seen = set(todo)
            for _ in range(MAX_DEPTH):
                self._load_parents(frontier)
                nxt = set()
                for q in frontier:
                    for p in self._parents.get(q, []):
                        if p not in seen:
                            seen.add(p)
                            nxt.add(p)
                if not nxt:
                    break
                frontier = nxt
            for q in todo:
                self._anc[q] = frozenset(self._walk(q))
            log.debug("closure: %d classes, %d known parents", len(todo), len(self._parents))
        return {q: self._anc[q] for q in qids}

    def _walk(self, q: str) -> set[str]:
        out = {q}
        stack = [(q, 0)]
        while stack:
            cur, d = stack.pop()
            if d >= MAX_DEPTH:
                continue
            for p in self._parents.get(cur, []):
                if p not in out:
                    out.add(p)
                    stack.append((p, d + 1))
        return out
