"""Ingest adapter interface (blueprint §3.2) and shared HTTP plumbing.

Adapters implement `fetch(start, end) -> polars.DataFrame` and write only under data/raw.
The HTTP client rate-limits per source, retries transient failures with exponential backoff
(tenacity), and never lets a URL query string (where API keys travel) reach an exception
message or a log line.
"""

from __future__ import annotations

import re
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urlsplit

import httpx
import polars as pl
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from eqrisk.config import HttpCfg


class SourceError(RuntimeError):
    """A vendor call failed after retries."""


class EntitlementError(SourceError):
    """HTTP 401/403: the subscription does not cover this endpoint."""


class NotFoundError(SourceError):
    """HTTP 404, or an S3-style 403 for a missing file: the vendor has no such object (symbol, CIK, file)."""


class CostGuardError(SourceError):
    """A metered request would exceed the configured spending cap."""


def safe_url(url: str | httpx.URL) -> str:
    """scheme://host/path with the query string dropped (API keys live there)."""
    parts = urlsplit(str(url))
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


# S3-style storage answers 403 AccessDenied, not 404, for a file that does not exist, because a public
# bucket cannot be listed. SEC EDGAR's Archives work this way: weekend and holiday daily indexes, a
# quarter not started (DECISIONS D-026). That is "no such object". A real refusal (SEC's rate limit, a
# missing User-Agent) is an HTML page and stays an EntitlementError.
_S3_MISSING = re.compile(rb"<Error>\s*<Code>(?:AccessDenied|NoSuchKey)</Code>")


class RateLimiter:
    """Spaces calls at least 1/rate seconds apart; thread-safe."""

    def __init__(self, per_second: float) -> None:
        self._interval = 1.0 / per_second
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                time.sleep(self._next - now)
                now = self._next
            self._next = now + self._interval


class _Transient(Exception):
    """Internal marker: retry this attempt."""


def _is_transient(exc: BaseException) -> bool:
    return isinstance(exc, (_Transient, httpx.TransportError))


class HttpClient:
    def __init__(self, cfg: HttpCfg, per_second: float, headers: dict[str, str] | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(timeout=cfg.timeout_s, follow_redirects=True,
                                    headers=headers or {}, transport=transport)
        self._limiter = RateLimiter(per_second)
        self._cfg = cfg

    def get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        retrying = Retrying(
            stop=stop_after_attempt(self._cfg.retries),
            wait=wait_exponential_jitter(initial=self._cfg.backoff_initial_s, max=self._cfg.backoff_max_s,
                                         jitter=self._cfg.backoff_initial_s),
            retry=retry_if_exception(_is_transient),
            reraise=True,
        )
        try:
            for attempt in retrying:
                with attempt:
                    self._limiter.wait()
                    r = self._client.get(url, params=params)
                    if r.status_code in (429, 500, 502, 503, 504):
                        raise _Transient(f"HTTP {r.status_code}")
        except (_Transient, httpx.TransportError) as exc:
            raise SourceError(f"{safe_url(url)}: gave up after {self._cfg.retries} attempts "
                              f"({type(exc).__name__}: {exc})") from None
        if r.status_code == 403 and _S3_MISSING.search(r.content):
            raise NotFoundError(f"{safe_url(url)} -> HTTP 403 (no such object)")
        if r.status_code in (401, 403):
            raise EntitlementError(f"{safe_url(url)} -> HTTP {r.status_code}")
        if r.status_code == 404:
            raise NotFoundError(f"{safe_url(url)} -> HTTP 404")
        if r.status_code >= 400:
            raise SourceError(f"{safe_url(url)} -> HTTP {r.status_code}")
        return r

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        return self.get(url, params).json()

    def close(self) -> None:
        self._client.close()


@dataclass
class IngestReport:
    """What one adapter pulled and wrote; lands in the run manifest."""

    source: str
    dataset: str
    rows: int = 0
    partitions_written: int = 0
    partitions_unchanged: int = 0
    missing: list[str] = field(default_factory=list)     # objects the vendor had no data for
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "dataset": self.dataset, "rows": self.rows,
                "written": self.partitions_written, "unchanged": self.partitions_unchanged,
                "missing": len(self.missing), "missing_sample": self.missing[:20],
                "errors": self.errors[:20]}


class Source(ABC):
    """One vendor dataset. `fetch` returns a polars frame and never touches disk."""

    name: str
    dataset: str

    @abstractmethod
    def fetch(self, start: date, end: date) -> pl.DataFrame:
        raise NotImplementedError
