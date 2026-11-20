"""
Aggregate “today at Halcyon” data for GET /api/today.

Sources: public school-wide Google Calendar ICS, iSAMS REST (registration, HR absences),
optional iSAMS batch CoverManager, and Supabase sign-in Who's In/Out.
"""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

from isams_registration import (
    get_current_time_hhmm,
    get_today_registration_periods,
    isams_get_students,
    isams_get_token,
    load_isams_config_from_env,
    period_day_iso,
    period_has_started,
    registration_period_ui_label,
    registration_status_symbol_from_body,
    school_id_raw,
    today_local_date,
)
from isams_registration_attendance_rest import (
    _get_json,
    _get_json_optional,
    list_register_statuses,
)

DEFAULT_SCHOOL_ICS = (
    "https://calendar.google.com/calendar/ical/"
    "halcyonschool.com_svlqctumhrn3kevhlgv04d63tk%40group.calendar.google.com/public/basic.ics"
)
# Public staff-absence basic.ics often 404; set TODAY_STAFF_ABSENCE_CALENDAR_ICS_URL (secret iCal) on the server.
STAFF_ABSENCE_ICS_DOC_URL = (
    "https://calendar.google.com/calendar/ical/"
    "halcyonschool.com_u0hh4k04k0hjd9asltr9aklbko%40group.calendar.google.com/public/basic.ics"
)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _parse_day(day: Optional[str], cfg: Dict[str, str]) -> date:
    if day:
        return datetime.strptime(day.strip()[:10], "%Y-%m-%d").date()
    return today_local_date(cfg)


def _section_err(label: str, exc: BaseException) -> Dict[str, Any]:
    return {"ok": False, "error": f"{label}: {exc}"}


def _parse_ics_property(line: str) -> Tuple[str, str, Dict[str, str]]:
    prop_part, _, value = line.partition(":")
    pieces = prop_part.split(";")
    params: Dict[str, str] = {}
    for piece in pieces[1:]:
        if "=" in piece:
            key, val = piece.split("=", 1)
            params[key.strip()] = val.strip()
    return pieces[0].strip(), value.strip(), params


def _zone_for_ics(params: Dict[str, str], fallback: ZoneInfo, raw: str) -> ZoneInfo:
    tzid = (params.get("TZID") or "").strip()
    if tzid:
        try:
            return ZoneInfo(tzid)
        except Exception:
            pass
    if raw.endswith("Z"):
        return ZoneInfo("UTC")
    return fallback


def _parse_ics_date_value(
    raw: str,
    fallback_tz: ZoneInfo,
    params: Optional[Dict[str, str]] = None,
) -> Tuple[str, Optional[date], Optional[datetime]]:
    """Return (kind, date_only, datetime_with_tz) where kind is 'date' or 'datetime'."""
    raw = (raw or "").strip()
    params = params or {}
    if not raw:
        return ("", None, None)
    if params.get("VALUE") == "DATE" or (len(raw) == 8 and raw.isdigit() and "T" not in raw):
        d = datetime.strptime(raw, "%Y%m%d").date()
        return ("date", d, None)
    if "T" in raw:
        zone = _zone_for_ics(params, fallback_tz, raw)
        if raw.endswith("Z"):
            dt = datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=ZoneInfo("UTC"))
        else:
            dt = datetime.strptime(raw[:15], "%Y%m%dT%H%M%S").replace(tzinfo=zone)
        return ("datetime", dt.date(), dt)
    return ("", None, None)


def _fetch_ics_text(url: str, label: str) -> Tuple[Optional[str], Optional[str]]:
    if not url:
        return None, f"{label} ICS URL not set"
    try:
        with httpx.Client(timeout=45.0, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            if not (r.text or "").strip().startswith("BEGIN:VCALENDAR"):
                return None, (
                    f"{label} feed did not return a calendar (.ics). "
                    "Use the Secret address in iCal format from Google Calendar settings, "
                    "or make the calendar public with “See all event details”."
                )
            return r.text, None
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None, (
                f"{label} ICS URL returned 404 (not public). "
                "Use the Secret address in iCal format from Google Calendar settings."
            )
        return None, f"{label}: HTTP {exc.response.status_code}"
    except Exception as exc:
        return None, f"{label}: {exc}"


def _staff_absence_ics_url() -> str:
    explicit = _env("TODAY_STAFF_ABSENCE_CALENDAR_ICS_URL")
    if explicit.lower() in ("0", "false", "no", "off", "disabled"):
        return ""
    if explicit and "/public/basic.ics" in explicit:
        return ""
    return explicit


def _staff_absence_public_ics_configured() -> bool:
    explicit = _env("TODAY_STAFF_ABSENCE_CALENDAR_ICS_URL")
    return bool(explicit and "/public/basic.ics" in explicit)


def _staff_absence_configured() -> bool:
    if _staff_absence_ics_url():
        return True
    from google_credentials import google_credentials_available

    return google_credentials_available()


def _fetch_school_ics_text() -> Tuple[Optional[str], Optional[str]]:
    url = _env("TODAY_SCHOOL_CALENDAR_ICS_URL", DEFAULT_SCHOOL_ICS)
    return _fetch_ics_text(url, "School calendar")


def _fetch_staff_absence_ics_text() -> Tuple[Optional[str], Optional[str]]:
    url = _staff_absence_ics_url()
    if not url:
        return None, None
    return _fetch_ics_text(url, "Staff absence calendar")


def _staff_absence_setup_hint() -> str:
    return (
        "On the server, set TODAY_STAFF_ABSENCE_CALENDAR_ICS_URL to the Google Calendar "
        "“Secret address in iCal format” (the public basic.ics link often returns 404). "
        "Alternatively, share the staff absence calendar with your service account "
        "(GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SERVICE_ACCOUNT_JSON) and enable the Calendar API."
    )


def _fetch_staff_absence_events(
    range_start: date, range_end: date, tz: ZoneInfo
) -> Tuple[List[Dict[str, Any]], Optional[str], str]:
    """ICS first; on failure try Google Calendar API (calendar shared with service account)."""
    if _staff_absence_public_ics_configured():
        return (
            [],
            "TODAY_STAFF_ABSENCE_CALENDAR_ICS_URL points at the public basic.ics feed, which "
            f"returns 404. Replace it with the Secret address in iCal format. {_staff_absence_setup_hint()}",
            "none",
        )

    if not _staff_absence_configured():
        return [], None, "disabled"

    staff_text: Optional[str] = None
    staff_fetch_err: Optional[str] = None
    if _staff_absence_ics_url():
        staff_text, staff_fetch_err = _fetch_staff_absence_ics_text()
    if staff_text:
        events = parse_ics_events_in_range(staff_text, tz, range_start, range_end)
        for ev in events:
            ev["source"] = "staff"
        return events, None, "ics"

    from google_credentials import google_credentials_available

    if google_credentials_available():
        try:
            from staff_absence_calendar import fetch_staff_absence_events_api

            events, api_err = fetch_staff_absence_events_api(range_start, range_end, tz)
            if events:
                return events, None, "google_api"
            if api_err and not staff_fetch_err:
                return [], api_err, "none"
        except Exception as exc:
            if not staff_fetch_err:
                return [], str(exc), "none"

    if staff_fetch_err:
        return [], f"{staff_fetch_err} {_staff_absence_setup_hint()}", "none"
    return [], None, "none"


def academic_year_bounds(reference: date) -> Tuple[date, date, int]:
    """UK-style academic year: 1 Sep – 31 Aug. Returns (start, end inclusive, start calendar year)."""
    start_year = reference.year if reference.month >= 9 else reference.year - 1
    return date(start_year, 9, 1), date(start_year + 1, 8, 31), start_year


def _all_day_event_overlaps(d0: date, end_exclusive: date, range_start: date, range_end: date) -> bool:
    if end_exclusive <= d0:
        last_day = d0
    else:
        last_day = end_exclusive - timedelta(days=1)
    return not (last_day < range_start or d0 > range_end)


def parse_ics_events_in_range(
    text: str, tz: ZoneInfo, range_start: date, range_end: date
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for block in text.split("BEGIN:VEVENT")[1:]:
        block = block.split("END:VEVENT")[0]
        summary_m = re.search(r"^SUMMARY:(.*)$", block, re.M)
        summary = (summary_m.group(1).strip() if summary_m else "").replace("\\,", ",")
        ds_m = re.search(r"^DTSTART.*$", block, re.M)
        de_m = re.search(r"^DTEND.*$", block, re.M)
        if not summary or not ds_m:
            continue
        _, ds_raw, ds_params = _parse_ics_property(ds_m.group(0))
        de_raw = ""
        de_params: Dict[str, str] = {}
        if de_m:
            _, de_raw, de_params = _parse_ics_property(de_m.group(0))
        kind, d0, t0 = _parse_ics_date_value(ds_raw, tz, ds_params)
        if kind == "date" and d0:
            _, end_d, _ = _parse_ics_date_value(de_raw, tz, de_params) if de_raw else ("", None, None)
            if end_d is None:
                end_d = d0 + timedelta(days=1)
            if not _all_day_event_overlaps(d0, end_d, range_start, range_end):
                continue
            if end_d > d0:
                last_inclusive = end_d - timedelta(days=1)
            else:
                last_inclusive = d0
            events.append(
                {
                    "title": summary,
                    "allDay": True,
                    "startDate": d0.isoformat(),
                    "endDate": last_inclusive.isoformat(),
                    "startTime": None,
                    "endTime": None,
                }
            )
        elif kind == "datetime" and t0:
            d0 = t0.astimezone(tz).date()
            if d0 < range_start or d0 > range_end:
                continue
            end_label = None
            if de_raw:
                _ek, _ed, te = _parse_ics_date_value(de_raw, tz, de_params)
                if te:
                    te = te.astimezone(tz)
                    end_label = te.strftime("%H:%M")
            t0 = t0.astimezone(tz)
            events.append(
                {
                    "title": summary,
                    "allDay": False,
                    "startDate": d0.isoformat(),
                    "endDate": d0.isoformat(),
                    "startTime": t0.strftime("%H:%M"),
                    "endTime": end_label,
                }
            )

    events.sort(
        key=lambda e: (
            e.get("startDate") or "",
            "" if e.get("allDay") else (e.get("startTime") or ""),
            e.get("title") or "",
        )
    )
    return events


def fetch_school_calendar_academic_year(
    year: Optional[int] = None, reference_day: Optional[date] = None
) -> Dict[str, Any]:
    cfg = load_isams_config_from_env()
    tz = ZoneInfo(cfg["tz"])
    ref = reference_day or today_local_date(cfg)
    if year is not None:
        if year < 2000 or year > 2100:
            return {"ok": False, "error": "Invalid academic year", "events": []}
        range_start = date(year, 9, 1)
        range_end = date(year + 1, 8, 31)
        start_year = year
    else:
        range_start, range_end, start_year = academic_year_bounds(ref)

    text, err = _fetch_school_ics_text()
    if err or not text:
        return {"ok": False, "error": err or "Empty calendar feed", "events": []}

    ics_events = parse_ics_events_in_range(text, tz, range_start, range_end)
    for ev in ics_events:
        ev["source"] = "ics"

    staff_events, staff_err, staff_via = _fetch_staff_absence_events(range_start, range_end, tz)

    sheet_events: List[Dict[str, Any]] = []
    sheet_err: Optional[str] = None
    try:
        from school_calendar_sheet import fetch_sheet_calendar_events

        sheet_events, sheet_err = fetch_sheet_calendar_events(range_start, range_end)
    except Exception as exc:
        sheet_err = str(exc)

    events = ics_events + staff_events + sheet_events
    events.sort(
        key=lambda e: (
            e.get("startDate") or "",
            "" if e.get("allDay") else (e.get("startTime") or ""),
            e.get("source") or "",
            e.get("title") or "",
        )
    )
    generated = datetime.now(tz)
    return {
        "ok": True,
        "source": "ics+staff+sheet",
        "academicYearStart": start_year,
        "label": f"{start_year}–{str(start_year + 1)[-2:]}",
        "rangeStart": range_start.isoformat(),
        "rangeEnd": range_end.isoformat(),
        "timezone": cfg["tz"],
        "generatedAt": generated.isoformat(),
        "events": events,
        "count": len(events),
        "staffAbsenceCalendar": {
            "ok": staff_err is None if _staff_absence_configured() else True,
            "error": staff_err,
            "count": len(staff_events),
            "enabled": _staff_absence_configured(),
            "configured": _staff_absence_configured(),
            "via": staff_via,
        },
        "sheetCalendar": {
            "ok": sheet_err is None,
            "error": sheet_err,
            "count": len(sheet_events),
        },
    }


def fetch_school_calendar_events(day: date, tz: ZoneInfo) -> Dict[str, Any]:
    text, err = _fetch_school_ics_text()
    if err or not text:
        return {"ok": False, "error": err or "Empty calendar feed", "events": []}

    events: List[Dict[str, Any]] = []
    for block in text.split("BEGIN:VEVENT")[1:]:
        block = block.split("END:VEVENT")[0]
        summary_m = re.search(r"^SUMMARY:(.*)$", block, re.M)
        summary = (summary_m.group(1).strip() if summary_m else "").replace("\\,", ",")
        ds_m = re.search(r"^DTSTART.*$", block, re.M)
        de_m = re.search(r"^DTEND.*$", block, re.M)
        if not summary or not ds_m:
            continue
        _, ds_raw, ds_params = _parse_ics_property(ds_m.group(0))
        de_raw = ""
        de_params: Dict[str, str] = {}
        if de_m:
            _, de_raw, de_params = _parse_ics_property(de_m.group(0))
        kind, d0, t0 = _parse_ics_date_value(ds_raw, tz, ds_params)
        if kind == "date" and d0:
            _, end_d, _ = _parse_ics_date_value(de_raw, tz, de_params) if de_raw else ("", None, None)
            if end_d is None:
                end_d = d0
            if d0 <= day < end_d:
                events.append(
                    {
                        "title": summary,
                        "allDay": True,
                        "start": d0.isoformat(),
                        "end": end_d.isoformat(),
                    }
                )
        elif kind == "datetime" and t0 and d0 == day:
            end_label = ""
            if de_raw:
                _ek, _ed, te = _parse_ics_date_value(de_raw, tz, de_params)
                if te:
                    te = te.astimezone(tz)
                    end_label = te.strftime("%H:%M")
            t0 = t0.astimezone(tz)
            events.append(
                {
                    "title": summary,
                    "allDay": False,
                    "start": t0.strftime("%H:%M"),
                    "end": end_label or None,
                }
            )

    events.sort(key=lambda e: (not e.get("allDay"), str(e.get("start") or ""), e.get("title") or ""))
    return {"ok": True, "source": "ics", "events": events, "count": len(events)}


def _parse_absence_bounds(record: Dict[str, Any]) -> Tuple[Optional[date], Optional[date]]:
    start_raw = None
    end_raw = None
    for key in ("startDate", "dateFrom", "fromDate", "start"):
        if record.get(key):
            start_raw = str(record[key])[:10]
            break
    for key in ("endDate", "dateTo", "toDate", "finish", "end"):
        if record.get(key):
            end_raw = str(record[key])[:10]
            break
    try:
        start_d = datetime.strptime(start_raw, "%Y-%m-%d").date() if start_raw else None
    except ValueError:
        start_d = None
    try:
        end_d = datetime.strptime(end_raw, "%Y-%m-%d").date() if end_raw else start_d
    except ValueError:
        end_d = start_d
    return start_d, end_d


def _absence_overlaps_day(record: Dict[str, Any], day: date) -> bool:
    start_d, end_d = _parse_absence_bounds(record)
    if not start_d:
        return False
    if not end_d:
        end_d = start_d
    return start_d <= day <= end_d


def _fetch_employee_absences(
    cfg: Dict[str, str], token: str, employee_id: Any
) -> List[Dict[str, Any]]:
    path = f"/api/humanresources/employees/{employee_id}/absences"
    body = _get_json_optional(cfg, token, path)
    if not body:
        return []
    items = body.get("absences")
    if isinstance(items, list):
        return items
    return []


def fetch_staff_absences_today(cfg: Dict[str, str], token: str, day: date) -> Dict[str, Any]:
    max_employees = int(_env("TODAY_STAFF_ABSENCE_MAX_EMPLOYEES", "250") or "250")
    page_size = min(500, max(50, max_employees))
    employees: List[Dict[str, Any]] = []
    page = 1
    try:
        while len(employees) < max_employees:
            body = _get_json(
                cfg,
                token,
                f"/api/humanresources/employees?page={page}&pageSize={page_size}",
            )
            chunk = list(body.get("employees") or [])
            employees.extend(chunk)
            total_pages = int(body.get("totalPages") or 1)
            if page >= total_pages or not chunk:
                break
            page += 1
    except Exception as exc:
        return {"ok": False, "error": str(exc), "absentToday": []}

    employees = employees[:max_employees]
    absent: List[Dict[str, Any]] = []
    workers = min(12, max(4, int(_env("TODAY_STAFF_ABSENCE_WORKERS", "8") or "8")))

    def job(emp: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        eid = emp.get("id")
        if eid is None:
            return None
        try:
            absences = _fetch_employee_absences(cfg, token, eid)
        except Exception:
            return None
        todays = [a for a in absences if isinstance(a, dict) and _absence_overlaps_day(a, day)]
        if not todays:
            return None
        name = str(emp.get("fullName") or "").strip() or " ".join(
            filter(None, [str(emp.get("forename") or "").strip(), str(emp.get("surname") or "").strip()])
        )
        absence_rows = []
        for a in todays[:5]:
            s_d, e_d = _parse_absence_bounds(a)
            absence_rows.append(
                {
                    "id": a.get("id"),
                    "typeId": a.get("absenceTypeId") or a.get("typeId"),
                    "startDate": s_d.isoformat() if s_d else None,
                    "endDate": e_d.isoformat() if e_d else None,
                    "notes": a.get("notes") or a.get("comment"),
                }
            )
        return {
            "employeeId": eid,
            "name": name or f"Employee {eid}",
            "absences": absence_rows,
        }

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(job, emp) for emp in employees]
        for fut in as_completed(futures):
            row = fut.result()
            if row:
                absent.append(row)

    absent.sort(key=lambda r: (r.get("name") or "").lower())
    return {
        "ok": True,
        "employeesScanned": len(employees),
        "absentToday": absent,
        "count": len(absent),
    }


def _paginate_list(
    cfg: Dict[str, str], token: str, path: str, list_key: str, *, page_size: int = 500
) -> List[Dict[str, Any]]:
    page = 1
    out: List[Dict[str, Any]] = []
    while True:
        sep = "&" if "?" in path else "?"
        body = _get_json(cfg, token, f"{path}{sep}page={page}&pageSize={page_size}")
        chunk = list(body.get(list_key) or [])
        out.extend(chunk)
        total_pages = int(body.get("totalPages") or 1)
        if page >= total_pages or not chunk:
            break
        page += 1
    return out


def _student_display_map(cfg: Dict[str, str], token: str) -> Dict[str, Dict[str, str]]:
    """Map iSAMS schoolId → display name / year for registration lists."""
    out: Dict[str, Dict[str, str]] = {}
    try:
        for stu in isams_get_students(cfg, token):
            sid = school_id_raw(stu.get("schoolId"))
            if not sid:
                continue
            preferred = str(stu.get("preferredName") or stu.get("forename") or "").strip()
            surname = str(stu.get("surname") or "").strip()
            full = str(stu.get("fullName") or "").strip()
            name = " ".join(filter(None, [preferred, surname])).strip() or full or sid
            year = str(stu.get("yearGroupName") or stu.get("yearGroup") or "").strip()
            out[sid] = {"name": name, "yearGroup": year}
    except Exception:
        pass
    return out


def _enrich_pupil_rows(
    pupils: List[Dict[str, Any]], student_map: Dict[str, Dict[str, str]]
) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for row in pupils:
        sid = str(row.get("schoolId") or "")
        info = student_map.get(sid) or {}
        enriched.append(
            {
                **row,
                "name": info.get("name") or sid,
                "yearGroup": info.get("yearGroup") or "",
            }
        )
    enriched.sort(key=lambda r: (str(r.get("name") or "").lower(), r.get("schoolId") or ""))
    return enriched


def fetch_registration_today(cfg: Dict[str, str], token: str, day: date) -> Dict[str, Any]:
    tz = ZoneInfo(cfg["tz"])
    day_iso = day.isoformat()
    current_hhmm = get_current_time_hhmm(cfg)
    try:
        periods = get_today_registration_periods(cfg, token)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    period_summaries: List[Dict[str, Any]] = []
    pupils_oos: Dict[str, Dict[str, Any]] = {}
    pupils_absent: Dict[str, Dict[str, Any]] = {}

    for period in periods:
        if period_day_iso(cfg, period) != day_iso:
            continue
        pid = period.get("id")
        fname = str(period.get("friendlyName") or "")
        label = registration_period_ui_label(fname)
        started = period_has_started(period, cfg, current_hhmm)
        summary = {
            "id": pid,
            "name": fname,
            "label": label,
            "start": period.get("start"),
            "finish": period.get("finish"),
            "started": started,
            "studentCount": period.get("numberOfStudents"),
        }
        if pid is None:
            period_summaries.append(summary)
            continue
        rows, missing = list_register_statuses(cfg, token, pid)
        summary["registerReady"] = not missing
        summary["registerRows"] = len(rows) if not missing else 0
        period_summaries.append(summary)
        if missing or not started:
            continue
        for row in rows:
            sid = str(row.get("schoolId") or "").strip().lstrip("'")
            if not sid:
                continue
            if row.get("isOutOfSchool") is True:
                pupils_oos.setdefault(
                    sid,
                    {"schoolId": sid, "periods": []},
                )["periods"].append(label)
            sym = registration_status_symbol_from_body(row)
            if sym == "❌":
                pupils_absent.setdefault(
                    sid,
                    {"schoolId": sid, "periods": []},
                )
                if label not in pupils_absent[sid]["periods"]:
                    pupils_absent[sid]["periods"].append(label)

    student_map = _student_display_map(cfg, token)

    def _sorted_pupils(d: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        return _enrich_pupil_rows(list(d.values()), student_map)

    return {
        "ok": True,
        "currentTime": current_hhmm,
        "periods": period_summaries,
        "pupilsOutOfSchool": _sorted_pupils(pupils_oos),
        "pupilsMarkedAbsent": _sorted_pupils(pupils_absent),
        "pupilsOutOfSchoolCount": len(pupils_oos),
        "pupilsMarkedAbsentCount": len(pupils_absent),
    }


def fetch_registration_extras(cfg: Dict[str, str], token: str, day: date) -> Dict[str, Any]:
    day_iso = day.isoformat()
    out: Dict[str, Any] = {"ok": True}
    try:
        nature = _paginate_list(
            cfg,
            token,
            f"/api/registration/natureofabsence?registrationFrom={day_iso}&registrationTo={day_iso}",
            "natureOfAbsences",
            page_size=200,
        )
        out["natureOfAbsence"] = nature
        out["natureOfAbsenceCount"] = len(nature)
    except Exception as exc:
        out["natureOfAbsenceError"] = str(exc)
    try:
        changes = _paginate_list(
            cfg,
            token,
            f"/api/registration/codechanges?changeDateStart={day_iso}&changeDateEnd={day_iso}",
            "attendanceCodeReasonForChanges",
            page_size=200,
        )
        out["codeChanges"] = changes
        out["codeChangesCount"] = len(changes)
    except Exception as exc:
        out["codeChangesError"] = str(exc)
    try:
        off = _paginate_list(cfg, token, "/api/registration/offgames", "registrationOffGames", page_size=200)
        # Filter to records touching today when date fields exist
        filtered = []
        for row in off:
            if not isinstance(row, dict):
                continue
            start_d, end_d = _parse_absence_bounds(row)
            if start_d and end_d and start_d <= day <= end_d:
                filtered.append(row)
        out["offGames"] = filtered[:100]
        out["offGamesCount"] = len(filtered)
    except Exception as exc:
        out["offGamesError"] = str(exc)
    return out


def fetch_cover_batch(day: date) -> Dict[str, Any]:
    if _env("TODAY_INCLUDE_ISAMS_COVER", "0").lower() in ("0", "false", "no"):
        return {"ok": False, "skipped": True, "reason": "TODAY_INCLUDE_ISAMS_COVER=0"}
    try:
        from isams_batch_filters import batch_post_filtered, build_filters_xml, registration_manager_keys
    except ImportError as exc:
        return {"ok": False, "error": str(exc)}

    day_iso = day.isoformat()
    try:
        xml = build_filters_xml(day_iso, day_iso, style="full")
        data = batch_post_filtered(xml, timeout=120)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    isams = data.get("iSAMS") if isinstance(data, dict) else {}
    cover = isams.get("CoverManager") if isinstance(isams, dict) else None
    reg_keys = registration_manager_keys(data) if isinstance(data, dict) else []
    cover_summary: Dict[str, Any] = {}
    if isinstance(cover, dict):
        for key, val in cover.items():
            if isinstance(val, list):
                cover_summary[key] = {"count": len(val), "sample": val[:5]}
            elif isinstance(val, dict):
                cover_summary[key] = {"keys": list(val.keys())[:12], "sample": str(val)[:500]}
            else:
                cover_summary[key] = val
    return {
        "ok": True,
        "coverManager": cover_summary,
        "registrationManagerKeys": reg_keys,
    }


def fetch_sign_in_snapshot() -> Dict[str, Any]:
    if _env("TODAY_INCLUDE_SIGN_IN", "1").lower() in ("0", "false", "no"):
        return {"ok": False, "skipped": True}
    try:
        from sign_in_out import today_whos_in_out_snapshot

        url = _env("SUPABASE_URL")
        key = _env("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            return {"ok": False, "error": "Supabase not configured"}
        from supabase import create_client

        sb = create_client(url, key)
        snap = today_whos_in_out_snapshot(sb)
        return {
            "ok": True,
            "inCount": snap.get("inCount"),
            "outCount": snap.get("outCount"),
            "updatedAt": snap.get("updatedAt"),
            "in": snap.get("in") or [],
            "out": snap.get("out") or [],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def build_today_snapshot(day: Optional[str] = None) -> Dict[str, Any]:
    cfg = load_isams_config_from_env()
    tz = ZoneInfo(cfg["tz"])
    target = _parse_day(day, cfg)
    generated = datetime.now(tz)

    payload: Dict[str, Any] = {
        "ok": True,
        "date": target.isoformat(),
        "timezone": cfg["tz"],
        "generatedAt": generated.isoformat(),
        "calendar": {},
        "registration": {},
        "staffAbsences": {},
    }

    payload["calendar"] = fetch_school_calendar_events(target, tz)

    try:
        token = isams_get_token(cfg)
    except Exception as exc:
        payload["ok"] = True
        payload["isamsAuthError"] = str(exc)
        payload["registration"] = _section_err("iSAMS auth", exc)
        payload["staffAbsences"] = {"ok": False, "skipped": True}
        return payload

    payload["registration"] = fetch_registration_today(cfg, token, target)
    payload["staffAbsences"] = fetch_staff_absences_today(cfg, token, target)

    if _env("TODAY_INCLUDE_REGISTRATION_EXTRAS", "0").lower() in ("1", "true", "yes"):
        payload["registrationExtras"] = fetch_registration_extras(cfg, token, target)
    if _env("TODAY_INCLUDE_SIGN_IN", "0").lower() in ("1", "true", "yes"):
        payload["signIn"] = fetch_sign_in_snapshot()
    if _env("TODAY_INCLUDE_ISAMS_COVER", "0").lower() in ("1", "true", "yes"):
        payload["cover"] = fetch_cover_batch(target)

    if not payload["calendar"].get("ok"):
        payload["partial"] = True
    for key in ("registration", "staffAbsences"):
        sec = payload.get(key) or {}
        if sec.get("ok") is False and not sec.get("skipped"):
            payload["partial"] = True

    return payload
