"""`pending_sessions` (§13.2): --force re-runs a session, but never bootstraps an empty model.

A forced run used to return `[through]` before the "no processed sessions yet" check, so the app's
Re-run button could start a daily run against a model with no history. That run ingested and staged
for minutes before dying in the factor covariance, which reads the whole `factor_returns` table.
"""

from datetime import date
from types import SimpleNamespace

import pytest

from eqrisk.calendar import get_calendar
from eqrisk.pipeline import daily

CAL = "XNYS"
THROUGH = date(2026, 9, 18)


def _project():
    return SimpleNamespace(config=SimpleNamespace(calendar=CAL))


def _processed(monkeypatch, days):
    monkeypatch.setattr(daily, "processed_dates", lambda project: set(days))


def test_force_on_an_empty_model_refuses_instead_of_bootstrapping(monkeypatch):
    _processed(monkeypatch, [])
    with pytest.raises(LookupError, match="backfill"):
        daily.pending_sessions(_project(), THROUGH, force=True)


def test_catch_up_on_an_empty_model_refuses(monkeypatch):
    _processed(monkeypatch, [])
    with pytest.raises(LookupError, match="backfill"):
        daily.pending_sessions(_project(), THROUGH, force=False)


def test_force_re_runs_one_already_processed_session(monkeypatch):
    _processed(monkeypatch, [THROUGH])
    assert daily.pending_sessions(_project(), THROUGH, force=True) == [THROUGH]


def test_catch_up_returns_the_gap_after_the_last_processed_session(monkeypatch):
    cal = get_calendar(CAL)
    last = cal.offset(THROUGH, -3)
    _processed(monkeypatch, [last])
    expected = cal.sessions(cal.next_session(last), THROUGH)
    assert daily.pending_sessions(_project(), THROUGH, force=False) == expected
    assert len(expected) == 3


def test_a_model_already_through_today_has_nothing_pending(monkeypatch):
    _processed(monkeypatch, [THROUGH])
    assert daily.pending_sessions(_project(), THROUGH, force=False) == []
