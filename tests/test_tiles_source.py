"""Range cache and HTTP range source (fake transport, no network)."""
from __future__ import annotations

import re

import pytest

from pipeline.tiles import net, source
from pipeline.tiles.extract import extract
from pipeline.tiles.geo import BBox, count_by_zoom
from pipeline.tiles.source import HttpRangeSource, RangeCache
from pipeline.tiles.synthetic import write_synthetic


class FakeResponse:
    def __init__(self, status, body=b"", headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeSession:
    """Serves ``blob`` with HTTP Range semantics; ``script`` overrides the next responses."""

    def __init__(self, blob: bytes, etag='"v1"'):
        self.blob = blob
        self.etag = etag
        self.calls: list[str] = []
        self.script: list[FakeResponse] = []

    def get(self, url, headers=None, stream=False, timeout=None):
        rng = (headers or {}).get("Range", "")
        self.calls.append(rng)
        if self.script:
            return self.script.pop(0)
        m = re.match(r"bytes=(\d+)-(\d+)", rng)
        a, b = int(m.group(1)), min(int(m.group(2)), len(self.blob) - 1)
        return FakeResponse(206, self.blob[a:b + 1], {
            "Content-Range": f"bytes {a}-{b}/{len(self.blob)}", "ETag": self.etag})


@pytest.fixture
def fake(monkeypatch):
    sessions = {}

    def install(blob: bytes, etag='"v1"') -> FakeSession:
        s = FakeSession(blob, etag)
        sessions["s"] = s
        monkeypatch.setattr(net, "session", lambda: sessions["s"])
        monkeypatch.setattr(net.time, "sleep", lambda s: None)
        return s

    return install


def test_range_cache_serves_subranges_and_persists(tmp_path):
    c = RangeCache(tmp_path / "r")
    c.write(1000, bytes(range(200)))
    assert c.read(1000, 200) == bytes(range(200))
    assert c.read(1050, 10) == bytes(range(50, 60))
    assert c.read(990, 20) is None and c.read(1190, 20) is None
    (tmp_path / "r" / "r_5_100.bin").write_bytes(b"short")    # wrong size: dropped
    (tmp_path / "r" / "r_7_3.tmp").write_bytes(b"xyz")        # leftover temp: dropped
    c2 = RangeCache(tmp_path / "r")
    assert len(c2) == 1 and c2.read(1100, 100) == bytes(range(100, 200))
    assert not (tmp_path / "r" / "r_5_100.bin").exists()


def test_http_source_caches_and_validates(tmp_path, fake):
    blob = bytes(i % 251 for i in range(100_000))
    s = fake(blob)
    src = HttpRangeSource("https://example.test/x.pmtiles", tmp_path / "c")
    assert src.get(50_000, 1000) == blob[50_000:51_000]
    n = len(s.calls)
    assert s.calls[0] == f"bytes=0-{source.HEADER_FETCH - 1}"    # probe = header block
    assert src.get(50_100, 10) == blob[50_100:50_110]           # sub-range from cache
    assert src.get(10, 100) == blob[10:110]                     # inside cached header block
    assert len(s.calls) == n
    got = src.get_many([(60_000, 10), (70_000, 20), (50_000, 5)])
    assert got[(70_000, 20)] == blob[70_000:70_020]
    assert len(s.calls) == n + 2
    # a new run re-probes once, then serves everything from the disk cache
    src2 = HttpRangeSource("https://example.test/x.pmtiles", tmp_path / "c")
    assert src2.get(70_005, 10) == blob[70_005:70_015]
    assert len(s.calls) == n + 3 and src2.requests == 1


def test_http_source_refuses_full_body_and_bad_ranges(tmp_path, fake):
    s = fake(b"x" * 50_000)
    src = HttpRangeSource("https://example.test/x.pmtiles", tmp_path / "c")
    s.script = [FakeResponse(200, b"x" * 50_000, {"Content-Length": "50000"})]
    with pytest.raises(net.FatalHTTPError, match="ignored Range"):
        src.get(0, 10)
    src.identity_checked = True  # skip the probe for the next checks
    s.script = [FakeResponse(206, b"x" * 10, {"Content-Range": "bytes 5-14/50000"})]
    with pytest.raises(net.FatalHTTPError, match="Content-Range"):
        src.get(20_000, 10)
    s.script = [FakeResponse(404)]
    with pytest.raises(net.FatalHTTPError):
        src.get(30_000, 10)


def test_http_source_retries_transient_errors(tmp_path, fake):
    blob = bytes(range(256)) * 100
    s = fake(blob)
    src = HttpRangeSource("https://example.test/x.pmtiles", tmp_path / "c", retries=4)
    s.script = [FakeResponse(503), FakeResponse(429),
                FakeResponse(206, blob[:5], {"Content-Range": f"bytes 0-16383/{len(blob)}"})]
    assert src.get(100, 10) == blob[100:110]   # 503, 429, short read, then success
    assert len(s.calls) == 4
    s.script = [FakeResponse(503)] * 4
    with pytest.raises(net.RetryableError):
        src.get(20_000, 10)


def test_http_source_drops_cache_when_remote_changes(tmp_path, fake):
    blob = b"a" * 30_000
    fake(blob, etag='"v1"')
    src = HttpRangeSource("https://example.test/x.pmtiles", tmp_path / "c")
    src.get(20_000, 100)
    assert len(src.cache) == 2
    fake(b"b" * 30_000, etag='"v2"')
    src2 = HttpRangeSource("https://example.test/x.pmtiles", tmp_path / "c")
    assert src2.get(20_000, 100) == b"b" * 100


def test_extract_through_http_source(tmp_path, fake):
    import gzip
    from pipeline.tiles.synthetic import minimal_mvt
    tiles = {(z, x, y): gzip.compress(minimal_mvt({"earth": 1}, tag=f"{z}{x}{y}".encode()))
             for z in range(4) for x in range(1 << z) for y in range(1 << z)}
    p = write_synthetic(tmp_path / "src.pmtiles", tiles, leaf_size=5)
    fake(p.read_bytes())
    src = HttpRangeSource("https://example.test/src.pmtiles", tmp_path / "c")
    res = extract(src, tmp_path / "out.pmtiles", BBox(0, 0, 90, 60), maxzoom=3)
    assert res.addressed_tiles == sum(count_by_zoom(BBox(0, 0, 90, 60), 0, 3).values()) == 16
    assert src.requests >= 2


def test_daily_build_keys_and_urls():
    builds = [{"key": "20261023.pmtiles"}, {"key": "20230918.pmtiles"},
              {"key": "something.pmtiles"}, {"key": "20261022.pmtiles"}, {}]
    assert source.daily_build_keys(builds) == ["20230918", "20261022", "20261023"]
    assert source.build_url("20261023") == "https://build.protomaps.com/20261023.pmtiles"
    with pytest.raises(ValueError):
        source.build_url("2026-10-23")
    assert source.build_info(builds, "20261022") == {"key": "20261022.pmtiles"}
