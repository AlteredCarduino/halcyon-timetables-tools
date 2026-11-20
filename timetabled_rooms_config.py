"""Shared timetabled-rooms settings (sync + booking UI)."""

from __future__ import annotations

from datetime import date

# Rooms shown on the grid even when not in iSAMS timetable slots.
EXTRA_GRID_ROOMS: list[str] = ["4.2 Garden", "2.3 (Admissions)"]

# Rooms excluded from ad-hoc booking clicks entirely.
EXCLUDED_ROOMS: list[str] = []

# Rooms visible on the grid but only bookable by admin-listed staff emails.
RESTRICTED_ROOMS: list[str] = ["2.3 (Admissions)"]

# Default school-day bounds for admin all-day bookings and blocks.
SCHOOL_DAY_START = "08:00"
SCHOOL_DAY_END = "17:00"

ACADEMIC_YEAR_START_MONTH = 8
ACADEMIC_YEAR_START_DAY = 25


def academic_year_start_year_on(day: date) -> int:
    boundary = date(day.year, ACADEMIC_YEAR_START_MONTH, ACADEMIC_YEAR_START_DAY)
    if day >= boundary:
        return day.year
    return day.year - 1


def academic_year_bounds(day: date | None = None) -> tuple[date, date]:
    """Current school year from late Aug through late Aug next year."""
    today = day or date.today()
    start_year = academic_year_start_year_on(today)
    start = date(start_year, ACADEMIC_YEAR_START_MONTH, ACADEMIC_YEAR_START_DAY)
    end = date(start_year + 1, ACADEMIC_YEAR_START_MONTH, ACADEMIC_YEAR_START_DAY - 1)
    return start, end

