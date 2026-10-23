"""Wikidata HTTP layer: cache, offline mode, retries/back-off, entity accessors, client batching."""
from __future__ import annotations

import json

import pytest

from pipeline.wikidata.client import WikidataClient
from pipeline.wikidata.entity import claim_ids, is_qid, label, year
from pipeline.wikidata.http import CacheMiss, Fetcher, HttpCache, HttpError, cache_key


class Resp:
    def __init__(self, status=200, data=None, headers=None, text=None):
        self.status_code = status
        self._data = data
        self.headers = headers or {}
        self.text = text if text is not None else json.dumps(data)

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("GET", url, params))
        return self.responses.pop(0)

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append(("POST", url, data))
        return self.responses.pop(0)


def fetcher(responses, offline=False):
    sleeps = []
    f = Fetcher(HttpCache(":memory:"), offline=offline, session=Session(responses), sleep=sleeps.append,
                gaps={"api": 0.0, "sparql": 0.0})
    return f, sleeps


def test_cache_hit_after_first_request():
    f, _ = fetcher([Resp(200, {"ok": 1})])
    assert f.get_json("https://x/api", {"a": "1"}) == {"ok": 1}
    assert f.get_json("https://x/api", {"a": "1"}) == {"ok": 1}
    assert len(f.session.calls) == 1
    assert f.stats["hits"] == 1


def test_cache_key_is_order_insensitive():
    assert cache_key("GET", "u", {"a": 1, "b": 2}) == cache_key("get", "u", {"b": 2, "a": 1})


def test_offline_miss_raises():
    f, _ = fetcher([], offline=True)
    with pytest.raises(CacheMiss):
        f.get_json("https://x/api", {"q": "1"})


def test_429_honours_retry_after_and_doubles_gap():
    f, sleeps = fetcher([Resp(429, None, {"Retry-After": "7"}, text="slow down"), Resp(200, {"ok": 2})])
    f.gaps["api"] = 1.0
    assert f.get_json("https://x/api", {"q": "2"}) == {"ok": 2}
    assert 7.0 in sleeps
    assert f.gaps["api"] == 2.0
    assert f.stats["retries"] == 1


def test_5xx_backoff_then_give_up():
    f, sleeps = fetcher([Resp(503, None, text="x")] * 6)
    with pytest.raises(HttpError):
        f.get_json("https://x/api", {"q": "3"})
    assert len(sleeps) >= 5
    assert sleeps[1] > sleeps[0]


def test_404_is_not_retried_nor_cached():
    f, _ = fetcher([Resp(404, None, text="nope"), Resp(200, {"ok": 1})])
    with pytest.raises(HttpError):
        f.get_json("https://x/api", {"q": "4"})
    assert len(f.session.calls) == 1
    assert f.get_json("https://x/api", {"q": "4"}) == {"ok": 1}


def test_maxlag_api_error_is_retried():
    f, _ = fetcher([Resp(200, {"error": {"code": "maxlag", "lag": 1}}), Resp(200, {"entities": {}})])
    assert f.get_json("https://x/api", {"q": "5"}) == {"entities": {}}


def test_api_error_raises():
    f, _ = fetcher([Resp(200, {"error": {"code": "badvalue", "info": "x"}})])
    with pytest.raises(HttpError):
        f.get_json("https://x/api", {"q": "6"})


def _claim(p, q, rank="normal"):
    return {"mainsnak": {"snaktype": "value", "property": p,
                         "datavalue": {"type": "wikibase-entityid", "value": {"id": q}}}, "rank": rank}


def test_entity_accessors():
    e = {"labels": {"ru": {"value": "Пушкин"}},
         "claims": {"P106": [_claim("P106", "Q1"), _claim("P106", "Q2", "deprecated"), _claim("P106", "Q3", "preferred")],
                    "P569": [{"mainsnak": {"datavalue": {"type": "time", "value": {"time": "-0300-00-00T00:00:00Z"}}},
                              "rank": "normal"}]}}
    assert claim_ids(e, "P106") == ["Q3", "Q1"]
    assert year(e, "P569") == -300
    assert year(e, "P570") is None
    assert label(e, "ru") == "Пушкин" and label(e, "ka") == ""
    assert is_qid("Q42") and not is_qid("Q0") and not is_qid("L1")


def test_client_batches_by_50_and_handles_missing_and_redirects():
    ids = [f"Q{i}" for i in range(1, 61)]
    first = {q: {"id": q, "labels": {}} for q in ids[:50]}
    first["Q7"] = {"id": "Q7000", "labels": {}}            # redirect
    first["Q8"] = {"id": "Q8", "missing": ""}
    second = {q: {"id": q, "labels": {}} for q in ids[50:]}
    f, _ = fetcher([Resp(200, {"entities": first}), Resp(200, {"entities": second})])
    c = WikidataClient(f)
    got = c.entities(ids)
    assert len(f.session.calls) == 2
    assert "Q8" not in got and got["Q7"]["id"] == "Q7000"
    assert len(got) == 59
    # second call is served from the entity table, no new request
    again = c.entities(ids[:10])
    assert len(f.session.calls) == 2 and len(again) == 9
    # a fresh client on the same cache also needs no request
    c2 = WikidataClient(Fetcher(f.cache, offline=True))
    assert len(c2.entities(ids[50:])) == 10


def test_client_offline_entities_are_missing_not_errors():
    c = WikidataClient(Fetcher(HttpCache(":memory:"), offline=True))
    assert c.entities(["Q1", "Q2"]) == {}
    assert c.search("ილია ჭავჭავაძე", human=True) == []


def test_humans_by_label_caches_each_pair_including_empty():
    rows = {"results": {"bindings": [{"k": {"value": "L1"}, "item": {"value": "http://www.wikidata.org/entity/Q42"}}]}}
    f, _ = fetcher([Resp(200, rows)])
    c = WikidataClient(f)
    got = c.humans_by_label([("ილია ჭავჭავაძე", "ka"), ("Nobody Here", "en")])
    assert got == {("ილია ჭავჭავაძე", "ka"): {"Q42"}, ("Nobody Here", "en"): set()}
    assert len(f.session.calls) == 1 and f.session.calls[0][0] == "POST"
    # a different combination of already known pairs needs no request, also offline
    c2 = WikidataClient(Fetcher(f.cache, offline=True))
    assert c2.humans_by_label([("Nobody Here", "en")]) == {("Nobody Here", "en"): set()}
    assert c2.humans_by_label([("Unknown", "en")]) == {}


def test_extracts_cached_per_title():
    data = {"query": {"normalized": [{"from": "a_b", "to": "A b"}],
                      "pages": [{"title": "A b", "extract": "x" * 900}, {"title": "C", "extract": ""}]}}
    f, _ = fetcher([Resp(200, data)])
    c = WikidataClient(f)
    got = c.extracts("ka", ["a_b", "C"], chars=600)
    assert got == {"a_b": "x" * 600}
    c2 = WikidataClient(Fetcher(f.cache, offline=True))
    assert c2.extracts("ka", ["C", "a_b"], chars=600) == {"a_b": "x" * 600}
