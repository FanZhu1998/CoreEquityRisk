"""Phase 8 acceptance on a synthetic snapshot: the decompositions add up exactly."""

from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from eqrisk.analytics.risk import holdings_vectors, market_portfolio, portfolio_risk
from eqrisk.model.snapshot import RiskModelSnapshot


def make_snapshot(seed=3, N=250, n_ind=6, n_sty=12):
    rng = np.random.default_rng(seed)
    ind = rng.integers(0, n_ind, N)
    K = 1 + n_ind + n_sty
    X = np.column_stack([np.ones(N), np.eye(n_ind)[ind], rng.standard_normal((N, n_sty))])
    B = rng.standard_normal((K, K)) * 0.01
    F = B @ B.T + 1e-4 * np.eye(K)
    mcap = np.exp(rng.normal(23, 1.2, N))
    factors = ["COUNTRY", *[f"IND{i}" for i in range(n_ind)], *[f"STY{i}" for i in range(n_sty)]]
    return RiskModelSnapshot(
        as_of=date(2024, 6, 3), model_id="t", sids=np.arange(1, N + 1), tickers=np.array([f"T{i}" for i in range(N)]),
        factors=factors, groups={"country": np.array([0]), "industry": np.arange(1, 1 + n_ind),
                                 "style": np.arange(1 + n_ind, K)},
        X=X, F=F, spec_var=(rng.uniform(0.04, 0.12, N) / np.sqrt(12)) ** 2, mcap=mcap, in_estu=np.ones(N, bool))


@pytest.fixture(scope="module")
def snap():
    return make_snapshot()


def test_total_risk_decompositions_add_up(snap):
    h = np.random.default_rng(1).dirichlet(np.ones(len(snap.sids)))
    r = portfolio_risk(snap, h)
    s = r.sigma
    assert r.factors["contrib_var"].sum() + r.specific_var == pytest.approx(s * s, rel=1e-12)
    assert r.assets["contrib"].sum() == pytest.approx(s, rel=1e-12)                 # sum h * MCTR = sigma
    assert r.factors["xsr"].sum() + r.specific_var / s == pytest.approx(s, rel=1e-12)
    g = dict(r.groups.select("group", "contrib_var").iter_rows())
    assert g["country"] + g["industry"] + g["style"] == pytest.approx(r.factor_var, rel=1e-12)
    assert r.sigma_ann == pytest.approx(s * np.sqrt(12))
    assert np.allclose(r.factors["exposure"].to_numpy(), snap.X.T @ h)


def test_asset_covariance_is_psd_and_matches_the_factor_form(snap):
    S = snap.asset_cov()
    h = market_portfolio(snap)
    assert np.linalg.eigvalsh(S).min() > 0
    assert np.sqrt(h @ S @ h) == pytest.approx(portfolio_risk(snap, h).sigma, rel=1e-12)


def test_active_risk_and_predicted_beta(snap):
    h_b = market_portfolio(snap)
    r_b = portfolio_risk(snap, h_b, h_b)
    assert r_b.sigma == 0.0 and r_b.beta == pytest.approx(1.0)                      # benchmark vs itself
    tilt = h_b.copy()
    tilt[:10] += 0.01
    tilt /= tilt.sum()
    r = portfolio_risk(snap, tilt, h_b)
    a = tilt - h_b
    assert r.active and r.sigma == pytest.approx(np.sqrt(a @ snap.asset_cov() @ a), rel=1e-12)


def test_holdings_from_tickers(snap):
    frame = pl.DataFrame({"ticker": ["T0", "T1", "NOPE"], "weight": [0.6, 0.4, 0.0], "bench_weight": [0.5, 0.5, 0.0]})
    h, h_b, unmatched = holdings_vectors(snap, frame)
    assert h[0] == 0.6 and h_b is not None and h_b[1] == 0.5 and unmatched == ["NOPE"]


def test_snapshot_npz_round_trip(snap, tmp_path: Path):
    path = tmp_path / "snap.npz"
    snap.save(path)
    assert RiskModelSnapshot.load(path).same_as(snap)
