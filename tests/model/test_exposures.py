"""§6.3 construction on a synthetic cross-section with gaps, data errors and fat tails."""

from pathlib import Path

import numpy as np
import pytest

from eqrisk.config import load_config
from eqrisk.kernels import standardize
from eqrisk.kernels.descriptors import weighted_corr
from eqrisk.model.descriptors import DESCRIPTORS
from eqrisk.model.exposures import STYLES, cross_section

CFG = load_config(Path(__file__).resolve().parents[2] / "configs" / "model_us_lc.yaml")


@pytest.fixture(scope="module")
def xs():
    rng = np.random.default_rng(3)
    N, n_ind = 600, 8
    mcap = np.exp(rng.normal(23, 1.3, N))
    cov = np.ones(N, bool)
    estu = cov.copy()
    estu[:20] = False                                    # coverage-only names
    capw = np.where(estu, mcap, 0) / mcap[estu].sum()
    industry = rng.integers(0, n_ind, N)
    desc = {name: rng.standard_t(4, N) for name in DESCRIPTORS}
    desc["LNCAP"] = np.log(mcap)
    desc["HBETA"] = 1 + 0.3 * rng.normal(size=N)
    desc["DASTD"] = 0.02 + 0.5 * 0.01 * desc["HBETA"] + 0.003 * rng.normal(size=N)   # correlated with beta
    desc["BTOP"][5] = 1e6                                # a data error
    for name in ("EGRO", "SGRO"):
        desc[name][rng.random(N) < 0.3] = np.nan         # growth often missing
    desc["HBETA"][rng.random(N) < 0.05] = np.nan         # new listings
    for name in ("ETOP", "CETOP"):
        desc[name][40:45] = np.nan                        # a style with every descriptor missing
    return cross_section(desc, cov, estu, capw, mcap, industry, n_ind, CFG), estu, capw, mcap, desc


def test_final_moments_every_style(xs):
    cs, estu, capw, _, _ = xs
    for s in STYLES:
        x = cs.X[s]
        assert np.isfinite(x).all(), s
        assert abs(np.average(x[estu], weights=capw[estu])) < 1e-10, s
        assert abs(np.std(x[estu]) - 1) < 1e-10, s


def test_orthogonality_is_exact_after_imputation(xs):
    cs, estu, _, mcap, _ = xs
    w = np.sqrt(mcap)
    for style, target in (("RESIDUAL_VOLATILITY", "BETA"), ("NONLINEAR_BETA", "BETA"), ("NONLINEAR_SIZE", "SIZE")):
        assert abs(weighted_corr(cs.X[style], cs.X[target], w, estu)) < 1e-8, style


def test_data_errors_are_dropped_and_counted(xs):
    cs, *_ = xs
    assert cs.errors["BTOP"] >= 1
    assert cs.imputed["BOOK_TO_PRICE"][5]                 # its only descriptor was the error


def test_imputation_flags_and_partial_descriptors(xs):
    cs, _, _, _, desc = xs
    assert cs.imputed["EARNINGS_YIELD"][40:45].all() and not cs.imputed["EARNINGS_YIELD"][50]
    one_missing = np.isnan(desc["EGRO"]) & np.isfinite(desc["SGRO"])
    assert not cs.imputed["GROWTH"][one_missing].any()    # weights renormalize over what is there
    assert cs.imputed["BETA"][np.isnan(desc["HBETA"])].all()


def test_size_is_not_clipped(xs):
    # With no clip and a single descriptor, SIZE is exactly the standardized log cap; the small
    # names sit far below the cap-weighted mean and would be trimmed otherwise.
    cs, estu, capw, mcap, _ = xs
    expected = standardize(np.log(mcap), capw, estu)
    assert np.allclose(cs.X["SIZE"], expected, atol=1e-12)
    assert cs.X["SIZE"][estu].min() < -3.0
