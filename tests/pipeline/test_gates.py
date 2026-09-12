"""§11.4 gates on synthetic day data: FAIL quarantines, WARN only reports."""

from pathlib import Path

import numpy as np
import polars as pl

from eqrisk.config import load_config
from eqrisk.model.exposures import FUNDAMENTAL_STYLES, STYLES
from eqrisk.pipeline.gates import FAIL, OK, QUARANTINED, WARN, DayData, gate_results, status_of

G = load_config(Path(__file__).resolve().parents[2] / "configs" / "model_us_lc.yaml").gates


def _day(**over):
    qa = pl.DataFrame({"style": list(STYLES), "mean_cw": [1e-12] * len(STYLES), "std_ew": [1.0] * len(STYLES),
                       "imputed": [3] * len(STYLES), "stability": [0.97] * len(STYLES),
                       "vif": [2.0] * len(STYLES)})
    d = dict(n_cov=503, n_priced=503, qa=qa,
             stats={"status": "ok", "n": 490, "cond": 20.0, "constraint_resid": 1e-17, "r2_w": 0.4,
                    "country_minus_mkt": 0.0005},
             factors=pl.DataFrame({"factor": ["COUNTRY", "SIZE"], "f": [0.01, 0.001], "f_over_sigma": [1.2, -0.4]}),
             F=np.diag([0.002, 0.0005]), lambda_f=1.05, lambda_s=0.98, sigma_cov=np.full(503, 0.08),
             sigma_estu=np.full(495, 0.08))
    d.update(over)
    return DayData(**d)


def _failed(day):
    return {r["gate"]: r["level"] for r in gate_results(day, G, set(FUNDAMENTAL_STYLES)) if not r["ok"]}


def test_a_clean_day_passes_every_gate():
    res = gate_results(_day(), G, set(FUNDAMENTAL_STYLES))
    assert all(r["ok"] for r in res) and status_of(res) == OK


def test_fail_gates_quarantine_the_session():
    for over, gate in ((dict(n_priced=480), "data_freshness"),
                       (dict(stats={"status": "too_few_names", "n": 0}), "regression"),
                       (dict(F=np.array([[1.0, 2.0], [2.0, 1.0]])), "factor_covariance"),
                       (dict(sigma_cov=np.r_[np.full(502, 0.08), np.nan]), "specific_coverage")):
        res = gate_results(_day(**over), G, set(FUNDAMENTAL_STYLES))
        assert _failed(_day(**over)).get(gate) == FAIL and status_of(res) == QUARANTINED, gate


def test_warn_gates_only_report():
    qa = _day().qa.with_columns(stability=pl.when(pl.col("style") == "GROWTH").then(0.9).otherwise(0.97))
    day = _day(n_cov=512, n_priced=512, lambda_f=2.7, qa=qa,
               factors=pl.DataFrame({"factor": ["ENERGY"], "f": [0.05], "f_over_sigma": [7.5]}))
    failed = _failed(day)
    assert failed == {"universe": WARN, "vra": WARN, "factor_shock": WARN, "stability": WARN}
    assert status_of(gate_results(day, G, set(FUNDAMENTAL_STYLES))) == OK


def test_vif_warns_above_five_and_fails_above_ten():
    for vif, expect in ((6.0, {"vif_warn": WARN}), (11.0, {"vif": FAIL, "vif_warn": WARN})):
        qa = _day().qa.with_columns(vif=pl.when(pl.col("style") == "MOMENTUM").then(vif).otherwise(2.0))
        assert _failed(_day(qa=qa)) == expect
