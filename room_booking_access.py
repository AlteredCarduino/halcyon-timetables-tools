"""Per-room booking access (restricted rooms + email allowlists)."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from room_bookings import school_key
from timetabled_rooms_config import RESTRICTED_ROOMS


def access_table() -> str:
    return os.environ.get(
        "SUPABASE_ROOM_BOOKING_ACCESS_TABLE", "halcyon_room_booking_access"
    ).strip()


def restricted_rooms() -> List[str]:
    return list(RESTRICTED_ROOMS)


def is_restricted_room(room: str) -> bool:
    name = str(room or "").strip()
    return name in set(RESTRICTED_ROOMS)


def normalize_email(value: str) -> str:
    email = str(value or "").strip().lower()
    if not email or "@" not in email:
        raise ValueError("Valid school email is required")
    return email


def list_room_access(sb: Any, room: str = "") -> List[Dict[str, str]]:
    sk = school_key()
    table = access_table()
    query = sb.table(table).select("id,room,staff_email,created_at").eq("school_key", sk)
    if room.strip():
        query = query.eq("room", room.strip())
    rows = query.order("room").order("staff_email").execute().data or []
    return [
        {
            "id": str(row["id"]),
            "room": str(row.get("room") or ""),
            "staff_email": str(row.get("staff_email") or ""),
            "created_at": str(row.get("created_at") or ""),
        }
        for row in rows
    ]


def email_can_book_room(sb: Any, room: str, email: str) -> bool:
    if not is_restricted_room(room):
        return True
    normalized = normalize_email(email)
    sk = school_key()
    table = access_table()
    rows = (
        sb.table(table)
        .select("id")
        .eq("school_key", sk)
        .eq("room", room.strip())
        .eq("staff_email", normalized)
        .limit(1)
        .execute()
        .data
        or []
    )
    return bool(rows)


def assert_room_booking_access(sb: Any, room: str, email: str) -> None:
    if not is_restricted_room(room):
        return
    if email_can_book_room(sb, room, email):
        return
    raise ValueError(
        f"You are not authorised to book {room.strip()}. Ask admin to add your school email."
    )


def add_room_access(sb: Any, *, room: str, staff_email: str) -> Dict[str, str]:
    name = str(room or "").strip()
    if not name:
        raise ValueError("Room is required")
    if not is_restricted_room(name):
        raise ValueError(f"{name} is not configured as a restricted room")
    email = normalize_email(staff_email)
    sk = school_key()
    table = access_table()
    existing = (
        sb.table(table)
        .select("id,room,staff_email,created_at")
        .eq("school_key", sk)
        .eq("room", name)
        .eq("staff_email", email)
        .limit(1)
        .execute()
        .data
        or []
    )
    if existing:
        row = existing[0]
        return {
            "id": str(row["id"]),
            "room": str(row.get("room") or ""),
            "staff_email": str(row.get("staff_email") or ""),
            "created_at": str(row.get("created_at") or ""),
        }
    inserted = (
        sb.table(table)
        .insert({"school_key": sk, "room": name, "staff_email": email})
        .execute()
        .data
        or []
    )
    if not inserted:
        raise ValueError("Could not add room access")
    row = inserted[0]
    return {
        "id": str(row["id"]),
        "room": str(row.get("room") or ""),
        "staff_email": str(row.get("staff_email") or ""),
        "created_at": str(row.get("created_at") or ""),
    }


def delete_room_access(sb: Any, access_id: str) -> None:
    sk = school_key()
    table = access_table()
    sb.table(table).delete().eq("school_key", sk).eq("id", access_id).execute()
