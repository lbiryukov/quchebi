"""Small HTTP helpers with retries (polite: project User-Agent, bounded backoff)."""
from __future__ import annotations

import threading
import time
from typing import Callable

import requests

from pipeline.config import USER_AGENT
from pipeline.log import get_logger

log = get_logger(__name__)

RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
DEFAULT_TIMEOUT = (15, 120)  # connect, read (seconds)


class RetryableError(Exception):
    """Transient failure: the request may be retried."""


class FatalHTTPError(Exception):
    """Permanent failure: retrying will not help."""


_local = threading.local()


def session() -> requests.Session:
    """One ``requests.Session`` per thread (sessions are not guaranteed thread-safe)."""
    s = getattr(_local, "session", None)
    if s is None:
        s = requests.Session()
        s.headers["User-Agent"] = USER_AGENT
        _local.session = s
    return s


def with_retries(fn: Callable[[], bytes], what: str, retries: int = 5, base_delay: float = 1.5,
                 sleep: Callable[[float], None] | None = None) -> bytes:
    """Call ``fn`` until it succeeds; retry on ``RetryableError`` and network errors."""
    sleep = sleep or time.sleep
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except FatalHTTPError:
            raise
        except (RetryableError, requests.ConnectionError, requests.Timeout,
                requests.exceptions.ChunkedEncodingError) as exc:
            if attempt >= retries:
                log.error("%s: giving up after %d attempts: %s", what, attempt, exc)
                raise
            delay = base_delay * (2 ** (attempt - 1))
            log.warning("%s: attempt %d/%d failed (%s), retry in %.1fs", what, attempt, retries,
                        exc, delay)
            sleep(delay)


def get_bytes(url: str, *, allow_404: bool = False, retries: int = 5,
              timeout: tuple[int, int] = DEFAULT_TIMEOUT, max_bytes: int = 64 << 20) -> bytes | None:
    """GET a whole (small) resource. Returns ``None`` on 404 when ``allow_404``."""

    def once() -> bytes | None:
        with session().get(url, stream=True, timeout=timeout) as r:
            if r.status_code in RETRY_STATUSES:
                raise RetryableError(f"HTTP {r.status_code}")
            if r.status_code == 404 and allow_404:
                return None
            if r.status_code != 200:
                raise FatalHTTPError(f"GET {url}: HTTP {r.status_code}")
            chunks, total = [], 0
            for chunk in r.iter_content(1 << 16):
                total += len(chunk)
                if total > max_bytes:
                    raise FatalHTTPError(f"GET {url}: body exceeds {max_bytes} bytes")
                chunks.append(chunk)
            expected = r.headers.get("Content-Length")
            body = b"".join(chunks)
            if expected is not None and r.headers.get("Content-Encoding") in (None, "identity"):
                if int(expected) != len(body):
                    raise RetryableError(f"short read {len(body)}/{expected}")
            return body

    return with_retries(once, f"GET {url}", retries=retries)  # type: ignore[return-value]
