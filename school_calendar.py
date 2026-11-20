"""School term dates from iSAMS REST (for timetabled rooms and similar)."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional


def fetch_school_terms() -> List[Dict[str, Any]]:
    """Return iSAMS term list, or [] when OAuth is unavailable."""
    try:
        from isams_registration import isams_get_token, load_isams_config_from_env
        from isams_registration_attendance_rest import get_school_terms

        cfg = load_isams_config_from_env()
        token = isams_get_token(cfg)
        return get_school_terms(cfg, token)
    except Exception:
        return []


def _parse_term_date(value: Any) -> Optional[date]:
    try:
        from isams_registration_attendance_rest import _parse_api_date

        return _parse_api_date(value)
    except Exception:
        return None


def term_label_for_day(day: date, terms: List[Dict[str, Any]]) -> Optional[str]:
    for term in terms:
        start = _parse_term_date(term.get("startDate"))
        end = _parse_term_date(term.get("finishDate"))
        if start and end and start <= day <= end:
            name = str(term.get("name") or term.get("friendlyName") or "").strip()
            return name or "School term"
    return None


def non_school_day_message(day: date, terms: List[Dict[str, Any]]) -> Optional[str]:
    """
    When terms are loaded: weekdays outside all term ranges are marked closed.
    Weekends are handled by callers; returns None for in-term weekdays.
    """
    if not terms:
        return None
    if term_label_for_day(day, terms):
        return None
    return "Outside school term (iSAMS calendar)"


def non_school_day_message_from_cache(day: date) -> Optional[str]:
    """Like non_school_day_message but uses serialized school_calendar.json terms."""
    from school_calendar_store import booking_date_error

    err = booking_date_error(day)
    if not err:
        return None
    if "weekdays" in err.lower():
        return None
    return "Outside school term (iSAMS calendar)"
