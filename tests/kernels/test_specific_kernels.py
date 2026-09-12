"""The vectorized specific-risk kernels must equal the Appendix A reference column by column."""

import numpy as np
import pytest

from eqrisk.kernels import coverage_gamma, specific_ts_var
from eqrisk.kernels.specific import compact_last, coverage_gamma_panel, specific_ts_panel


def _panel(seed=0, W=420, N=25):
    rng = np.random.default_rng(seed)
    U = rng.standard_t(5, (W, N)) * 0.015
    U[rng.random((W, N)) < 0.15] = np.nan
    U[:, 3] = np.nan
    U[-40:, 4] = np.nan                       # delisted a while ago
    U[:-60, 5] = np.nan                       # new listing: 60 days of history
    e = rng.normal(0, 0.01, W + 1)
    U[:, 6] = e[1:] - e[:-1]                  # bid-ask bounce, hits the NW floor
    return U


def test_compact_last_keeps_order_and_skips_gaps():
    U = np.array([[1.0, np.nan], [np.nan, 5.0], [3.0, np.nan], [4.0, 6.0]])
    A, n = compact_last(U, 2)
    assert np.array_equal(A[:, 0], [3.0, 4.0]) and np.array_equal(A[:, 1], [5.0, 6.0])
    A3, n3 = compact_last(U, 3)
    assert np.isnan(A3[0, 1]) and list(n3) == [3, 2]


def test_specific_ts_panel_matches_reference_column_by_column():
    U = _panel()
    v0, ratio, counts = specific_ts_panel(U, 252, 84, 5, 252)
    got = v0 * np.clip(ratio, 0.25, 4.0)
    for j in range(U.shape[1]):
        x = U[:, j][np.isfinite(U[:, j])][-252:]
        if len(x) < 2:
            assert counts[j] == len(x)
            continue
        assert counts[j] == len(x)
        assert got[j] == pytest.approx(specific_ts_var(x, 84, 5, 252), rel=1e-9), j


def test_coverage_gamma_panel_matches_reference():
    U = _panel(seed=1)[-252:]
    g = coverage_gamma_panel(U, 60, 120)
    for j in range(U.shape[1]):
        assert g[j] == pytest.approx(coverage_gamma(U[:, j], 60, 120), abs=1e-12), j
