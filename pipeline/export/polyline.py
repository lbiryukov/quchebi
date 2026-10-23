"""Google encoded polyline (precision 5 by default) and the shared e5 rounding.

Must agree with ``app/src/zone/geo.js``; shared vectors live in ``tests/vectors/polyline.json``.

Rounding is ``floor(v * 10**p + 0.5)`` (same as JS ``Math.round``), applied to absolute
coordinates first; deltas are taken between the rounded integers so no error accumulates.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

Point = Sequence[float]


def round_e(value: float, precision: int = 5) -> int:
    """Round degrees to an integer with ``precision`` decimals, half up (JS ``Math.round``)."""
    return int(math.floor(value * (10 ** precision) + 0.5))


def e5(value: float) -> int:
    """Degrees -> integer e5 (``round(deg * 1e5)``, half up)."""
    return round_e(value, 5)


def _encode_value(v: int, out: list[str]) -> None:
    v = ~(v << 1) if v < 0 else (v << 1)
    while v >= 0x20:
        out.append(chr((0x20 | (v & 0x1F)) + 63))
        v >>= 5
    out.append(chr(v + 63))


def encode_ints(points: Iterable[Sequence[int]]) -> str:
    """Encode already-rounded integer ``(lat, lon)`` pairs."""
    out: list[str] = []
    plat = plon = 0
    for lat, lon in points:
        _encode_value(lat - plat, out)
        _encode_value(lon - plon, out)
        plat, plon = lat, lon
    return "".join(out)


def encode(points: Iterable[Point], precision: int = 5) -> str:
    """Encode ``[(lat, lon), ...]`` in degrees as a Google polyline."""
    return encode_ints((round_e(lat, precision), round_e(lon, precision)) for lat, lon in points)


def decode_ints(encoded: str) -> list[tuple[int, int]]:
    """Decode a polyline into integer ``(lat, lon)`` pairs (scaled by ``10**precision``)."""
    pts: list[tuple[int, int]] = []
    i = 0
    n = len(encoded)
    lat = lon = 0
    while i < n:
        vals = []
        for _ in range(2):
            shift = result = 0
            while True:
                if i >= n:
                    raise ValueError(f"truncated polyline at char {i}")
                b = ord(encoded[i]) - 63
                i += 1
                if b < 0 or b > 63:
                    raise ValueError(f"invalid polyline char {encoded[i - 1]!r} at {i - 1}")
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            vals.append(~(result >> 1) if result & 1 else (result >> 1))
        lat += vals[0]
        lon += vals[1]
        pts.append((lat, lon))
    return pts


def decode(encoded: str, precision: int = 5) -> list[tuple[float, float]]:
    """Decode a polyline into ``[(lat, lon), ...]`` degrees."""
    f = 10 ** precision
    return [(lat / f, lon / f) for lat, lon in decode_ints(encoded)]


def dedupe_ints(points: Sequence[Sequence[int]]) -> list[tuple[int, int]]:
    """Drop consecutive duplicate integer points (they appear after rounding)."""
    out: list[tuple[int, int]] = []
    for p in points:
        t = (int(p[0]), int(p[1]))
        if not out or out[-1] != t:
            out.append(t)
    return out
