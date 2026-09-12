"""The browser analyzer (site_template/analyzer.js) reproduces eqrisk.analytics.risk (Phase 11)."""

from datetime import date
from pathlib import Path

import numpy as np
import pytest

from eqrisk.analytics.risk import portfolio_risk
from eqrisk.model.snapshot import RiskModelSnapshot
from eqrisk.pipeline.export_site import snapshot_from_payload, snapshot_payload

mini_racer = pytest.importorskip("py_mini_racer")
ROOT = Path(__file__).resolve().parents[2]
TOL = 1e-10


def _snapshot(seed=7, N=300, n_ind=8, n_sty=12):
    rng = np.random.default_rng(seed)
    K = 1 + n_ind + n_sty
    X = np.column_stack([np.ones(N), np.eye(n_ind)[rng.integers(0, n_ind, N)], rng.standard_normal((N, n_sty))])
    B = rng.standard_normal((K, K)) * 0.01
    estu = rng.uniform(size=N) > 0.1
    return RiskModelSnapshot(
        as_of=date(2026, 9, 10), model_id="t", sids=np.arange(N), tickers=np.array([f"T{i}" for i in range(N)]),
        factors=["COUNTRY", *[f"IND{i}" for i in range(n_ind)], *[f"STY{i}" for i in range(n_sty)]],
        groups={"country": np.array([0]), "industry": np.arange(1, 1 + n_ind), "style": np.arange(1 + n_ind, K)},
        X=X, F=B @ B.T + 1e-4 * np.eye(K), spec_var=rng.uniform(1e-4, 4e-3, N),
        mcap=np.exp(rng.normal(23, 1.3, N)), in_estu=estu)


@pytest.fixture(scope="module")
def js():
    ctx = mini_racer.MiniRacer()
    ctx.eval((ROOT / "site_template" / "analyzer.js").read_text(encoding="utf-8"))
    return ctx


@pytest.mark.parametrize("active", [False, True])
def test_js_matches_python(js, active):
    payload = snapshot_payload(_snapshot())
    snap = snapshot_from_payload(payload)
    rng = np.random.default_rng(1)
    h = rng.dirichlet(np.ones(len(snap.tickers)))
    hb = rng.dirichlet(np.ones(len(snap.tickers))) if active else None
    got = js.call("EQRisk.portfolioRisk", payload, h.tolist(), None if hb is None else hb.tolist())
    ref = portfolio_risk(snap, h, hb)
    assert got["sigma"] == pytest.approx(ref.sigma, rel=TOL, abs=0)
    assert got["specific_var"] == pytest.approx(ref.specific_var, rel=TOL)
    assert got["beta"] == pytest.approx(ref.beta, rel=TOL)
    for key, col, table in (("exposures", "exposure", ref.factors), ("contrib_var", "contrib_var", ref.factors),
                            ("xsr", "xsr", ref.factors), ("mctr", "mctr", ref.assets), ("betas", "beta", ref.assets)):
        np.testing.assert_allclose(got[key], table[col].to_numpy(), rtol=TOL, atol=TOL * 1e-3, err_msg=key)
    groups = dict(ref.groups.select("group", "contrib_var").iter_rows())
    for g, v in got["groups"].items():
        assert v == pytest.approx(groups[g], rel=TOL, abs=1e-18), g


def test_js_holdings_parsing(js):
    payload = snapshot_payload(_snapshot(N=5, n_ind=2, n_sty=2))
    rows = [{"ticker": "t1", "weight": 0.6, "bench_weight": 0.5}, {"ticker": "NOPE", "weight": 0.4, "bench_weight": 0.5}]
    got = js.call("EQRisk.holdingsVectors", payload, rows)
    assert got["h"][1] == 0.6 and got["hb"][1] == 0.5 and got["unmatched"] == ["NOPE"]
