"""Typed reads of single values out of polars frames.

`Series.max()`, `.min()`, `.mean()` and `DataFrame.item()` are typed as "any Python scalar"
(int | float | Decimal | date | str | bytes | ... | None), because polars cannot know the column's
dtype statically. Reports and manifests need concrete numbers, so every such read goes through one
of these, which says what the caller expects and fails loudly on the value it cannot convert.
"""

from __future__ import annotations

from typing import Any


def as_float(value: Any, default: float | None = None) -> float:
    """A numeric cell as a float. `default` covers an empty column (max() of no rows is None)."""
    if value is None:
        if default is None:
            raise ValueError("expected a number, got an empty result")
        return default
    return float(value)


def as_int(value: Any, default: int | None = None) -> int:
    if value is None:
        if default is None:
            raise ValueError("expected an integer, got an empty result")
        return default
    return int(value)


def as_str(value: Any, default: str = "—") -> str:
    """A cell as text (dates included), for log lines and report prose."""
    return default if value is None else str(value)
