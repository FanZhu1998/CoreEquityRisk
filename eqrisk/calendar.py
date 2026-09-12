"""XNYS session calendar (blueprint §3.3) via exchange_calendars.

Only sessions exist: weekends, holidays and ad-hoc closures (e.g. 2025-01-09) never produce rows.
"""

from __future__ import annotations

import bisect
from datetime import date
from functools import lru_cache

import exchange_calendars as xcals

# Earliest session the calendar is built from; comfortably before any price history we pull.
_CALENDAR_START = "1990-01-02"


class TradingCalendar:
    def __init__(self, name: str) -> None:
        self.name = name
        cal = xcals.get_calendar(name, start=_CALENDAR_START)
        self._sessions: list[date] = [ts.date() for ts in cal.sessions]
        self._pos: dict[date, int] = {d: i for i, d in enumerate(self._sessions)}

    @property
    def first(self) -> date:
        return self._sessions[0]

    @property
    def last(self) -> date:
        """Last session the calendar knows (roughly one year ahead of today)."""
        return self._sessions[-1]

    def is_session(self, d: date) -> bool:
        return d in self._pos

    def sessions(self, start: date, end: date) -> list[date]:
        """Sessions in the closed interval [start, end]."""
        lo = bisect.bisect_left(self._sessions, start)
        hi = bisect.bisect_right(self._sessions, end)
        return self._sessions[lo:hi]

    def next_session(self, d: date) -> date:
        """First session strictly after `d` (d need not be a session)."""
        i = bisect.bisect_right(self._sessions, d)
        if i >= len(self._sessions):
            raise ValueError(f"no session after {d} in the {self.name} calendar")
        return self._sessions[i]

    def prev_session(self, d: date) -> date:
        """Last session strictly before `d`."""
        i = bisect.bisect_left(self._sessions, d) - 1
        if i < 0:
            raise ValueError(f"no session before {d} in the {self.name} calendar")
        return self._sessions[i]

    def session_on_or_before(self, d: date) -> date:
        return d if d in self._pos else self.prev_session(d)

    def offset(self, d: date, n: int) -> date:
        """The session `n` sessions after session `d` (negative n moves back)."""
        if d not in self._pos:
            raise ValueError(f"{d} is not a {self.name} session")
        i = self._pos[d] + n
        if not 0 <= i < len(self._sessions):
            raise ValueError(f"offset {n} from {d} leaves the calendar")
        return self._sessions[i]

    def count(self, start: date, end: date) -> int:
        return len(self.sessions(start, end))


@lru_cache(maxsize=4)
def get_calendar(name: str = "XNYS") -> TradingCalendar:
    return TradingCalendar(name)
