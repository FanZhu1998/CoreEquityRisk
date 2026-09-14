"""`eqrisk serve` (eqrisk/pipeline/feed.py): the protocol, the JSON-safety of every result, and on the
development data every method, the real stdio process, and that no response ever carries a key."""

from __future__ import annotations

import io
import json
import math
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from eqrisk.pipeline import feed as feedmod
from eqrisk.pipeline.feed import Feed, _holdings_frame, clean, serve

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "data" / "model"
KEY_VARS = {"EODHD_API_KEY", "FRED_API_KEY", "DATABENTO_API_KEY", "NASDAQ_DATA_LINK_API_KEY", "SEC_USER_AGENT"}


# ---- JSON safety ---------------------------------------------------------------------------------

def test_clean_makes_everything_json_safe() -> None:
    out = clean({"a": float("nan"), "b": np.float64("inf"), "c": np.int64(3), "d": np.bool_(True),
                 "e": date(2026, 9, 11), "f": datetime(2026, 9, 11, 18, 30), "g": np.array([1.5, np.nan]),
                 "h": pl.DataFrame({"x": [1.0, None]}), "i": Path("C:/x"), "j": (1, 2)})
    assert out == {"a": None, "b": None, "c": 3, "d": True, "e": "2026-09-11", "f": "2026-09-11T18:30:00",
                   "g": [1.5, None], "h": [{"x": 1.0}, {"x": None}], "i": str(Path("C:/x")), "j": [1, 2]}
    json.dumps(out, allow_nan=False)                     # strict JSON: would raise on NaN


def test_holdings_are_validated_and_normalized() -> None:
    f = _holdings_frame([{"ticker": " aapl ", "weight": 0.6}, {"ticker": "msft", "weight": "0.4"}])
    assert f.columns == ["ticker", "weight"] and f["ticker"].to_list() == ["AAPL", "MSFT"]
    g = _holdings_frame([{"ticker": "AAPL", "weight": 0.5, "bench_weight": 0.4}, {"ticker": "MSFT", "weight": 0.5}])
    assert g["bench_weight"].to_list() == [0.4, 0.0]
    for bad in (None, [], [{"ticker": "AAPL"}], ["AAPL"]):
        with pytest.raises(ValueError):
            _holdings_frame(bad)


# ---- the protocol loop, with a fake feed ---------------------------------------------------------

class FakeFeed:
    def hello(self, _: Any) -> dict[str, Any]:
        return {"protocol": feedmod.PROTOCOL, "model_id": "test"}

    def call(self, method: str, params: dict[str, Any]) -> Any:
        if method == "echo":
            return params
        if method == "nan":
            return {"x": float("nan")}                   # a method that forgot to clean its result
        raise KeyError(f"unknown method {method!r}")


def _talk(lines: list[str]) -> list[dict[str, Any]]:
    out = io.StringIO()
    serve(None, io.StringIO("\n".join(lines) + "\n"), out, feed=FakeFeed())  # type: ignore[arg-type]
    return [json.loads(x) for x in out.getvalue().splitlines()]


def test_ready_then_one_response_per_request_in_order() -> None:
    msgs = _talk(['{"id": 1, "method": "echo", "params": {"a": 1}}', "", '{"id": 2, "method": "echo"}'])
    assert msgs[0]["event"] == "ready" and msgs[0]["protocol"] == feedmod.PROTOCOL
    assert [(m["id"], m["ok"], m["result"]) for m in msgs[1:]] == [(1, True, {"a": 1}), (2, True, {})]


def test_failures_become_responses_and_the_server_keeps_going() -> None:
    msgs = _talk(["not json", "[1, 2]", '{"id": 3, "method": "nope"}', '{"id": 4, "method": "echo", "params": 5}',
                  '{"id": 5, "method": "nan"}', '{"id": 6, "method": "echo", "params": {"ok": true}}'])[1:]
    assert [m["ok"] for m in msgs] == [False, False, False, False, False, True]
    assert msgs[0]["error"]["type"] == "BadRequest" and msgs[0]["id"] is None
    assert msgs[2]["error"]["type"] == "KeyError" and msgs[2]["id"] == 3
    assert msgs[4]["error"]["type"] == "ValueError"       # NaN refused: stdout only ever carries valid JSON
    assert msgs[5]["result"] == {"ok": True}


def test_shutdown_stops_the_loop() -> None:
    msgs = _talk(['{"id": 1, "method": "shutdown"}', '{"id": 2, "method": "echo"}'])
    assert [m.get("id") for m in msgs] == [None, 1]        # ready, then the shutdown reply; request 2 unread


# ---- on the development data ----------------------------------------------------------------------

def _env_values() -> dict[str, str]:
    """The real .env values, to prove none of them ever reaches a response (never printed)."""
    path = ROOT / ".env"
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.strip().partition("=")
        value = value.strip().strip('"').strip("'")
        if sep and not name.startswith("#") and len(value) >= 8:
            out[name] = value
    return out


@pytest.fixture(scope="module")
def live_feed() -> Feed:
    if not (MODEL / "us_lc_v1" / "factor_returns").exists():
        pytest.skip("no model tables; run `eqrisk backfill --stage model`")
    from eqrisk.config import load_project

    return Feed(load_project(ROOT, None))


@pytest.mark.golden
def test_every_method_answers_on_the_real_model(live_feed: Feed) -> None:
    f = live_feed
    st = f.call("status", {})
    assert st["last"] and st["latest_good"] and isinstance(st["pending"], list)
    assert {k["env"] for k in st["keys"]} >= {"EODHD_API_KEY", "FRED_API_KEY", "SEC_USER_AGENT"}
    assert all(set(k) == {"env", "present"} and isinstance(k["present"], bool) for k in st["keys"])
    d = f.call("dates", {})
    assert d["latest"] in d["dates"]
    day = f.call("day", {})
    assert day["date"] == st["last"] and len(day["moves"]) >= 30
    assert {m["group"] for m in day["moves"]} == {"country", "industry", "style"}
    assert 0 < day["r2"] < 1 and day["n"] > 400
    hist = f.call("history", {"years": 1})
    assert len(hist["vra_dates"]) == len(hist["lambda_F"]) > 200 and "SIZE" in hist["cum"]
    fr = f.call("factor_risk", {})
    k = len(fr["factors"])
    assert len(fr["corr"]) == k and all(abs(fr["corr"][i][i] - 1) < 1e-3 for i in range(k))
    assert all(0 < r["vol_ann"] < 1 for r in fr["factors"])
    ex = f.call("exposures", {})
    assert len(ex["rows"]) > 400 and set(ex["styles"]) <= set(ex["rows"][0])
    sp = f.call("specific", {})
    assert len(sp["rows"]) > 400 and 0 < sp["rows"][0]["sigma_final"] < 3
    inv = f.call("inventory", {})
    assert inv["eod_sessions"] > 1000 and inv["data_bytes"] > 0
    exc = f.call("exceptions", {"limit": 10})
    assert exc["total"] >= len(exc["rows"]) and sum(c["count"] for c in exc["counts"]) == exc["total"]
    runs = f.call("runs", {"limit": 5})
    assert len(runs["runs"]) <= 5
    cfg = f.call("config", {})
    assert cfg["model_id"] == st["model_id"] and cfg["horizon_days"] > 0
    val = f.call("validation", {})
    assert val is None or (val["scorecard"] and isinstance(val["factor"][0]["bias"], float))
    assert val is None or all(isinstance(r["inside"], bool) for r in val["factor"])   # CSV "true" became a bool
    out = f.call("outputs", {})
    assert set(out) == {"site", "exports_dir", "snapshots"} and isinstance(out["site"]["exported"], bool)
    assert out["site"]["modified"] is None or "+" in out["site"]["modified"] or "-" in out["site"]["modified"][19:]


@pytest.mark.golden
def test_portfolio_and_optimize_on_the_real_model(live_feed: Feed) -> None:
    rep = live_feed.call("portfolio", {"holdings": [{"ticker": "AAPL", "weight": 0.5}, {"ticker": "MSFT", "weight": 0.5},
                                                    {"ticker": "NOT_A_TICKER", "weight": 0.1}]})
    assert rep["unmatched"] == ["NOT_A_TICKER"] and rep["matched"] == 2 and not rep["active"]
    assert 0.05 < rep["sigma_ann"] < 1 and abs(rep["factor_share"] + rep["specific_share"] - 1) < 1e-6
    opt = live_feed.call("optimize", {"method": "factor", "te_max_ann": 0.03, "style_band": 0.1, "ind_band": 0.02,
                                      "w_max": 0.05, "turnover_max": 1.0})
    assert opt["ok"], opt
    assert opt["names_held"] > 20 and abs(sum(h["weight"] for h in opt["holdings"]) - 1) < 1e-3
    assert opt["sigma_ann"] <= 0.03 + 1e-3              # active risk respects the tracking-error cap


@pytest.mark.golden
def test_no_response_ever_contains_a_key(live_feed: Feed) -> None:
    secrets = _env_values()
    if not secrets:
        pytest.skip("no .env on this machine")
    for method in live_feed.methods:
        if method in ("portfolio", "optimize"):
            continue
        text = json.dumps(live_feed.call(method, {}))
        for name, value in secrets.items():
            assert value not in text, f"{method} leaked {name}"   # the name only: never the value


@pytest.mark.golden
def test_the_real_process_speaks_only_protocol_on_stdout(live_feed: Feed) -> None:
    """The contract the C# app relies on: `python -m eqrisk.cli serve` over real pipes, keys scrubbed."""
    env = {k: v for k, v in os.environ.items() if k not in KEY_VARS}
    proc = subprocess.run([sys.executable, "-m", "eqrisk.cli", "serve", "--root", str(ROOT)], cwd=ROOT, env=env,
                          input='{"id":1,"method":"hello"}\n{"id":2,"method":"status"}\n{"id":3,"method":"shutdown"}\n',
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    msgs = [json.loads(x) for x in proc.stdout.splitlines()]   # every stdout line is one JSON object
    assert msgs[0]["event"] == "ready"
    assert [m["id"] for m in msgs[1:]] == [1, 2, 3] and all(m["ok"] for m in msgs[1:])
    assert msgs[1]["result"]["model_id"] == msgs[0]["model_id"]
    assert proc.returncode == 0 and math.isfinite(msgs[1]["ms"])
