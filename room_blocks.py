"""Room closure blocks — prevent ad-hoc bookings over date ranges (not shown on grid)."""

from __future__ import annotations

import os
from datetime import date
from typing import Any, Dict, List, Optional

from room_bookings import overlaps, parse_hhmm, school_key, time_to_str

WEEKDAY_MON = 0
WEEKDAY_FRI = 4


def blocks_table() -> str:
    return os.environ.get("SUPABASE_ROOM_BLOCKS_TABLE", "halcyon_room_blocks").strip()


def _parse_date(value: str) -> date:
    return date.fromisoformat(str(value).strip())


def normalize_block_row(row: Dict[str, Any]) -> Dict[str, str]:
    weekdays = row.get("weekdays")
    if weekdays is None:
        weekdays_label = "Every day"
    elif len(weekdays) == 7:
        weekdays_label = "Every day"
    elif weekdays == [0, 1, 2, 3, 4]:
        weekdays_label = "Weekdays (Mon–Fri)"
    elif weekdays == [5, 6]:
        weekdays_label = "Weekends (Sat–Sun)"
    elif len(weekdays) == 1:
        names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        weekdays_label = f"Every {names[int(weekdays[0])]}"
    else:
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        weekdays_label = ", ".join(names[int(d)] for d in sorted(weekdays))
    return {
        "id": str(row.get("id") or ""),
        "room": str(row.get("room") or ""),
        "start_date": str(row.get("start_date") or ""),
        "end_date": str(row.get("end_date") or ""),
        "start_time": time_to_str(parse_hhmm(str(row.get("start_time") or "08:00")[:5])),
        "end_time": time_to_str(parse_hhmm(str(row.get("end_time") or "17:00")[:5])),
        "weekdays": weekdays_label,
        "weekdays_raw": "" if row.get("weekdays") is None else ",".join(str(int(d)) for d in row["weekdays"]),
        "title": str(row.get("title") or ""),
        "staff_name": str(row.get("staff_name") or ""),
        "created_at": str(row.get("created_at") or ""),
    }


def parse_weekdays(raw) -> Optional[List[int]]:
    if raw is None or raw == "" or raw == []:
        return None
    if isinstance(raw, list):
        values = [int(v) for v in raw]
    else:
        values = [int(part.strip()) for part in str(raw).split(",") if part.strip() != ""]
    for value in values:
        if value < 0 or value > 6:
            raise ValueError("Weekdays must be 0 (Mon) through 6 (Sun)")
    return sorted(set(values))


def block_covers(
    block: Dict[str, Any],
    day: date,
    slot_start,
    slot_end,
) -> bool:
    start_date = _parse_date(str(block.get("start_date") or ""))
    end_date = _parse_date(str(block.get("end_date") or ""))
    if day < start_date or day > end_date:
        return False
    weekdays = block.get("weekdays")
    if weekdays is not None and day.weekday() not in weekdays:
        return False
    block_start = parse_hhmm(str(block.get("start_time") or "08:00")[:5])
    block_end = parse_hhmm(str(block.get("end_time") or "17:00")[:5])
    return overlaps(slot_start, slot_end, block_start, block_end)


def list_blocks(
    sb: Any,
    *,
    day_from: str,
    day_to: str,
    room: str = "",
) -> List[Dict[str, str]]:
    sk = school_key()
    table = blocks_table()
    query = (
        sb.table(table)
        .select(
            "id,room,start_date,end_date,start_time,end_time,weekdays,title,staff_name,created_at"
        )
        .eq("school_key", sk)
        .lte("start_date", day_to)
        .gte("end_date", day_from)
    )
    if room.strip():
        query = query.eq("room", room.strip())
    res = query.order("start_date").order("room").execute()
    return [normalize_block_row(row) for row in (res.data or [])]


def list_blocks_raw(sb: Any, *, day_from: str, day_to: str, room: str = "") -> List[Dict[str, Any]]:
    sk = school_key()
    table = blocks_table()
    query = (
        sb.table(table)
        .select("id,room,start_date,end_date,start_time,end_time,weekdays,title,staff_name")
        .eq("school_key", sk)
        .lte("start_date", day_to)
        .gte("end_date", day_from)
    )
    if room.strip():
        query = query.eq("room", room.strip())
    res = query.execute()
    return res.data or []


def is_room_blocked(
    sb: Any,
    *,
    room: str,
    day_date: str,
    start_time: str,
    end_time: str,
) -> bool:
    day = _parse_date(day_date)
    slot_start = parse_hhmm(start_time)
    slot_end = parse_hhmm(end_time)
    for block in list_blocks_raw(sb, day_from=day_date, day_to=day_date, room=room):
        if block_covers(block, day, slot_start, slot_end):
            return True
    return False


def create_block(
    sb: Any,
    *,
    room: str,
    start_date: str,
    end_date: str,
    title: str,
    staff_name: str,
    start_time: str = "08:00",
    end_time: str = "17:00",
    weekdays: Optional[List[int]] = None,
) -> Dict[str, str]:
    room = room.strip()
    title = title.strip()
    staff_name = staff_name.strip()
    if not room:
        raise ValueError("Room is required")
    if not title:
        raise ValueError("Title / reason is required")
    if not staff_name:
        raise ValueError("Staff name is required")
    start_day = _parse_date(start_date)
    end_day = _parse_date(end_date)
    if end_day < start_day:
        raise ValueError("End date must be on or after start date")
    start = parse_hhmm(start_time)
    end = parse_hhmm(end_time)
    if end <= start:
        raise ValueError("End time must be after start time")

    sk = school_key()
    table = blocks_table()
    row = {
        "school_key": sk,
        "room": room,
        "start_date": start_day.isoformat(),
        "end_date": end_day.isoformat(),
        "start_time": time_to_str(start),
        "end_time": time_to_str(end),
        "title": title,
        "staff_name": staff_name,
    }
    if weekdays is not None:
        row["weekdays"] = weekdays
    res = sb.table(table).insert(row).execute()
    rows = res.data or []
    if not rows:
        raise RuntimeError("Block insert failed")
    return normalize_block_row(rows[0])


def create_blocks_for_periods(
    sb: Any,
    *,
    room: str,
    start_date: str,
    end_date: str,
    title: str,
    staff_name: str,
    period_orders: List[int],
    weekdays: Optional[List[int]] = None,
    clear_existing_bookings: bool = True,
) -> Dict[str, Any]:
    from timetabled_rooms_occupancy import periods_by_order

    catalog = periods_by_order()
    if not period_orders:
        raise ValueError("Select at least one period to block")
    blocks: List[Dict[str, str]] = []
    removed = 0
    for order in sorted(set(int(v) for v in period_orders)):
        period = catalog.get(order)
        if not period:
            raise ValueError(f"Unknown timetable period {order}")
        block = create_block(
            sb,
            room=room,
            start_date=start_date,
            end_date=end_date,
            title=title,
            staff_name=staff_name,
            start_time=period["start"],
            end_time=period["end"],
            weekdays=weekdays,
        )
        blocks.append(block)
        if clear_existing_bookings:
            removed += remove_bookings_overlapping_block(
                sb,
                room=room,
                start_date=start_date,
                end_date=end_date,
                start_time=block["start_time"],
                end_time=block["end_time"],
                weekdays=weekdays,
            )
    period_names = ", ".join(
        catalog[order]["name"] for order in sorted(set(int(v) for v in period_orders))
    )
    return {"blocks": blocks, "count": len(blocks), "removed": removed, "period_names": period_names}


def delete_block(sb: Any, block_id: str) -> None:
    sk = school_key()
    table = blocks_table()
    sb.table(table).delete().eq("school_key", sk).eq("id", block_id).execute()


def remove_bookings_overlapping_block(
    sb: Any,
    *,
    room: str,
    start_date: str,
    end_date: str,
    start_time: str,
    end_time: str,
    weekdays: Optional[List[int]] = None,
) -> int:
    from room_bookings import delete_booking, list_bookings, parse_hhmm

    removed = 0
    block_stub = {
        "start_date": start_date,
        "end_date": end_date,
        "start_time": start_time,
        "end_time": end_time,
        "weekdays": weekdays,
    }
    for booking in list_bookings(sb, day_from=start_date, day_to=end_date, room=room):
        day = _parse_date(booking["day_date"])
        if block_covers(
            block_stub,
            day,
            parse_hhmm(booking["start_time"]),
            parse_hhmm(booking["end_time"]),
        ):
            delete_booking(sb, booking["id"])
            removed += 1
    return removed


def default_year_range(today: Optional[date] = None) -> tuple[str, str]:
    from timetabled_rooms_config import academic_year_bounds

    start, end = academic_year_bounds(today or date.today())
    return start.isoformat(), end.isoformat()
