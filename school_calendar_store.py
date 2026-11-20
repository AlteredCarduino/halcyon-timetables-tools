"""Persisted school calendar: iSAMS term dates for timetabled rooms and bookings."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent
_STATIC = _ROOT / "static" / "timetabled_rooms"
CALENDAR_FILENAME = "school_calendar.json"
REMOTE_NAME = "school_calendar.json"

_cache: Optional[Dict[str, Any]] = None


def serialize_terms(terms: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    from school_calendar import _parse_term_date

    out: List[Dict[str, str]] = []
    for term in terms:
        start = _parse_term_date(term.get("startDate"))
        end = _parse_term_date(term.get("finishDate"))
        if not start or not end:
            continue
        name = str(term.get("name") or term.get("friendlyName") or "").strip()
        out.append(
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "name": name or "Term",
            }
        )
    out.sort(key=lambda item: item["start"])
    return out


def calendar_payload(terms: List[Dict[str, Any]], *, generated_at: str) -> Dict[str, Any]:
    serialized = serialize_terms(terms)
    return {
        "generatedAt": generated_at,
        "termsLoaded": bool(serialized),
        "loaded": bool(serialized),
        "terms": serialized,
        "sourceNote": "Term dates from iSAMS REST (weekdays outside terms are closed on the timetabled rooms grid).",
    }


def write_school_calendar_file(
    terms: List[Dict[str, Any]], *, generated_at: str
) -> Path:
    global _cache
    _STATIC.mkdir(parents=True, exist_ok=True)
    payload = calendar_payload(terms, generated_at=generated_at)
    path = _STATIC / CALENDAR_FILENAME
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _cache = payload
    return path


def _load_from_storage() -> Optional[Dict[str, Any]]:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    bucket = os.environ.get("SUPABASE_TIMETABLED_ROOMS_BUCKET", "timetabled-rooms").strip()
    if not url or not key:
        return None
    try:
        from supabase_storage_http import download_storage_object

        raw = download_storage_object(bucket, REMOTE_NAME)
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def load_school_calendar_data(*, force_reload: bool = False) -> Dict[str, Any]:
    global _cache
    if _cache is not None and not force_reload:
        return _cache
    path = _STATIC / CALENDAR_FILENAME
    if path.is_file():
        _cache = json.loads(path.read_text(encoding="utf-8"))
        return _cache
    remote = _load_from_storage()
    if remote is not None:
        _cache = remote
        return _cache
    _cache = {"loaded": False, "terms": [], "generatedAt": ""}
    return _cache


def invalidate_school_calendar_cache() -> None:
    global _cache
    _cache = None


def day_in_terms(day: date, terms: List[Dict[str, str]]) -> bool:
    for term in terms:
        try:
            start = date.fromisoformat(str(term.get("start") or ""))
            end = date.fromisoformat(str(term.get("end") or ""))
        except ValueError:
            continue
        if start <= day <= end:
            return True
    return False


def is_bookable_date(day: date, terms: Optional[List[Dict[str, str]]] = None) -> bool:
    if day.weekday() >= 5:
        return False
    if terms is None:
        terms = load_school_calendar_data().get("terms") or []
    if not terms:
        return True
    return day_in_terms(day, terms)


def booking_date_error(day: date, terms: Optional[List[Dict[str, str]]] = None) -> Optional[str]:
    if day.weekday() >= 5:
        return "Bookings are only available on weekdays"
    if terms is None:
        terms = load_school_calendar_data().get("terms") or []
    if not terms:
        return None
    if day_in_terms(day, terms):
        return None
    return "Outside school term (iSAMS calendar) — booking not allowed"


def validate_booking_date_str(day_date: str) -> None:
    day = date.fromisoformat(str(day_date).strip())
    err = booking_date_error(day)
    if err:
        raise ValueError(err)
