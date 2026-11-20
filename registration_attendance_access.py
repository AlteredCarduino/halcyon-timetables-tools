"""Staff email allowlist for viewing Registration Attendance."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Set


def access_table() -> str:
    return os.environ.get(
        "SUPABASE_REGISTRATION_ATTENDANCE_ACCESS_TABLE",
        "halcyon_registration_attendance_access",
    ).strip()


def school_key() -> str:
    return (
        os.environ.get("REGISTRATION_SCHOOL_KEY")
        or os.environ.get("SCHOOL_KEY")
        or "halcyon"
    ).strip() or "halcyon"


def normalize_email(value: str) -> str:
    email = str(value or "").strip().lower()
    if not email or "@" not in email:
        raise ValueError("Valid school email is required")
    return email


def env_allowed_emails() -> Optional[Set[str]]:
    """Optional comma-separated fallback when Supabase table is empty / unused."""
    raw = os.environ.get("REGISTRATION_ATTENDANCE_ALLOWED_EMAILS", "").strip()
    if not raw:
        return None
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def list_viewer_access(sb: Any) -> List[Dict[str, str]]:
    sk = school_key()
    table = access_table()
    rows = (
        sb.table(table)
        .select("id,staff_email,created_at")
        .eq("school_key", sk)
        .order("staff_email")
        .execute()
        .data
        or []
    )
    return [
        {
            "id": str(row["id"]),
            "staff_email": str(row.get("staff_email") or ""),
            "created_at": str(row.get("created_at") or ""),
        }
        for row in rows
    ]


def email_can_view(sb: Optional[Any], email: str) -> bool:
    normalized = normalize_email(email)
    env_set = env_allowed_emails()
    if env_set is not None and normalized in env_set:
        return True
    if sb is None:
        return bool(env_set and normalized in env_set)
    table = access_table()
    sk = school_key()
    rows = (
        sb.table(table)
        .select("id")
        .eq("school_key", sk)
        .eq("staff_email", normalized)
        .limit(1)
        .execute()
        .data
        or []
    )
    if rows:
        return True
    # If table has no rows and env list is set, env alone already handled above.
    # Empty table + no env → deny.
    return False


def add_viewer_access(sb: Any, *, staff_email: str) -> Dict[str, str]:
    email = normalize_email(staff_email)
    sk = school_key()
    table = access_table()
    existing = (
        sb.table(table)
        .select("id,staff_email,created_at")
        .eq("school_key", sk)
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
            "staff_email": str(row.get("staff_email") or ""),
            "created_at": str(row.get("created_at") or ""),
        }
    inserted = (
        sb.table(table)
        .insert({"school_key": sk, "staff_email": email})
        .execute()
        .data
        or []
    )
    if not inserted:
        raise ValueError("Could not add viewer access")
    row = inserted[0]
    return {
        "id": str(row["id"]),
        "staff_email": str(row.get("staff_email") or ""),
        "created_at": str(row.get("created_at") or ""),
    }


def delete_viewer_access(sb: Any, access_id: str) -> None:
    sk = school_key()
    table = access_table()
    sb.table(table).delete().eq("school_key", sk).eq("id", access_id).execute()
