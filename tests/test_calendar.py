from datetime import date

import pytest

from eqrisk.calendar import get_calendar

cal = get_calendar("XNYS")


def test_ad_hoc_closure_2025_01_09():
    assert not cal.is_session(date(2025, 1, 9))
    assert cal.next_session(date(2025, 1, 8)) == date(2025, 1, 10)


def test_juneteenth_from_2022_only():
    assert not cal.is_session(date(2022, 6, 20))      # observed Monday (June 19 was a Sunday)
    assert not cal.is_session(date(2023, 6, 19))
    assert cal.is_session(date(2021, 6, 18))          # not yet an NYSE holiday


def test_next_and_prev_are_strict():
    fri, mon = date(2024, 6, 7), date(2024, 6, 10)
    assert cal.next_session(date(2024, 6, 8)) == mon
    assert cal.next_session(fri) == mon
    assert cal.prev_session(mon) == fri
    assert cal.session_on_or_before(date(2024, 6, 9)) == fri


def test_sessions_closed_interval_and_offset():
    s = cal.sessions(date(2024, 1, 2), date(2024, 1, 5))
    assert s == [date(2024, 1, d) for d in (2, 3, 4, 5)]
    assert cal.offset(date(2024, 1, 2), 3) == date(2024, 1, 5)
    assert cal.offset(date(2024, 1, 5), -3) == date(2024, 1, 2)
    with pytest.raises(ValueError):
        cal.offset(date(2024, 1, 6), 1)
