from pathlib import Path

import httpx
import pytest

from eqrisk.config import load_sources
from eqrisk.sources.base import HttpClient

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures"
SOURCES = load_sources(ROOT / "configs")


@pytest.fixture
def sources():
    return SOURCES


@pytest.fixture
def fixture_bytes():
    return lambda name: (FIX / name).read_bytes()


@pytest.fixture
def mock_http():
    """Factory for an HttpClient backed by recorded responses (no network).

    routes: [(substring of the URL path, response)] matched in order. A response is bytes,
    (status, bytes), or a callable(request) -> httpx.Response. Unmatched paths get HTTP 404.
    """

    def make(routes, calls=None, headers=None):
        def handler(request: httpx.Request) -> httpx.Response:
            if calls is not None:
                calls.append(request)
            for key, resp in routes:
                if key in request.url.path:
                    if callable(resp):
                        return resp(request)
                    status, body = resp if isinstance(resp, tuple) else (200, resp)
                    return httpx.Response(status, content=body)
            return httpx.Response(404, content=b"not found")

        cfg = SOURCES.http.model_copy(update={"backoff_initial_s": 0.0, "backoff_max_s": 0.0})
        return HttpClient(cfg, per_second=10_000.0, headers=headers, transport=httpx.MockTransport(handler))

    return make
