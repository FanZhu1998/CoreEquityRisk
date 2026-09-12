"""Phase 11 acceptance (blueprint §17): the workbench pages and the static viewer on the development data."""

import functools
import http.server
import json
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from eqrisk.analytics.risk import holdings_vectors, portfolio_risk
from eqrisk.config import load_project
from eqrisk.model.snapshot import ModelStore
from eqrisk.pipeline.export_site import TEMPLATE_FILES, export_site, snapshot_from_payload

pytestmark = pytest.mark.golden
ROOT = Path(__file__).resolve().parents[2]
PAGES = ["status", "factor_returns", "factor_risk", "exposures", "specific_risk", "portfolio", "validation",
         "optimizer"]
VENDOR_FIELDS = {"close", "close_unadj", "open", "high", "low", "volume", "adjusted_close", "prev_close",
                 "mcap", "mcap_issuer", "mcap_sid", "shares_out", "dividend", "price", "public_float",
                 "ni_ttm", "cfo_ttm", "book_equity", "total_assets", "revenue_fy", "eps_fy", "long_term_debt"}
SVG_NS = "http://www.w3.org/2000/svg"


@pytest.fixture(scope="module")
def project():
    p = load_project(ROOT)
    if not (p.model_dir / "specific_risk").exists():
        pytest.skip("no model tables; run `eqrisk backfill --stage model`")
    return p


@pytest.fixture(scope="module")
def site(project, tmp_path_factory):
    return export_site(project, out=tmp_path_factory.mktemp("export") / "site")


def _keys(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            _keys(v, out)
    elif isinstance(obj, list):
        for v in obj[:3]:
            _keys(v, out)
    return out


def test_site_is_small_self_contained_and_holds_no_vendor_data(site):
    path, size = site
    assert size < 5_000_000, size
    for name in TEMPLATE_FILES:
        text = (path / name).read_text(encoding="utf-8").replace(SVG_NS, "")
        assert "http://" not in text and "https://" not in text and "//cdn" not in text, name
    keys = set()
    for f in (path / "data").glob("*.json"):
        _keys(json.loads(f.read_text(encoding="utf-8")), keys)
    assert not keys & VENDOR_FIELDS, keys & VENDOR_FIELDS
    print(f"\nsite {size / 1e6:.2f} MB; data keys {sorted(keys)}")


def test_site_works_from_a_plain_http_server(site):
    path, _ = site
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        for rel in ("index.html", "app.js", "analyzer.js", "style.css", "data/snapshot.json", "data/history.json",
                    "data/status.json", "data/specific.json", "data/validation.json"):
            with urllib.request.urlopen(f"{base}/{rel}", timeout=10) as r:
                assert r.status == 200, rel
    finally:
        server.shutdown()


def test_browser_analyzer_matches_python_on_the_export(site):
    mini_racer = pytest.importorskip("py_mini_racer")
    path, _ = site
    payload = json.loads((path / "data" / "snapshot.json").read_text(encoding="utf-8"))
    snap = snapshot_from_payload(payload)
    holdings = pl.read_csv(ROOT / "app" / "sample_holdings.csv")
    h, _, unmatched = holdings_vectors(snap, holdings)
    assert not unmatched
    ctx = mini_racer.MiniRacer()
    ctx.eval((path / "analyzer.js").read_text(encoding="utf-8"))
    parsed = ctx.call("EQRisk.holdingsVectors", payload, holdings.to_dicts())
    assert np.array_equal(parsed["h"], h)
    bench = np.where(snap.in_estu, np.nan_to_num(snap.mcap), 0.0)
    for hb in (None, bench / bench.sum()):
        got = ctx.call("EQRisk.portfolioRisk", payload, h.tolist(), None if hb is None else hb.tolist())
        ref = portfolio_risk(snap, h, hb)
        assert got["sigma"] == pytest.approx(ref.sigma, rel=1e-8) and got["beta"] == pytest.approx(ref.beta, rel=1e-8)
        np.testing.assert_allclose(got["mctr"], ref.assets["mctr"].to_numpy(), rtol=1e-8, atol=1e-12)
        np.testing.assert_allclose(got["contrib_var"], ref.factors["contrib_var"].to_numpy(), rtol=1e-8, atol=1e-14)


def test_workbench_pages_load_from_cache_within_two_seconds(project, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("EQRISK_ROOT", str(ROOT))
    at = AppTest.from_file(str(ROOT / "app" / "main.py"), default_timeout=600)
    at.run()
    assert not at.exception, at.exception
    timings = {}
    for page in PAGES:
        at.switch_page(f"views/{page}.py").run()                      # first visit fills the cache
        assert not at.exception, (page, at.exception)
        t0 = time.perf_counter()
        at.run()
        timings[page] = round(time.perf_counter() - t0, 3)
        assert not at.exception, (page, at.exception)
    print("\npage reruns (s):", timings)
    assert max(timings.values()) < 2.0, timings

    at.switch_page("views/portfolio.py").run()
    rep = at.session_state["portfolio_report"]
    store = ModelStore(project)
    snap = store.snapshot(store.latest())
    h, hb, _ = holdings_vectors(snap, pl.read_csv(ROOT / "app" / "sample_holdings.csv"))
    ref = portfolio_risk(snap, h, hb)
    assert abs(rep.sigma - ref.sigma) < 1e-10 and abs(rep.beta - ref.beta) < 1e-10
    assert np.max(np.abs(rep.assets["mctr"].to_numpy() - ref.assets["mctr"].to_numpy())) < 1e-10
