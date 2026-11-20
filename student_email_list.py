"""
Student email list page: registration students + optional parent emails from halcyon_community.
Usage is logged to student_email_list_usage (see sql/007_student_email_list_usage.sql).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from isams_registration import school_id_raw
from sign_in_out import _community_table, _emails_from_field, _norm_email, _registration_table


def _usage_table() -> str:
    return os.environ.get(
        "SUPABASE_EMAIL_LIST_USAGE_TABLE", "student_email_list_usage"
    ).strip()


def _school_tz() -> ZoneInfo:
    name = os.environ.get("SCHOOL_TIMEZONE", "Europe/London").strip() or "Europe/London"
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Europe/London")


def _row_is_parent(row: Dict[str, Any]) -> bool:
    role = str(row.get("role") or "").strip().lower()
    if not role:
        return False
    if role in ("parent", "guardian"):
        return True
    for token in role.replace("\n", ";").replace(",", ";").split(";"):
        t = token.strip()
        if t in ("parent", "guardian") or "parent" in t:
            return True
    return False


def _dedupe_emails(emails: List[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for raw in emails:
        em = _norm_email(raw)
        if em and "@" in em and em not in seen:
            seen.add(em)
            out.append(em)
    return out


def gmail_recipient_line(emails: List[str]) -> str:
    """Comma-separated line for Gmail To/Bcc field."""
    return ", ".join(_dedupe_emails(emails))


def list_registration_students(sb: Any) -> List[Dict[str, str]]:
    reg_table = _registration_table()
    res = (
        sb.table(reg_table)
        .select("school_id,name,year,email")
        .order("year")
        .order("name")
        .execute()
    )
    students: List[Dict[str, str]] = []
    for row in res.data or []:
        sid = school_id_raw(row.get("school_id"))
        if not sid:
            continue
        students.append(
            {
                "schoolId": sid,
                "name": str(row.get("name") or "").strip(),
                "year": str(row.get("year") if row.get("year") is not None else "").strip(),
                "email": _norm_email(str(row.get("email") or "")),
            }
        )
    return students


def _dedupe_school_ids(school_ids: List[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for raw in school_ids:
        sid = school_id_raw(raw)
        if sid and sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


def parent_emails_for_students(
    sb: Any, school_ids: List[str]
) -> Dict[str, Any]:
    """Parent emails keyed by student school_id; halcyon_community role must be parent."""
    raw_ids = _dedupe_school_ids(school_ids)
    if not raw_ids:
        return {"byStudent": {}, "parentEmails": []}

    table = _community_table()
    by_student: Dict[str, List[str]] = {sid: [] for sid in raw_ids}
    all_parents: List[str] = []

    chunk = 200
    for i in range(0, len(raw_ids), chunk):
        batch = raw_ids[i : i + chunk]
        res = (
            sb.table(table)
            .select("school_id,email,role")
            .in_("school_id", batch)
            .execute()
        )
        for row in res.data or []:
            if not _row_is_parent(row):
                continue
            sid = school_id_raw(row.get("school_id"))
            if sid not in by_student:
                continue
            for em in _emails_from_field(str(row.get("email") or "")):
                by_student[sid].append(em)
                all_parents.append(em)

    for sid in by_student:
        by_student[sid] = _dedupe_emails(by_student[sid])

    return {
        "byStudent": by_student,
        "parentEmails": _dedupe_emails(all_parents),
    }


def log_email_list_usage(
    sb: Any,
    event_type: str,
    *,
    student_count: int = 0,
    email_count: int = 0,
    include_parents: bool = False,
    details: Optional[Dict[str, Any]] = None,
) -> bool:
    """Best-effort insert; returns False if table missing or insert failed."""
    payload = {
        "event_type": event_type,
        "student_count": max(0, int(student_count)),
        "email_count": max(0, int(email_count)),
        "include_parents": bool(include_parents),
        "details": details or {},
    }
    try:
        sb.table(_usage_table()).insert(payload).execute()
        return True
    except Exception as exc:
        msg = str(exc)
        if "does not exist" in msg or "42P01" in msg:
            print(
                "WARN: student_email_list_usage table missing — "
                "run signInApp_server/sql/007_student_email_list_usage.sql",
                file=sys.stderr,
            )
        return False


def _usage_totals_from_rows(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    totals: Dict[str, int] = {}
    for row in rows:
        et = str(row.get("event_type") or "unknown")
        totals[et] = totals.get(et, 0) + 1
    return totals


def _fetch_usage_rows(
    sb: Any, since: Optional[datetime] = None, *, max_rows: int = 10000
) -> List[Dict[str, Any]]:
    page_size = 1000
    page = 0
    all_rows: List[Dict[str, Any]] = []
    while len(all_rows) < max_rows:
        q = (
            sb.table(_usage_table())
            .select("event_type,created_at")
            .order("created_at", desc=True)
        )
        if since is not None:
            q = q.gte("created_at", since.isoformat())
        res = q.range(page * page_size, (page + 1) * page_size - 1).execute()
        rows = res.data or []
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        page += 1
    return all_rows


def usage_summary(sb: Any, days: int = 30) -> Dict[str, Any]:
    """Totals for the last N days (legacy single-range response)."""
    since = datetime.now(_school_tz()) - timedelta(days=max(1, min(days, 365)))
    try:
        rows = _fetch_usage_rows(sb, since)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "events": [], "totals": {}}

    return {
        "ok": True,
        "days": days,
        "events": rows[:100],
        "totals": _usage_totals_from_rows(rows),
        "totalEvents": len(rows),
    }


def usage_summary_dashboard(sb: Any) -> Dict[str, Any]:
    """Last 30 days + all-time totals since logging began."""
    tz = _school_tz()
    since_30 = datetime.now(tz) - timedelta(days=30)
    try:
        rows_30 = _fetch_usage_rows(sb, since_30)
        rows_all = _fetch_usage_rows(sb, None)
        first_res = (
            sb.table(_usage_table())
            .select("created_at")
            .order("created_at", desc=False)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        msg = str(exc)
        return {"ok": False, "error": msg, "last30": {}, "allTime": {}}

    tracking_since = None
    if first_res.data:
        tracking_since = str(first_res.data[0].get("created_at") or "").strip() or None

    return {
        "ok": True,
        "last30": {
            "label": "Last 30 days",
            "totals": _usage_totals_from_rows(rows_30),
            "totalEvents": len(rows_30),
        },
        "allTime": {
            "label": "All time",
            "totals": _usage_totals_from_rows(rows_all),
            "totalEvents": len(rows_all),
            "trackingSince": tracking_since,
        },
    }
