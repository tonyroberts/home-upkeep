"""
Unit tests for the pure rescheduling/seasonal-constraint logic.

These functions were previously inline in the add-on's FastAPI backend and
had no unit tests; porting them into `logic.py` fixes that.
"""

from __future__ import annotations

from datetime import date

import pytest

from custom_components.home_upkeep.logic import (
    calculate_next_due_date,
    find_first_non_prohibited_month,
)


@pytest.mark.parametrize(
    ("base_date", "period", "expected"),
    [
        (date(2026, 1, 1), "5d", date(2026, 1, 6)),
        (date(2026, 1, 1), "2w", date(2026, 1, 15)),
        (date(2026, 1, 31), "1m", date(2026, 2, 28)),  # clamp to month length
        (date(2026, 1, 15), "1m", date(2026, 2, 15)),
        (date(2026, 12, 15), "1m", date(2027, 1, 15)),  # year rollover
    ],
)
def test_calculate_next_due_date(base_date: date, period: str, expected: date) -> None:
    """Reschedule periods (days/weeks/months) compute the expected date."""
    assert calculate_next_due_date(base_date, period) == expected


def test_calculate_next_due_date_skips_prohibited_month() -> None:
    """A rescheduled date landing in a prohibited month rolls forward."""
    result = calculate_next_due_date(date(2026, 2, 15), "1m", prohibited_months=[3])
    assert result == date(2026, 4, 1)


def test_calculate_next_due_date_allowed_month_is_unaffected() -> None:
    """A rescheduled date outside prohibited months is left untouched."""
    result = calculate_next_due_date(date(2026, 2, 15), "1m", prohibited_months=[4])
    assert result == date(2026, 3, 15)


def test_find_first_non_prohibited_month_current_month_ok() -> None:
    """Current month is allowed: roll back to its first day."""
    assert find_first_non_prohibited_month(date(2026, 3, 15), [4]) == date(2026, 3, 1)


def test_find_first_non_prohibited_month_rolls_forward() -> None:
    """The current month is prohibited: roll forward to the next allowed month."""
    assert find_first_non_prohibited_month(date(2026, 3, 15), [3, 4]) == date(
        2026, 5, 1
    )


def test_find_first_non_prohibited_month_rolls_over_year_boundary() -> None:
    """Rolling forward past December wraps into the next year."""
    assert find_first_non_prohibited_month(date(2026, 11, 1), [11, 12, 1]) == date(
        2027, 2, 1
    )


def test_find_first_non_prohibited_month_no_prohibitions() -> None:
    """With no prohibited months, the original date is returned unchanged."""
    assert find_first_non_prohibited_month(date(2026, 3, 15), []) == date(2026, 3, 15)


def test_find_first_non_prohibited_month_all_prohibited_falls_back() -> None:
    """If every month is prohibited, fall back to the original date."""
    all_months = list(range(1, 13))
    start = date(2026, 3, 15)
    assert find_first_non_prohibited_month(start, all_months) == start
