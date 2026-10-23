"""P279 closure through wbgetentities."""
from __future__ import annotations

from pipeline.wikidata.classes import STOP, ClassIndex


class Client:
    def __init__(self, parents):
        self.parents = parents
        self.requests = 0

    def entities(self, ids, props="", langs=""):
        self.requests += 1
        out = {}
        for q in ids:
            if q in self.parents:
                out[q] = {"id": q, "claims": {"P279": [
                    {"mainsnak": {"datavalue": {"type": "wikibase-entityid", "value": {"id": p}}}, "rank": "normal"}
                    for p in self.parents[q]]}}
        return out


def test_chain_and_cycle():
    stop = next(iter(STOP))
    c = Client({"Q901001": ["Q901002"], "Q901002": ["Q901003"], "Q901003": ["Q901004", stop],
                "Q901004": ["Q901003"], stop: ["Q901005"]})
    idx = ClassIndex(c)
    anc = idx.ancestors(["Q901001"])["Q901001"]
    assert {"Q901001", "Q901002", "Q901003", "Q901004", stop} <= anc
    assert "Q901005" not in anc                      # STOP classes are not expanded
    n = c.requests
    assert idx.ancestors(["Q901001"])["Q901001"] == anc
    assert c.requests == n                        # memoized


def test_unknown_class_is_its_own_ancestor():
    idx = ClassIndex(Client({}))
    assert idx.ancestors(["Q999"]) == {"Q999": frozenset({"Q999"})}
