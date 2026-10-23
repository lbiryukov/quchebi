"""Encoded polyline: shared vectors (Python and JS must agree) and round-trips."""
import json
from pathlib import Path

import pytest

from pipeline.export.polyline import decode, decode_ints, dedupe_ints, e5, encode, encode_ints, round_e

VECTORS = json.loads((Path(__file__).parent / "vectors" / "polyline.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["name"] for c in VECTORS["cases"]])
def test_encode_matches_vector(case):
    assert encode(case["points"], VECTORS["precision"]) == case["encoded"]


@pytest.mark.parametrize("case", VECTORS["cases"], ids=[c["name"] for c in VECTORS["cases"]])
def test_decode_matches_vector(case):
    got = decode(case["encoded"], VECTORS["precision"])
    assert len(got) == len(case["points"])
    for (glat, glon), (lat, lon) in zip(got, case["points"]):
        assert glat == pytest.approx(lat, abs=1e-9)
        assert glon == pytest.approx(lon, abs=1e-9)


def test_empty_and_single():
    assert encode([]) == ""
    assert decode("") == []
    assert decode_ints(encode_ints([(4169420, 4480150)])) == [(4169420, 4480150)]


def test_roundtrip_long_line_no_drift():
    pts = [(41.7 + i * 0.0000137, 44.8 - i * 0.0000291) for i in range(2000)]
    ints = [(e5(a), e5(b)) for a, b in pts]
    assert decode_ints(encode(pts)) == ints


def test_round_half_up_like_js():
    assert round_e(0.000005) == 1
    assert round_e(-0.000005) == 0  # Math.round(-0.5) === -0
    assert e5(41.70095) == 4170095


def test_invalid_input_raises():
    with pytest.raises(ValueError):
        decode_ints("_p~iF~ps|U_ulLnnqC_mqNvxq`")  # truncated last value
    with pytest.raises(ValueError):
        decode_ints("\x01\x02")


def test_dedupe_ints():
    assert dedupe_ints([(1, 2), (1, 2), (3, 4), (1, 2)]) == [(1, 2), (3, 4), (1, 2)]
