"""
Halcyon Timetables & Tools — FastAPI service.

Extracted from halcyon-signin-pwa (signInApp_server).
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

_ROOT = Path(__file__).resolve().parent
_env_file = _ROOT / ".env"
if _env_file.is_file():
    load_dotenv(_env_file)
load_dotenv()

app = FastAPI(title="Halcyon Timetables & Tools")

_cors = os.environ.get("CORS_ALLOW_ORIGINS", "").strip()
_cors_origins = [o.strip() for o in _cors.split(",") if o.strip()] if _cors else []
_cors_origins = list(
    dict.fromkeys(
        _cors_origins
        + [
            "http://127.0.0.1:8080",
            "http://localhost:8080",
            "http://127.0.0.1:8090",
            "http://localhost:8090",
        ]
    )
)
_cors_origin_regex = os.environ.get(
    "CORS_ALLOW_ORIGIN_REGEX",
    r"https://.*\.(ondigitalocean\.app|digitaloceanspaces\.com|cdn\.digitaloceanspaces\.com|netlify\.app|halcyonschool\.com)",
).strip()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_cors_origin_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_STATIC = _ROOT / "static"
_STUDENT_EMAILS_HTML = _ROOT / "StudentEmailListView.html"
_TODAY_HTML = _ROOT / "TodayView.html"
_TEACHING_STAFF_HTML = _ROOT / "TeachingStaffTimetableView.html"
_LESSON_OUT_HTML = _ROOT / "LessonOutView.html"
_SCHOOL_YEAR_CALENDAR_HTML = _ROOT / "SchoolYearCalendarView.html"
_SCHOOL_YEAR_CALENDAR_ALL_EVENTS_HTML = _ROOT / "SchoolYearCalendarAllEventsView.html"
_EMAIL_LIST_USAGE_SQL = _ROOT / "sql" / "007_student_email_list_usage.sql"


@app.get("/")
def index_page():
    """Default landing: Timetabled Rooms."""
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/timetabled-rooms", status_code=302)


@app.get("/api/ping")
def api_ping():
    return {"ok": True, "service": "halcyon-timetables-tools"}


def _staff_page_code() -> str:
    return os.environ.get("HALCYON_STAFF_PAGE_CODE", "7272").strip()


def _require_staff_page_code(staff_code: Optional[str]) -> None:
    expected = _staff_page_code()
    if not expected:
        return
    if (staff_code or "").strip() != expected:
        raise HTTPException(403, "Staff access code required.")


def _staff_table_name() -> str:
    return os.environ.get("SUPABASE_STAFF_TABLE", "halcyon_community").strip()


def _registration_table_name() -> str:
    return os.environ.get("SUPABASE_REGISTRATION_TABLE", "halcyon_registration_data").strip()


def _supabase_client():
    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not supabase_url or not supabase_key:
        raise HTTPException(500, "Server missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")
    from supabase import create_client

    return create_client(supabase_url, supabase_key)


def _staff_table_error_message(exc: Exception, table: str) -> str:
    msg = str(exc)
    low = msg.lower()
    if "permission denied" in low or "42501" in msg:
        return (
            f"Supabase permission denied on {table}. "
            "Run signInApp_server/sql/004_halcyon_community_grants.sql in the Supabase SQL Editor."
        )
    return msg or f"Failed to read {table}"


def _fetch_staff_rows(sb: Any, table: str) -> List[Dict[str, Any]]:
    page = 0
    page_size = 1000
    all_rows: List[Dict[str, Any]] = []
    while True:
        res = (
            sb.table(table)
            .select("school_id,fullname,email,preferred_name,role")
            .range(page * page_size, (page + 1) * page_size - 1)
            .execute()
        )
        rows = res.data or []
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        page += 1
    return all_rows


@app.get("/api/student-emails/students")
def api_student_emails_students():
    """Students from halcyon_registration_data for the email list page."""
    from student_email_list import list_registration_students

    reg_table = _registration_table_name()
    try:
        sb = _supabase_client()
        students = list_registration_students(sb)
        return {"ok": True, "students": students}
    except HTTPException:
        raise
    except Exception as exc:
        msg = str(exc)
        low = msg.lower()
        if "permission denied" in low or "42501" in msg:
            msg = f"Supabase permission denied on {reg_table}."
        return JSONResponse(status_code=500, content={"ok": False, "error": msg})


@app.post("/api/student-emails/resync")
def api_student_emails_resync():
    """Reload halcyon_registration_data from iSAMS (identity only) for Student Emails."""
    from student_email_list import log_email_list_usage
    from sync_registration import cmd_student_list

    try:
        code = cmd_student_list()
        if code != 0:
            return JSONResponse(
                status_code=500,
                content={"ok": False, "error": "iSAMS student sync did not complete."},
            )
        sb = _supabase_client()
        reg_table = _registration_table_name()
        res = sb.table(reg_table).select("school_id", count="exact").limit(1).execute()
        students = res.count if getattr(res, "count", None) is not None else len(res.data or [])
        log_email_list_usage(sb, "resync_from_isams", student_count=students or 0)
        return {
            "ok": True,
            "students": students,
            "message": f"Loaded {students} student row(s) from iSAMS into {reg_table}.",
        }
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@app.post("/api/student-emails/parents")
def api_student_emails_parents(body: Optional[Dict[str, Any]] = Body(default=None)):
    """Parent emails from halcyon_community for selected student school_ids."""
    from student_email_list import (
        gmail_recipient_line,
        log_email_list_usage,
        parent_emails_for_students,
    )

    payload = body or {}
    school_ids = payload.get("schoolIds") or payload.get("school_ids") or []
    if not isinstance(school_ids, list):
        return JSONResponse(status_code=400, content={"ok": False, "error": "schoolIds must be a list."})

    try:
        sb = _supabase_client()
        result = parent_emails_for_students(sb, [str(x) for x in school_ids])
        parents = result.get("parentEmails") or []
        log_email_list_usage(
            sb,
            "add_parents",
            student_count=len(school_ids),
            email_count=len(parents),
            include_parents=True,
        )
        return {
            "ok": True,
            "parentEmails": parents,
            "gmailLine": gmail_recipient_line(parents),
            "byStudent": result.get("byStudent") or {},
        }
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": _staff_table_error_message(exc, _staff_table_name())},
        )


@app.post("/api/student-emails/usage")
def api_student_emails_usage(body: Optional[Dict[str, Any]] = Body(default=None)):
    """Log copy / interaction events from the Student Emails page."""
    from student_email_list import log_email_list_usage

    payload = body or {}
    event_type = str(payload.get("eventType") or payload.get("event_type") or "interaction").strip()
    try:
        sb = _supabase_client()
        logged = log_email_list_usage(
            sb,
            event_type,
            student_count=int(payload.get("studentCount") or 0),
            email_count=int(payload.get("emailCount") or 0),
            include_parents=bool(payload.get("includeParents")),
            details={
                k: payload[k]
                for k in ("parentCount",)
                if payload.get(k) is not None
            },
        )
        return {"ok": True, "loggingReady": logged}
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@app.get("/api/student-emails/usage/summary")
def api_student_emails_usage_summary(days: int = Query(30, ge=1, le=365)):
    """Usage totals: last 30 days + all time since logging started."""
    from student_email_list import usage_summary, usage_summary_dashboard

    try:
        sb = _supabase_client()
        if days != 30:
            return usage_summary(sb, days=days)
        return usage_summary_dashboard(sb)
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@app.get("/api/student-emails/usage/grants-sql", response_class=HTMLResponse)
def api_student_emails_usage_grants_sql():
    """SQL to create student_email_list_usage audit table."""
    if not _EMAIL_LIST_USAGE_SQL.is_file():
        raise HTTPException(404, "sql/007_student_email_list_usage.sql missing")
    return HTMLResponse(
        _EMAIL_LIST_USAGE_SQL.read_text(encoding="utf-8"), media_type="text/plain"
    )


@app.get("/api/today")
def api_today(day: Optional[str] = Query(None, description="School-local date YYYY-MM-DD (default: today)")):
    """Merged snapshot: calendar, iSAMS registration/absences/cover, sign-in Who's In/Out."""
    from today_api import build_today_snapshot

    try:
        return build_today_snapshot(day=day)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@app.get("/api/staff/by-email")
def api_staff_by_email(email: str = Query("", description="Staff email")):
    """Community lookup by email (staff, students, parents, sports)."""
    from sign_in_out import lookup_person_in_community

    em = (email or "").strip().lower()
    if not em or "@" not in em:
        return {"ok": False, "error": "Invalid email."}

    try:
        sb = _supabase_client()
        return lookup_person_in_community(sb, em)
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": _staff_table_error_message(exc, _staff_table_name())},
        )


@app.get("/staff-bridge", response_class=HTMLResponse)
def staff_bridge_page(email: str = Query("")):
    path = _STATIC / "staff-bridge.html"
    if not path.is_file():
        raise HTTPException(404, "static/staff-bridge.html missing")
    html = path.read_text(encoding="utf-8")
    html = html.replace("__EMAIL_JSON__", json.dumps(email or ""))
    return HTMLResponse(html)


@app.get("/api/room-bookings")
def list_room_bookings_api(
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
):
    from room_bookings import list_bookings

    try:
        sb = _supabase_client()
        bookings = list_bookings(
            sb, day_from=from_date, day_to=to_date, school_days_only=True
        )
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"bookings": bookings})


@app.get("/api/room-bookings/school-calendar")
def room_bookings_school_calendar_api():
    from school_calendar_store import load_school_calendar_data

    return JSONResponse(load_school_calendar_data())


@app.get("/api/room-blocks")
def list_room_blocks_api(
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
    room: str = Query(""),
):
    from room_blocks import list_blocks

    try:
        sb = _supabase_client()
        blocks = list_blocks(sb, day_from=from_date, day_to=to_date, room=room)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"blocks": blocks})


@app.get("/api/room-bookings/periods")
def room_bookings_periods_api():
    from timetabled_rooms_occupancy import list_school_periods

    return JSONResponse({"periods": list_school_periods()})


@app.get("/api/room-bookings/year-range")
def room_bookings_year_range():
    from room_blocks import default_year_range

    start, end = default_year_range()
    return JSONResponse({"start_date": start, "end_date": end})


@app.get("/api/room-bookings/admin/year-range")
def room_bookings_admin_year_range(x_admin_token: str = Header("", alias="X-Admin-Token")):
    from room_blocks import default_year_range

    _require_room_bookings_admin(x_admin_token)
    start, end = default_year_range()
    return JSONResponse({"start_date": start, "end_date": end})


@app.post("/api/room-bookings")
def create_room_booking_api(body: Dict[str, Any] = Body(...)):
    from room_bookings import create_recurring_bookings

    try:
        sb = _supabase_client()
        raw_order = body.get("period_order")
        period_order = None
        if raw_order not in (None, ""):
            period_order = int(raw_order)
        result = create_recurring_bookings(
            sb,
            room=str(body.get("room") or ""),
            day_date=str(body.get("day_date") or ""),
            start_time=str(body.get("start_time") or ""),
            end_time=str(body.get("end_time") or ""),
            title=str(body.get("title") or ""),
            staff_name=str(body.get("staff_name") or ""),
            staff_email=str(body.get("staff_email") or ""),
            repeat=str(body.get("repeat") or "none"),
            repeat_end_date=str(body.get("repeat_end_date") or ""),
            period_order=period_order,
            period_start=str(body.get("period_start") or ""),
            period_end=str(body.get("period_end") or ""),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    bookings = result.get("bookings") or []
    payload: Dict[str, Any] = {
        "bookings": bookings,
        "count": result.get("count", len(bookings)),
        "skipped": result.get("skipped") or [],
        "skip_summary": result.get("skip_summary") or {},
    }
    if len(bookings) == 1:
        payload["booking"] = bookings[0]
    return JSONResponse(payload)


@app.delete("/api/room-bookings/{booking_id}")
def delete_room_booking_api(
    booking_id: str,
    scope: str = Query("single"),
    staff_email: str = Query(""),
    staff_name: str = Query(""),
):
    from room_bookings import delete_booking_for_user

    try:
        sb = _supabase_client()
        removed = delete_booking_for_user(
            sb,
            booking_id,
            staff_email=staff_email,
            staff_name=staff_name,
            scope=scope,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"ok": True, "removed": removed})


def _room_bookings_admin_token() -> str:
    return os.environ.get("ROOM_BOOKINGS_ADMIN_TOKEN", "").strip()


def _require_room_bookings_admin(admin_token: str = "") -> None:
    expected = _room_bookings_admin_token()
    if not expected:
        raise HTTPException(
            503,
            "Room bookings admin is not configured. Set ROOM_BOOKINGS_ADMIN_TOKEN on the server.",
        )
    provided = (admin_token or "").strip()
    if provided != expected:
        raise HTTPException(401, "Invalid admin token")


@app.get("/room-bookings-admin")
def room_bookings_admin_page():
    path = _STATIC / "timetabled_rooms" / "room-bookings-admin.html"
    if not path.is_file():
        raise HTTPException(404, "room-bookings-admin.html missing")
    return HTMLResponse(path.read_text(encoding="utf-8"), media_type="text/html; charset=utf-8")


@app.get("/registration-attendance-admin")
def registration_attendance_admin_page():
    path = _STATIC / "signin" / "registration-attendance-admin.html"
    if not path.is_file():
        raise HTTPException(404, "registration-attendance-admin.html missing")
    return HTMLResponse(path.read_text(encoding="utf-8"), media_type="text/html; charset=utf-8")


@app.get("/api/registration-attendance/check-access")
def registration_attendance_check_access_api(email: str = Query("")):
    from registration_attendance_access import email_can_view

    if not str(email or "").strip():
        return JSONResponse({"allowed": False, "error": "Email is required"})
    try:
        sb = None
        try:
            sb = _supabase_client()
        except Exception:
            sb = None
        allowed = email_can_view(sb, email)
    except ValueError as exc:
        return JSONResponse({"allowed": False, "error": str(exc)})
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    return JSONResponse({"allowed": allowed})


@app.get("/api/registration-attendance/admin/ping")
def registration_attendance_admin_ping(x_admin_token: str = Header("", alias="X-Admin-Token")):
    _require_room_bookings_admin(x_admin_token)
    return JSONResponse({"ok": True})


@app.get("/api/registration-attendance/admin/viewers")
def registration_attendance_admin_list_viewers(
    x_admin_token: str = Header("", alias="X-Admin-Token"),
):
    from registration_attendance_access import env_allowed_emails, list_viewer_access

    _require_room_bookings_admin(x_admin_token)
    env_set = env_allowed_emails()
    try:
        sb = _supabase_client()
        rows = list_viewer_access(sb)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse(
        {
            "access": rows,
            "env_emails": sorted(env_set) if env_set else [],
        }
    )


@app.post("/api/registration-attendance/admin/viewers")
def registration_attendance_admin_add_viewer(
    body: Dict[str, Any] = Body(...),
    x_admin_token: str = Header("", alias="X-Admin-Token"),
):
    from registration_attendance_access import add_viewer_access

    _require_room_bookings_admin(x_admin_token)
    try:
        sb = _supabase_client()
        row = add_viewer_access(
            sb,
            staff_email=str(body.get("staff_email") or body.get("email") or ""),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    return JSONResponse({"ok": True, "access": row})


@app.delete("/api/registration-attendance/admin/viewers/{access_id}")
def registration_attendance_admin_delete_viewer(
    access_id: str,
    x_admin_token: str = Header("", alias="X-Admin-Token"),
):
    from registration_attendance_access import delete_viewer_access

    _require_room_bookings_admin(x_admin_token)
    try:
        sb = _supabase_client()
        delete_viewer_access(sb, access_id)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"ok": True})


@app.get("/api/room-bookings/rooms")
def list_room_bookings_rooms_api():
    from room_bookings_meta import list_bookable_rooms

    return JSONResponse({"rooms": list_bookable_rooms()})


@app.get("/api/room-bookings/admin/ping")
def room_bookings_admin_ping_header(x_admin_token: str = Header("", alias="X-Admin-Token")):
    _require_room_bookings_admin(x_admin_token)
    return JSONResponse({"ok": True})


@app.get("/api/room-bookings/restricted-rooms")
def room_bookings_restricted_rooms_api():
    from room_booking_access import restricted_rooms

    return JSONResponse({"restricted_rooms": restricted_rooms()})


@app.get("/api/room-bookings/check-access")
def room_bookings_check_access_api(
    room: str = Query(""),
    email: str = Query(""),
):
    from room_booking_access import email_can_book_room, is_restricted_room

    name = str(room or "").strip()
    if not name:
        raise HTTPException(400, "Room is required")
    if not is_restricted_room(name):
        return JSONResponse({"allowed": True, "restricted": False})
    if not str(email or "").strip():
        return JSONResponse({"allowed": False, "restricted": True})
    try:
        sb = _supabase_client()
        allowed = email_can_book_room(sb, name, email)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    return JSONResponse({"allowed": allowed, "restricted": True})


@app.get("/api/room-bookings/admin/settings")
def room_bookings_admin_settings_api(x_admin_token: str = Header("", alias="X-Admin-Token")):
    from room_bookings import allowed_staff_emails
    from room_booking_access import restricted_rooms

    _require_room_bookings_admin(x_admin_token)
    allowed = allowed_staff_emails()
    emails = sorted(allowed) if allowed is not None else []
    return JSONResponse(
        {
            "email_restrictions_enabled": allowed is not None,
            "allowed_emails": emails,
            "allowed_count": len(emails),
            "restricted_rooms": restricted_rooms(),
        }
    )


@app.get("/api/room-bookings/admin/room-access")
def room_bookings_admin_list_room_access_api(
    room: str = Query(""),
    x_admin_token: str = Header("", alias="X-Admin-Token"),
):
    from room_booking_access import list_room_access

    _require_room_bookings_admin(x_admin_token)
    try:
        sb = _supabase_client()
        rows = list_room_access(sb, room=room)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"access": rows})


@app.post("/api/room-bookings/admin/room-access")
def room_bookings_admin_add_room_access_api(
    body: Dict[str, Any] = Body(...),
    x_admin_token: str = Header("", alias="X-Admin-Token"),
):
    from room_booking_access import add_room_access

    _require_room_bookings_admin(x_admin_token)
    try:
        sb = _supabase_client()
        row = add_room_access(
            sb,
            room=str(body.get("room") or ""),
            staff_email=str(body.get("staff_email") or body.get("email") or ""),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    return JSONResponse({"ok": True, "access": row})


@app.delete("/api/room-bookings/admin/room-access/{access_id}")
def room_bookings_admin_delete_room_access_api(
    access_id: str,
    x_admin_token: str = Header("", alias="X-Admin-Token"),
):
    from room_booking_access import delete_room_access

    _require_room_bookings_admin(x_admin_token)
    try:
        sb = _supabase_client()
        delete_room_access(sb, access_id)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"ok": True})


@app.get("/api/room-bookings/admin")
def room_bookings_admin_list_api(
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
    room: str = Query(""),
    x_admin_token: str = Header("", alias="X-Admin-Token"),
):
    from room_bookings import list_bookings

    _require_room_bookings_admin(x_admin_token)
    try:
        sb = _supabase_client()
        bookings = list_bookings(sb, day_from=from_date, day_to=to_date, room=room)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"bookings": bookings})


@app.post("/api/room-bookings/admin")
def room_bookings_admin_create_api(
    body: Dict[str, Any] = Body(...),
    x_admin_token: str = Header("", alias="X-Admin-Token"),
):
    from room_bookings import create_booking_admin

    _require_room_bookings_admin(x_admin_token)
    try:
        sb = _supabase_client()
        booking = create_booking_admin(
            sb,
            room=str(body.get("room") or ""),
            day_date=str(body.get("day_date") or ""),
            start_time=str(body.get("start_time") or ""),
            end_time=str(body.get("end_time") or ""),
            title=str(body.get("title") or ""),
            staff_name=str(body.get("staff_name") or ""),
            allow_overlap_replace=bool(body.get("replace_existing", True)),
        )
        message = (
            f"Visible booking created for {booking['room']} on {booking['day_date']} "
            f"({booking['start_time']}–{booking['end_time']})."
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    return JSONResponse({"ok": True, "message": message, "booking": booking})


@app.get("/api/room-bookings/admin/blocks")
def room_bookings_admin_list_blocks_api(
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
    room: str = Query(""),
    x_admin_token: str = Header("", alias="X-Admin-Token"),
):
    from room_blocks import list_blocks

    _require_room_bookings_admin(x_admin_token)
    try:
        sb = _supabase_client()
        blocks = list_blocks(sb, day_from=from_date, day_to=to_date, room=room)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"blocks": blocks})


@app.post("/api/room-bookings/admin/blocks")
def room_bookings_admin_create_block_api(
    body: Dict[str, Any] = Body(...),
    x_admin_token: str = Header("", alias="X-Admin-Token"),
):
    from room_blocks import create_blocks_for_periods, parse_weekdays

    _require_room_bookings_admin(x_admin_token)
    try:
        sb = _supabase_client()
        room = str(body.get("room") or "")
        start_date = str(body.get("start_date") or body.get("day_date") or "")
        end_date = str(body.get("end_date") or start_date)
        title = str(body.get("title") or "")
        staff_name = str(body.get("staff_name") or "")
        weekdays = parse_weekdays(body.get("weekdays"))
        repeat = str(body.get("repeat") or "").strip().lower()
        if repeat in ("daily", "every_day", "all_days"):
            weekdays = None
        elif repeat == "weekdays":
            weekdays = [0, 1, 2, 3, 4]
        elif repeat == "weekends":
            weekdays = [5, 6]
        elif repeat == "custom":
            if not weekdays:
                raise ValueError("Select at least one day under Repeat on")
        elif body.get("weekdays_only") is True:
            weekdays = [0, 1, 2, 3, 4]
        elif body.get("weekdays_only") is False and weekdays is None:
            weekdays = None
        elif weekdays is None and body.get("weekdays_only", True):
            weekdays = [0, 1, 2, 3, 4]

        raw_periods = body.get("period_orders") or body.get("periods") or []
        period_orders: List[int] = []
        if isinstance(raw_periods, list):
            period_orders = [int(v) for v in raw_periods if str(v).strip() != ""]
        elif str(raw_periods).strip():
            period_orders = [int(part.strip()) for part in str(raw_periods).split(",") if part.strip()]

        if not period_orders:
            from timetabled_rooms_config import SCHOOL_DAY_END, SCHOOL_DAY_START
            from room_blocks import create_block, remove_bookings_overlapping_block

            start_time = str(body.get("start_time") or SCHOOL_DAY_START)
            end_time = str(body.get("end_time") or SCHOOL_DAY_END)
            block = create_block(
                sb,
                room=room,
                start_date=start_date,
                end_date=end_date,
                title=title,
                staff_name=staff_name,
                start_time=start_time,
                end_time=end_time,
                weekdays=weekdays,
            )
            removed = 0
            if bool(body.get("clear_existing_bookings", True)):
                removed = remove_bookings_overlapping_block(
                    sb,
                    room=room,
                    start_date=start_date,
                    end_date=end_date,
                    start_time=block["start_time"],
                    end_time=block["end_time"],
                    weekdays=weekdays,
                )
            message = (
                f"Blocked {room} from {block['start_date']} to {block['end_date']} "
                f"({block['start_time']}–{block['end_time']}, {block['weekdays']})."
            )
            if removed:
                message += f" Removed {removed} overlapping booking(s)."
            return JSONResponse({"ok": True, "message": message, "block": block, "blocks": [block], "removed": removed})

        result = create_blocks_for_periods(
            sb,
            room=room,
            start_date=start_date,
            end_date=end_date,
            title=title,
            staff_name=staff_name,
            period_orders=period_orders,
            weekdays=weekdays,
            clear_existing_bookings=bool(body.get("clear_existing_bookings", True)),
        )
        blocks = result["blocks"]
        removed = int(result.get("removed") or 0)
        block = blocks[0] if blocks else {}
        weekdays_label = block.get("weekdays", "")
        message = (
            f"Blocked {room} from {start_date} to {end_date} "
            f"({result.get('period_names')}, {weekdays_label})."
        )
        if len(blocks) > 1:
            message = (
                f"Created {len(blocks)} blocks for {room} from {start_date} to {end_date} "
                f"({result.get('period_names')}, {weekdays_label})."
            )
        if removed:
            message += f" Removed {removed} overlapping booking(s)."
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    return JSONResponse({"ok": True, "message": message, "block": block, "blocks": blocks, "removed": removed})


@app.delete("/api/room-bookings/admin/blocks/{block_id}")
def room_bookings_admin_delete_block_api(
    block_id: str,
    x_admin_token: str = Header("", alias="X-Admin-Token"),
):
    from room_blocks import delete_block

    _require_room_bookings_admin(x_admin_token)
    try:
        sb = _supabase_client()
        delete_block(sb, block_id)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"ok": True})


@app.delete("/api/room-bookings/admin/{booking_id}")
def room_bookings_admin_delete_api(
    booking_id: str,
    x_admin_token: str = Header("", alias="X-Admin-Token"),
):
    from room_bookings import delete_booking

    _require_room_bookings_admin(x_admin_token)
    try:
        sb = _supabase_client()
        delete_booking(sb, booking_id)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"ok": True})


@app.post("/api/room-bookings/admin/clear-day")
def room_bookings_admin_clear_day_api(
    body: Dict[str, Any] = Body(...),
    x_admin_token: str = Header("", alias="X-Admin-Token"),
):
    from room_bookings import delete_bookings_for_room_day

    _require_room_bookings_admin(x_admin_token)
    try:
        sb = _supabase_client()
        removed = delete_bookings_for_room_day(
            sb,
            room=str(body.get("room") or ""),
            day_date=str(body.get("day_date") or ""),
        )
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({"ok": True, "removed": removed})


@app.get("/timetabled-rooms")
def timetabled_rooms_page():
    from timetabled_rooms_serve import load_timetabled_rooms_html

    content = load_timetabled_rooms_html("day")
    if not content:
        raise HTTPException(
            503,
            "Timetabled rooms not generated yet. Run signInApp_server/timetabled_rooms_sync.py or use Refresh from iSAMS.",
        )
    return HTMLResponse(content.decode("utf-8"), media_type="text/html")


@app.get("/timetabled-rooms/all-days")
def timetabled_rooms_all_days_page():
    from timetabled_rooms_serve import load_timetabled_rooms_html

    content = load_timetabled_rooms_html("all-days")
    if not content:
        raise HTTPException(
            503,
            "Timetabled rooms not generated yet. Run signInApp_server/timetabled_rooms_sync.py or use Refresh from iSAMS.",
        )
    return HTMLResponse(content.decode("utf-8"), media_type="text/html")


@app.post("/api/timetabled-rooms/resync")
def api_timetabled_rooms_resync():
    """Reload timetabled rooms HTML from iSAMS batch API into Supabase Storage."""
    import sys
    import traceback

    try:
        from timetabled_rooms_sync import sync_timetabled_rooms

        result = sync_timetabled_rooms()
        if not result.get("uploadedHtml"):
            return JSONResponse(
                status_code=500,
                content={
                    "ok": False,
                    "error": "Sync completed but Supabase Storage upload failed. Check server logs.",
                },
            )
        payload = {
            "ok": True,
            "message": (
                f"Loaded {result['timetableLabel']} — "
                f"{result['rooms']} rooms, {result['weekdays']} weekdays."
            ),
            "timetableLabel": result.get("timetableLabel"),
            "rangeLabel": result.get("rangeLabel"),
            "weekdays": int(result.get("weekdays") or 0),
            "rooms": int(result.get("rooms") or 0),
            "slotEntries": int(result.get("slotEntries") or 0),
            "generatedAt": result.get("generatedAt"),
            "uploadedHtml": bool(result.get("uploadedHtml")),
            "uploadedAllDays": bool(result.get("uploadedAllDays")),
            "uploadedCsv": bool(result.get("uploadedCsv")),
            "uploadedWeekly": bool(result.get("uploadedWeekly")),
            "prunedBookings": result.get("prunedBookings") or {"removed": 0, "items": []},
        }
        return JSONResponse(payload)
    except SystemExit as exc:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": f"Sync aborted ({exc.code})"},
        )
    except Exception as exc:
        print(f"ERROR timetabled-rooms resync: {exc}", file=sys.stderr)
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@app.post("/api/personal-timetables/resync")
def api_personal_timetables_resync():
    """Reload personal timetables HTML from iSAMS batch API into Supabase Storage."""
    from personal_timetables_sync import sync_personal_timetables

    try:
        result = sync_personal_timetables()
        if not result.get("uploaded"):
            return JSONResponse(
                status_code=500,
                content={
                    "ok": False,
                    "error": "Sync completed but Supabase Storage upload failed. Check server logs.",
                },
            )
        return {
            "ok": True,
            "message": (
                f"Loaded {result['timetableLabel']} — "
                f"{result['students']} students, {result['teachers']} teachers."
            ),
            **result,
        }
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@app.get("/lesson-out")
@app.get("/LessonOutView.html")
def lesson_out_page():
    if not _LESSON_OUT_HTML.is_file():
        raise HTTPException(500, "LessonOutView.html missing")
    return FileResponse(
        _LESSON_OUT_HTML,
        media_type="text/html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        },
    )


@app.get("/api/lesson-out")
def api_lesson_out(rebuild: int = Query(0, ge=0, le=1)):
    """Day, period, and lesson choices for the out-of-lesson log."""
    from lesson_out import lesson_out_index

    try:
        return lesson_out_index(rebuild=bool(rebuild))
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@app.get("/api/lesson-out/class")
def api_lesson_out_class(set_id: str = Query("", alias="setId"), code: str = Query("")):
    """Students in one teaching set. Class lists are cached from the iSAMS timetable."""
    from lesson_out import lesson_out_class

    try:
        return lesson_out_class(set_id, code)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@app.get("/teaching-staff-timetable")
@app.get("/TeachingStaffTimetableView.html")
def teaching_staff_timetable_page():
    if not _TEACHING_STAFF_HTML.is_file():
        raise HTTPException(500, "TeachingStaffTimetableView.html missing")
    return FileResponse(
        _TEACHING_STAFF_HTML,
        media_type="text/html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        },
    )


@app.get("/api/teaching-staff-timetable")
def api_teaching_staff_timetable(refresh: int = Query(0, ge=0, le=1)):
    """iSAMS timetable for current employees with isTeachingStaff true."""
    from teaching_staff_timetable import teaching_staff_timetable_payload

    try:
        return teaching_staff_timetable_payload(force=bool(refresh))
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@app.get("/personal-timetables")
def personal_timetables_page():
    from personal_timetables_serve import load_personal_timetables_html

    content = load_personal_timetables_html()
    if not content:
        raise HTTPException(
            503,
            "Personal timetables not generated yet. Run signInApp_server/personal_timetables_sync.py or use Refresh from iSAMS.",
        )
    return HTMLResponse(content, media_type="text/html; charset=utf-8")


@app.get("/calendar")
@app.get("/CalendarView.html")
def calendar_page():
    from calendar_serve import load_calendar_html

    try:
        return HTMLResponse(load_calendar_html().decode("utf-8"), media_type="text/html")
    except FileNotFoundError as exc:
        raise HTTPException(500, str(exc)) from exc


def _upgrade_registration_attendance_html(content: bytes) -> bytes:
    """Force Timetables & Tools shell (navy background, tools nav) on cached HTML."""
    tools_css = (
        b'<link rel="stylesheet" href="/static/signin/halcyon-tools-section.css?v=4" />'
    )
    if b"halcyon-tools-section.css" not in content:
        marker = b'halcyon-subpages.css'
        idx = content.find(marker)
        if idx >= 0:
            close = content.find(b">", idx)
            if close >= 0:
                content = content[: close + 1] + b"\n  " + tools_css + content[close + 1 :]
        else:
            head_close = content.find(b"</head>")
            if head_close >= 0:
                content = content[:head_close] + b"  " + tools_css + b"\n" + content[head_close:]

    content = content.replace(
        b"window.HALCYON_NAV_PAGE = 'registrationattendance';",
        b"window.HALCYON_NAV_MODE = 'tools'; window.HALCYON_SECTION_PAGE = 'registrationattendance';",
    )
    content = content.replace(
        b'window.HALCYON_NAV_PAGE = "registrationattendance";',
        b"window.HALCYON_NAV_MODE = 'tools'; window.HALCYON_SECTION_PAGE = 'registrationattendance';",
    )

    gate = b'<script src="/static/signin/halcyon-registration-attendance-gate.js?v=1"></script>'
    if b"halcyon-registration-attendance-gate.js" not in content:
        if b"halcyon-staff-gate.js" in content:
            for old in (
                b'<script src="/static/signin/halcyon-staff-gate.js?v=1"></script>',
                b'<script src="/static/signin/halcyon-staff-gate.js?v=2"></script>',
            ):
                content = content.replace(old, gate)
        else:
            body = content.find(b"<body")
            if body >= 0:
                close = content.find(b">", body)
                if close >= 0:
                    content = content[: close + 1] + b"\n  " + gate + content[close + 1 :]

    for old_nav in (
        b"/static/signin/halcyon-nav.js?v=14",
        b"/static/signin/halcyon-nav.js?v=21",
        b"/static/signin/halcyon-nav.js?v=30",
        b"/static/signin/halcyon-nav.js?v=31",
    ):
        content = content.replace(old_nav, b"/static/signin/halcyon-nav.js?v=32")

    # Do not key off "halcyon-tools-section" alone — the CSS href also contains that string.
    if b"halcyon-page halcyon-tools-section" not in content and b"halcyon-page' halcyon-tools-section" not in content:
        content = content.replace(
            b'class="halcyon-page"',
            b'class="halcyon-page halcyon-tools-section"',
            1,
        )
        content = content.replace(
            b"class='halcyon-page'",
            b"class='halcyon-page halcyon-tools-section'",
            1,
        )
    return content


@app.get("/registration-wholeyear")
def registration_wholeyear_page():
    from registration_wholeyear_serve import load_registration_wholeyear_html_diagnostics

    content, detail = load_registration_wholeyear_html_diagnostics()
    if not content:
        raise HTTPException(
            503,
            "Registration attendance page not available. "
            "Run signInApp_server/sql/009_registration_attendance_html_cache.sql in Supabase, then "
            "./signInApp_server/run_registration_wholeyear.sh --sync build-html. "
            f"Load checks: {detail}",
        )
    content = _upgrade_registration_attendance_html(content)
    return HTMLResponse(content, media_type="text/html; charset=utf-8")


@app.get("/today")
@app.get("/TodayView.html")
def today_page():
    if not _TODAY_HTML.is_file():
        raise HTTPException(500, "TodayView.html missing")
    return FileResponse(_TODAY_HTML, media_type="text/html")


@app.get("/api/school-calendar/academic-year")
def api_school_calendar_academic_year(
    year: Optional[int] = Query(
        None,
        description="Academic year start year (September), e.g. 2025 for 2025–26",
    ),
):
    """School-wide Google Calendar ICS, all events in the academic year (vertical list UI)."""
    from today_api import fetch_school_calendar_academic_year

    try:
        return fetch_school_calendar_academic_year(year=year)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@app.get("/school-calendar-year/all-events")
@app.get("/SchoolYearCalendarAllEventsView.html")
def school_year_calendar_all_events_page():
    if not _SCHOOL_YEAR_CALENDAR_ALL_EVENTS_HTML.is_file():
        raise HTTPException(500, "SchoolYearCalendarAllEventsView.html missing")
    return FileResponse(_SCHOOL_YEAR_CALENDAR_ALL_EVENTS_HTML, media_type="text/html")


@app.get("/school-calendar-year")
@app.get("/SchoolYearCalendarView.html")
def school_year_calendar_page():
    if not _SCHOOL_YEAR_CALENDAR_HTML.is_file():
        raise HTTPException(500, "SchoolYearCalendarView.html missing")
    return FileResponse(_SCHOOL_YEAR_CALENDAR_HTML, media_type="text/html")


@app.get("/student-emails")
@app.get("/StudentEmailListView.html")
def student_emails_page():
    if not _STUDENT_EMAILS_HTML.is_file():
        raise HTTPException(500, "StudentEmailListView.html missing")
    return FileResponse(_STUDENT_EMAILS_HTML, media_type="text/html")




if _STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
