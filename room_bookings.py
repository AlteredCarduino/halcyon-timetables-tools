"""Ad-hoc room bookings for /timetabled-rooms (Supabase halcyon_room_bookings)."""

from __future__ import annotations

import os
import re
import uuid
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple

BOOKING_MINUTES = 15
TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
TIME_EXTRACT_RE = re.compile(r"(\d{1,2}):(\d{2})")

# None = unknown, True/False = probed against live schema (011 migration optional).
_period_order_column: Optional[bool] = None
_series_columns: Optional[bool] = None


def _is_period_order_missing_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "period_order" in text or "pgrst204" in text


def _is_series_columns_missing_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "series_id" in text or "staff_email" in text or "pgrst204" in text


def _booking_select_fields(include_period_order: bool, include_series: bool = True) -> str:
    fields = [
        "id",
        "room",
        "day_date",
        "start_time",
        "end_time",
        "title",
        "staff_name",
        "created_at",
    ]
    if include_period_order:
        fields.insert(5, "period_order")
    if include_series:
        fields.extend(["series_id", "staff_email"])
    return ",".join(fields)


def school_key() -> str:
    return os.environ.get("REGISTRATION_SCHOOL_KEY", "halcyon").strip() or "halcyon"


def bookings_table() -> str:
    return os.environ.get("SUPABASE_ROOM_BOOKINGS_TABLE", "halcyon_room_bookings").strip()


def allowed_staff_emails() -> Optional[set[str]]:
    raw = os.environ.get("ROOM_BOOKINGS_ALLOWED_EMAILS", "").strip()
    if not raw:
        return None
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def normalize_staff_email(value: str) -> str:
    email = str(value or "").strip().lower()
    if not email or "@" not in email:
        raise ValueError("Valid school email is required")
    allowed = allowed_staff_emails()
    if allowed is not None and email not in allowed:
        raise ValueError("Your email is not authorised to book rooms")
    return email


def parse_hhmm(value: str) -> time:
    match = TIME_RE.match(str(value).strip())
    if not match:
        extracted = TIME_EXTRACT_RE.search(str(value).strip())
        if not extracted:
            raise ValueError(f"Invalid time {value!r} (use HH:MM)")
        hour, minute = int(extracted.group(1)), int(extracted.group(2))
    else:
        hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise ValueError(f"Invalid time {value!r}")
    return time(hour, minute)


def normalize_time_value(value: Any) -> str:
    match = TIME_EXTRACT_RE.search(str(value or ""))
    if not match:
        return ""
    hour, minute = int(match.group(1)), int(match.group(2))
    return f"{hour:02d}:{minute:02d}"


def time_to_str(value: time) -> str:
    return value.strftime("%H:%M")


def minutes_between(start: time, end: time) -> int:
    dt_start = datetime.combine(date.min, start)
    dt_end = datetime.combine(date.min, end)
    return int((dt_end - dt_start).total_seconds() // 60)


def validate_quarter_hour(value: time, label: str) -> None:
    if value.minute % BOOKING_MINUTES != 0:
        raise ValueError(f"{label} must be on the hour or quarter past (:00, :15, :30, :45)")


def overlaps(start_a: time, end_a: time, start_b: time, end_b: time) -> bool:
    return start_a < end_b and start_b < end_a


def skip_reason(message: str) -> str:
    lower = str(message or "").lower()
    if "school term" in lower or "weekdays" in lower:
        return "holiday"
    if "timetabled lesson" in lower or "staff meeting" in lower:
        return "timetable"
    if "blocked" in lower:
        return "blocked"
    if "already booked" in lower:
        return "booking"
    return "other"


def summarize_skips(skipped: List[Dict[str, str]]) -> Dict[str, int]:
    summary = {"timetable": 0, "booking": 0, "blocked": 0, "holiday": 0, "other": 0}
    for item in skipped:
        reason = item.get("reason") or skip_reason(str(item.get("error") or ""))
        if reason not in summary:
            reason = "other"
        summary[reason] += 1
    return summary


def normalize_booking_row(row: Dict[str, Any]) -> Dict[str, str]:
    start = normalize_time_value(row.get("start_time"))
    end = normalize_time_value(row.get("end_time"))
    period_order = row.get("period_order")
    return {
        "id": str(row.get("id") or ""),
        "room": str(row.get("room") or ""),
        "day_date": str(row.get("day_date") or ""),
        "start_time": start,
        "end_time": end,
        "period_order": "" if period_order is None else str(period_order),
        "title": str(row.get("title") or ""),
        "staff_name": str(row.get("staff_name") or ""),
        "staff_email": str(row.get("staff_email") or ""),
        "series_id": "" if row.get("series_id") is None else str(row.get("series_id")),
        "created_at": str(row.get("created_at") or ""),
    }


def list_bookings(
    sb: Any,
    *,
    day_from: str,
    day_to: str,
    room: str = "",
    school_days_only: bool = False,
) -> List[Dict[str, str]]:
    global _period_order_column, _series_columns
    sk = school_key()
    table = bookings_table()
    include_period = _period_order_column is not False
    include_series = _series_columns is not False
    try:
        query = (
            sb.table(table)
            .select(_booking_select_fields(include_period, include_series))
            .eq("school_key", sk)
            .gte("day_date", day_from)
            .lte("day_date", day_to)
        )
        if room.strip():
            query = query.eq("room", room.strip())
        res = query.order("day_date").order("start_time").execute()
        if include_period:
            _period_order_column = True
        if include_series:
            _series_columns = True
    except Exception as exc:
        if include_period and _is_period_order_missing_error(exc):
            _period_order_column = False
            return list_bookings(sb, day_from=day_from, day_to=day_to, room=room)
        if include_series and _is_series_columns_missing_error(exc):
            _series_columns = False
            return list_bookings(sb, day_from=day_from, day_to=day_to, room=room)
        raise
    rows = [normalize_booking_row(row) for row in (res.data or [])]
    if not school_days_only:
        return rows
    from school_calendar_store import is_bookable_date

    filtered: List[Dict[str, str]] = []
    for row in rows:
        try:
            day = date.fromisoformat(str(row.get("day_date") or ""))
        except ValueError:
            continue
        if is_bookable_date(day):
            filtered.append(row)
    return filtered


def _fetch_day_bookings(sb: Any, day_date: str, room: str) -> List[Dict[str, Any]]:
    sk = school_key()
    table = bookings_table()
    res = (
        sb.table(table)
        .select("id,start_time,end_time")
        .eq("school_key", sk)
        .eq("day_date", day_date)
        .eq("room", room)
        .execute()
    )
    return res.data or []


def create_booking(
    sb: Any,
    *,
    room: str,
    day_date: str,
    start_time: str,
    end_time: str,
    title: str,
    staff_name: str,
    staff_email: str = "",
    series_id: Optional[str] = None,
    period_order: Optional[int] = None,
    period_start: str = "",
    period_end: str = "",
) -> Dict[str, str]:
    room = room.strip()
    title = title.strip()
    staff_name = staff_name.strip()
    normalized_email = normalize_staff_email(staff_email) if staff_email.strip() else ""
    if not room:
        raise ValueError("Room is required")
    if not title:
        raise ValueError("Title / subject is required")
    if not staff_name:
        raise ValueError("Staff name is required")
    if not normalized_email:
        raise ValueError("School email is required")

    from school_calendar_store import validate_booking_date_str

    validate_booking_date_str(day_date)

    start = parse_hhmm(start_time)
    end = parse_hhmm(end_time)
    validate_quarter_hour(start, "Start time")
    validate_quarter_hour(end, "End time")
    if end <= start:
        raise ValueError("End time must be after start time")
    if minutes_between(start, end) < BOOKING_MINUTES:
        raise ValueError(f"Booking must be at least {BOOKING_MINUTES} minutes")

    if period_start and period_end:
        ps = parse_hhmm(period_start)
        pe = parse_hhmm(period_end)
        if start < ps or end > pe:
            raise ValueError(
                f"Booking must fall within {time_to_str(ps)}–{time_to_str(pe)}"
            )

    from timetabled_rooms_occupancy import check_timetable_conflict

    timetable_conflict = check_timetable_conflict(room, day_date, period_order)
    if timetable_conflict:
        raise ValueError(timetable_conflict)

    for existing in _fetch_day_bookings(sb, day_date, room):
        ex_start = parse_hhmm(str(existing.get("start_time") or "")[:5])
        ex_end = parse_hhmm(str(existing.get("end_time") or "")[:5])
        if overlaps(start, end, ex_start, ex_end):
            raise ValueError(
                f"Room {room} is already booked {time_to_str(ex_start)}–{time_to_str(ex_end)} on {day_date}"
            )

    from room_blocks import is_room_blocked

    if is_room_blocked(
        sb,
        room=room,
        day_date=day_date,
        start_time=time_to_str(start),
        end_time=time_to_str(end),
    ):
        raise ValueError(f"Room {room} is blocked for all or part of that time on {day_date}")

    from room_booking_access import assert_room_booking_access

    assert_room_booking_access(sb, room, normalized_email)

    sk = school_key()
    table = bookings_table()
    global _period_order_column, _series_columns
    row = {
        "school_key": sk,
        "room": room,
        "day_date": day_date,
        "start_time": time_to_str(start),
        "end_time": time_to_str(end),
        "title": title,
        "staff_name": staff_name,
    }
    include_period = _period_order_column is not False
    include_series = _series_columns is not False
    if include_period and period_order is not None:
        row["period_order"] = period_order
    if include_series:
        row["staff_email"] = normalized_email
        if series_id:
            row["series_id"] = series_id
    try:
        res = sb.table(table).insert(row).execute()
        if include_period and period_order is not None:
            _period_order_column = True
        if include_series:
            _series_columns = True
    except Exception as exc:
        if include_period and _is_period_order_missing_error(exc):
            _period_order_column = False
            row.pop("period_order", None)
            res = sb.table(table).insert(row).execute()
        elif include_series and _is_series_columns_missing_error(exc):
            _series_columns = False
            row.pop("staff_email", None)
            row.pop("series_id", None)
            res = sb.table(table).insert(row).execute()
        else:
            raise
    rows = res.data or []
    if not rows:
        raise RuntimeError("Booking insert failed")
    return normalize_booking_row(rows[0])


def dates_same_weekday(from_day: date, to_day: date) -> List[date]:
    """Every occurrence of from_day's weekday from from_day through to_day."""
    if to_day < from_day:
        return []
    out: List[date] = []
    current = from_day
    while current <= to_day:
        out.append(current)
        current += timedelta(days=7)
    return out


def create_recurring_bookings(
    sb: Any,
    *,
    room: str,
    day_date: str,
    start_time: str,
    end_time: str,
    title: str,
    staff_name: str,
    repeat: str,
    repeat_end_date: str = "",
    staff_email: str = "",
    period_order: Optional[int] = None,
    period_start: str = "",
    period_end: str = "",
) -> Dict[str, Any]:
    """Repeat weekly on the same weekday until academic year end or a custom end date."""
    from timetabled_rooms_config import academic_year_bounds

    mode = str(repeat or "none").strip().lower()
    if mode in ("", "none", "single"):
        booking = create_booking(
            sb,
            room=room,
            day_date=day_date,
            start_time=start_time,
            end_time=end_time,
            title=title,
            staff_name=staff_name,
            staff_email=staff_email,
            period_order=period_order,
            period_start=period_start,
            period_end=period_end,
        )
        return {"bookings": [booking], "count": 1, "skipped": [], "skip_summary": summarize_skips([])}

    series_id = str(uuid.uuid4())

    start_day = date.fromisoformat(str(day_date).strip())
    if mode in ("academic_year", "year", "rest_of_year"):
        _, end_day = academic_year_bounds(start_day)
    elif mode in ("custom", "range"):
        if not repeat_end_date.strip():
            raise ValueError("Repeat end date is required for a custom range")
        end_day = date.fromisoformat(repeat_end_date.strip())
    else:
        raise ValueError(f"Unknown repeat mode {repeat!r}")

    if end_day < start_day:
        raise ValueError("Repeat end date must be on or after the first booking date")

    created: List[Dict[str, str]] = []
    skipped: List[Dict[str, str]] = []
    for booking_day in dates_same_weekday(start_day, end_day):
        try:
            created.append(
                create_booking(
                    sb,
                    room=room,
                    day_date=booking_day.isoformat(),
                    start_time=start_time,
                    end_time=end_time,
                    title=title,
                    staff_name=staff_name,
                    staff_email=staff_email,
                    series_id=series_id,
                    period_order=period_order,
                    period_start=period_start,
                    period_end=period_end,
                )
            )
        except ValueError as exc:
            message = str(exc)
            skipped.append(
                {
                    "day_date": booking_day.isoformat(),
                    "error": message,
                    "reason": skip_reason(message),
                }
            )

    if not created:
        detail = skipped[0]["error"] if skipped else "No bookings could be created"
        raise ValueError(detail)

    return {
        "bookings": created,
        "count": len(created),
        "skipped": skipped,
        "skip_summary": summarize_skips(skipped),
    }


def prune_bookings_conflicting_with_timetable(
    sb: Any,
    *,
    day_from: str = "",
    day_to: str = "",
) -> Dict[str, Any]:
    """Remove ad-hoc bookings that now clash with the latest iSAMS weekly timetable."""
    from timetabled_rooms_config import academic_year_bounds
    from timetabled_rooms_occupancy import check_timetable_conflict, load_weekly_occupancy

    load_weekly_occupancy(force_reload=True)
    if day_from and day_to:
        start_day = date.fromisoformat(day_from)
        end_day = date.fromisoformat(day_to)
    else:
        start_day, end_day = academic_year_bounds()

    removed: List[Dict[str, str]] = []
    for booking in list_bookings(
        sb, day_from=start_day.isoformat(), day_to=end_day.isoformat()
    ):
        raw_order = booking.get("period_order")
        if raw_order in (None, ""):
            continue
        conflict = check_timetable_conflict(
            booking["room"],
            booking["day_date"],
            int(raw_order),
        )
        if not conflict:
            continue
        delete_booking(sb, booking["id"])
        removed.append(
            {
                "id": booking["id"],
                "day_date": booking["day_date"],
                "room": booking["room"],
                "title": booking.get("title") or "",
                "reason": conflict,
            }
        )

    return {"removed": len(removed), "items": removed}


def prune_after_timetable_sync() -> Dict[str, Any]:
    """Run after iSAMS timetabled-rooms sync to drop bookings that now clash with lessons."""
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        return {"removed": 0, "items": [], "skipped": True}
    from supabase import create_client

    sb = create_client(url, key)
    return prune_bookings_conflicting_with_timetable(sb)


def delete_booking(sb: Any, booking_id: str) -> None:
    sk = school_key()
    table = bookings_table()
    sb.table(table).delete().eq("school_key", sk).eq("id", booking_id).execute()


def _fetch_booking(sb: Any, booking_id: str) -> Optional[Dict[str, Any]]:
    global _period_order_column, _series_columns
    sk = school_key()
    table = bookings_table()
    include_period = _period_order_column is not False
    include_series = _series_columns is not False
    try:
        res = (
            sb.table(table)
            .select(_booking_select_fields(include_period, include_series))
            .eq("school_key", sk)
            .eq("id", booking_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        if include_period and _is_period_order_missing_error(exc):
            _period_order_column = False
            return _fetch_booking(sb, booking_id)
        if include_series and _is_series_columns_missing_error(exc):
            _series_columns = False
            return _fetch_booking(sb, booking_id)
        raise
    rows = res.data or []
    return rows[0] if rows else None


def verify_booking_owner(
    booking: Dict[str, Any],
    *,
    staff_email: str,
    staff_name: str = "",
) -> None:
    stored_email = str(booking.get("staff_email") or "").strip().lower()
    request_email = str(staff_email or "").strip().lower()
    if stored_email:
        if request_email != stored_email:
            raise ValueError("You can only delete your own bookings")
        return
    stored_name = str(booking.get("staff_name") or "").strip().lower()
    request_name = str(staff_name or "").strip().lower()
    if stored_name and request_name != stored_name:
        raise ValueError("Staff name must match the booking to delete it")


def delete_booking_for_user(
    sb: Any,
    booking_id: str,
    *,
    staff_email: str,
    staff_name: str = "",
    scope: str = "single",
) -> int:
    booking = _fetch_booking(sb, booking_id)
    if not booking:
        raise ValueError("Booking not found")
    verify_booking_owner(booking, staff_email=staff_email, staff_name=staff_name)

    delete_mode = str(scope or "single").strip().lower()
    series_id = booking.get("series_id")
    if delete_mode in ("series", "all") and series_id:
        sk = school_key()
        table = bookings_table()
        res = (
            sb.table(table)
            .select("id,staff_email,staff_name")
            .eq("school_key", sk)
            .eq("series_id", series_id)
            .execute()
        )
        rows = res.data or []
        for row in rows:
            verify_booking_owner(row, staff_email=staff_email, staff_name=staff_name)
            delete_booking(sb, str(row.get("id") or ""))
        return len(rows)

    delete_booking(sb, booking_id)
    return 1


def delete_bookings_for_room_day(sb: Any, *, room: str, day_date: str) -> int:
    sk = school_key()
    table = bookings_table()
    res = (
        sb.table(table)
        .select("id")
        .eq("school_key", sk)
        .eq("room", room.strip())
        .eq("day_date", day_date)
        .execute()
    )
    rows = res.data or []
    for row in rows:
        delete_booking(sb, str(row.get("id") or ""))
    return len(rows)


def create_booking_admin(
    sb: Any,
    *,
    room: str,
    day_date: str,
    start_time: str,
    end_time: str,
    title: str,
    staff_name: str,
    allow_overlap_replace: bool = False,
) -> Dict[str, str]:
    """Admin booking: any HH:MM times, optional replace of overlapping slots."""
    room = room.strip()
    title = title.strip()
    staff_name = staff_name.strip()
    if not room:
        raise ValueError("Room is required")
    if not title:
        raise ValueError("Title / subject is required")
    if not staff_name:
        raise ValueError("Staff name is required")

    start = parse_hhmm(start_time)
    end = parse_hhmm(end_time)
    if end <= start:
        raise ValueError("End time must be after start time")

    from room_blocks import is_room_blocked

    if is_room_blocked(
        sb,
        room=room,
        day_date=day_date,
        start_time=time_to_str(start),
        end_time=time_to_str(end),
    ):
        raise ValueError(f"Room {room} is blocked for all or part of that time on {day_date}")

    if allow_overlap_replace:
        to_remove: List[str] = []
        for existing in _fetch_day_bookings(sb, day_date, room):
            ex_start = parse_hhmm(str(existing.get("start_time") or "")[:5])
            ex_end = parse_hhmm(str(existing.get("end_time") or "")[:5])
            if overlaps(start, end, ex_start, ex_end):
                to_remove.append(str(existing.get("id") or ""))
        for booking_id in to_remove:
            if booking_id:
                delete_booking(sb, booking_id)
    else:
        for existing in _fetch_day_bookings(sb, day_date, room):
            ex_start = parse_hhmm(str(existing.get("start_time") or "")[:5])
            ex_end = parse_hhmm(str(existing.get("end_time") or "")[:5])
            if overlaps(start, end, ex_start, ex_end):
                raise ValueError(
                    f"Room {room} is already booked {time_to_str(ex_start)}–{time_to_str(ex_end)} on {day_date}"
                )

    sk = school_key()
    table = bookings_table()
    global _period_order_column
    row = {
        "school_key": sk,
        "room": room,
        "day_date": day_date,
        "start_time": time_to_str(start),
        "end_time": time_to_str(end),
        "title": title,
        "staff_name": staff_name,
    }
    include_period = _period_order_column is not False
    try:
        res = sb.table(table).insert(row).execute()
    except Exception as exc:
        if include_period and _is_period_order_missing_error(exc):
            _period_order_column = False
            res = sb.table(table).insert(row).execute()
        else:
            raise
    rows = res.data or []
    if not rows:
        raise RuntimeError("Booking insert failed")
    return normalize_booking_row(rows[0])


def create_all_day_booking(
    sb: Any,
    *,
    room: str,
    day_date: str,
    title: str,
    staff_name: str,
    start_time: str = "",
    end_time: str = "",
    replace_existing: bool = True,
) -> Dict[str, str]:
    from timetabled_rooms_config import SCHOOL_DAY_END, SCHOOL_DAY_START

    return create_booking_admin(
        sb,
        room=room,
        day_date=day_date,
        start_time=start_time or SCHOOL_DAY_START,
        end_time=end_time or SCHOOL_DAY_END,
        title=title,
        staff_name=staff_name,
        allow_overlap_replace=replace_existing,
    )
