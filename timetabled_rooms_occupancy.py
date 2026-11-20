"""Weekly iSAMS timetable occupancy for room booking validation."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent
_STATIC = _ROOT / "static" / "timetabled_rooms"
_WEEKLY_JSON = _STATIC / "timetabled_rooms_weekly.json"
_REMOTE_NAME = "timetabled_rooms_weekly.json"

_cache: Optional[Dict[str, Any]] = None
_occupied_index: Optional[Dict[Tuple[int, int, str], Dict[str, str]]] = None


def invalidate_cache() -> None:
    global _cache, _occupied_index
    _cache = None
    _occupied_index = None


def _bucket() -> str:
    return os.environ.get("SUPABASE_TIMETABLED_ROOMS_BUCKET", "timetabled-rooms").strip()


def _load_from_storage() -> Optional[Dict[str, Any]]:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        return None
    try:
        from supabase_storage_http import download_storage_object

        raw = download_storage_object(_bucket(), _REMOTE_NAME)
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def load_weekly_occupancy(*, force_reload: bool = False) -> Dict[str, Any]:
    global _cache
    if _cache is not None and not force_reload:
        return _cache
    if _WEEKLY_JSON.is_file():
        _cache = json.loads(_WEEKLY_JSON.read_text(encoding="utf-8"))
        return _cache
    remote = _load_from_storage()
    if remote is not None:
        _cache = remote
        return _cache
    _cache = {"occupied": [], "periods": []}
    return _cache


def _occupied_index_map(data: Dict[str, Any]) -> Dict[Tuple[int, int, str], Dict[str, str]]:
    global _occupied_index
    if _occupied_index is not None and data is _cache:
        return _occupied_index
    index: Dict[Tuple[int, int, str], Dict[str, str]] = {}
    for entry in data.get("occupied") or []:
        day_order = int(entry.get("dayOrder") or 0)
        period_order = int(entry.get("periodOrder") or 0)
        room = str(entry.get("room") or "").strip()
        if not day_order or not period_order or not room:
            continue
        key = (day_order, period_order, room)
        if key not in index:
            index[key] = {
                "subject": str(entry.get("subject") or ""),
                "type": str(entry.get("type") or "lesson"),
            }
    if data is _cache:
        _occupied_index = index
    return index


def day_order_from_date(day_date: str) -> int:
    """iSAMS day order: Monday=1 … Sunday=7."""
    return date.fromisoformat(str(day_date).strip()).weekday() + 1


def check_timetable_conflict(
    room: str,
    day_date: str,
    period_order: Optional[int],
) -> Optional[str]:
    """Return an error message when iSAMS occupies this room/period, else None."""
    if period_order is None:
        return None
    data = load_weekly_occupancy()
    index = _occupied_index_map(data)
    day_order = day_order_from_date(day_date)
    key = (day_order, int(period_order), room.strip())
    hit = index.get(key)
    if not hit:
        return None
    label = "Staff meeting" if hit.get("type") == "staff" else "Timetabled lesson"
    subject = hit.get("subject") or "occupied"
    return f"{label} ({subject}) in {room} on {day_date}"


def write_weekly_occupancy_json(
    *,
    weekly_slots: Dict[Any, Any],
    period_rows: List[Dict[str, Any]],
    timetable_label: str,
    generated_at: str,
) -> Path:
    occupied: List[Dict[str, Any]] = []
    for (day_order, period_order), rooms_dict in weekly_slots.items():
        for room, events in rooms_dict.items():
            for event in events:
                occupied.append(
                    {
                        "dayOrder": int(day_order),
                        "periodOrder": int(period_order),
                        "room": str(room),
                        "subject": str(event.get("subject") or ""),
                        "teacher": str(event.get("teacher") or ""),
                        "studentCount": event.get("studentCount"),
                        "type": str(event.get("type") or "lesson"),
                    }
                )
    payload = {
        "timetableLabel": timetable_label,
        "generatedAt": generated_at,
        "periods": [
            {
                "order": period["order"],
                "name": period["name"],
                "start": period["start"],
                "end": period["end"],
            }
            for period in period_rows
        ],
        "occupied": occupied,
    }
    _WEEKLY_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    invalidate_cache()
    return _WEEKLY_JSON


def list_school_periods() -> List[Dict[str, str]]:
    """Timetable periods from the latest iSAMS weekly export."""
    data = load_weekly_occupancy()
    periods: List[Dict[str, str]] = []
    for row in data.get("periods") or []:
        order = int(row.get("order") or 0)
        if not order:
            continue
        periods.append(
            {
                "order": str(order),
                "name": str(row.get("name") or f"Period {order}"),
                "start": str(row.get("start") or ""),
                "end": str(row.get("end") or ""),
            }
        )
    return periods


def periods_by_order() -> Dict[int, Dict[str, str]]:
    out: Dict[int, Dict[str, str]] = {}
    for row in list_school_periods():
        out[int(row["order"])] = row
    return out
