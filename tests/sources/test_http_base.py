import time

import httpx
import pytest

from eqrisk.sources.base import EntitlementError, NotFoundError, RateLimiter, SourceError


def test_retries_transient_errors_then_succeeds(mock_http):
    statuses = iter([503, 502, 200])
    calls = []
    http = mock_http([("/x", lambda req: httpx.Response(next(statuses), content=b'{"ok": 1}'))], calls=calls)
    assert http.get_json("https://h.example/x", {"api_token": "SECRET"}) == {"ok": 1}
    assert len(calls) == 3


def test_gives_up_without_leaking_the_query_string(mock_http, sources):
    calls = []
    http = mock_http([("/x", (500, b""))], calls=calls)
    with pytest.raises(SourceError) as err:
        http.get("https://h.example/x", {"api_token": "SECRET"})
    assert len(calls) == sources.http.retries
    assert "SECRET" not in str(err.value) and "h.example/x" in str(err.value)


def test_entitlement_and_not_found_are_distinct(mock_http):
    http = mock_http([("/forbidden", (403, b"")), ("/gone", (404, b""))])
    with pytest.raises(EntitlementError) as err:
        http.get("https://h.example/forbidden", {"api_token": "SECRET"})
    assert "SECRET" not in str(err.value)
    with pytest.raises(NotFoundError):
        http.get("https://h.example/gone")


def test_rate_limiter_spaces_calls():
    limiter = RateLimiter(50.0)
    t0 = time.monotonic()
    for _ in range(6):
        limiter.wait()
    assert time.monotonic() - t0 >= 5 / 50 * 0.9
