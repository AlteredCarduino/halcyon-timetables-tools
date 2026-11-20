"""
Sign In/Out via Supabase: halcyon_community lookup + halcyon_signInOutLogs append.
Mirrors GoogleAppsScript/SignInApp.gs recordSignInOutEvent_ behaviour.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

_CATEGORY_ORDER = ("staff", "sports", "student", "parent", "none")


def _community_table() -> str:
    return os.environ.get("SUPABASE_STAFF_TABLE", "halcyon_community").strip()


def _signin_log_table() -> str:
    return os.environ.get("SUPABASE_SIGNIN_LOG_TABLE", "halcyon_signInOutLogs").strip()


def _rejected_scan_table() -> str:
    return os.environ.get("SUPABASE_REJECTED_SCAN_TABLE", "halcyon_rejected_scans").strip()


def _visitor_in_table() -> str:
    return os.environ.get("SUPABASE_VISITOR_IN_TABLE", "halcyon_visitor_in").strip()


def _visitor_expected_table() -> str:
    return os.environ.get("SUPABASE_VISITOR_EXPECTED_TABLE", "halcyon_visitor_expected").strip()


def _registration_table() -> str:
    return os.environ.get("SUPABASE_REGISTRATION_TABLE", "halcyon_registration_data").strip()


def _school_tz() -> ZoneInfo:
    name = os.environ.get("SCHOOL_TIMEZONE", "Europe/London").strip() or "Europe/London"
    for key in (name, "Europe/London"):
        try:
            return ZoneInfo(key)
        except Exception:
            continue
    return ZoneInfo("UTC")


def _norm_email(email: str) -> str:
    return (email or "").strip().lower()


_NAME_SUFFIXES = frozenset({"ii", "iii", "iv", "jr", "sr", "jnr", "snr", "junior", "senior"})


def _name_token_is_suffix(token: str) -> bool:
    return token.casefold().rstrip(".") in _NAME_SUFFIXES


def _surname_from_fullname(fullname: str, preferred: str) -> str:
    """Whole surname from an iSAMS-style full name.

    iSAMS full names are usually forenames then the full surname, or
    ``Surname, Forenames``. The surname is every word after the preferred
    name, not only the last word. ``Osta Merdan`` stays ``Osta Merdan``.
    When the preferred name is a nickname and is not in the full name, a
    trailing suffix such as ``II`` stays with the word before it, so
    ``Raymond Smith II`` still yields ``Smith II``.
    """
    fullname = (fullname or "").strip()
    if not fullname:
        return ""
    if "," in fullname:
        return fullname.split(",", 1)[0].strip()
    parts = [p for p in fullname.split() if p]
    if len(parts) <= 1:
        return ""
    pref_parts = [p for p in (preferred or "").split() if p]
    if pref_parts:
        lower_parts = [p.casefold() for p in parts]
        lower_pref = [p.casefold() for p in pref_parts]
        width = len(lower_pref)
        for i in range(len(parts) - width + 1):
            if lower_parts[i : i + width] == lower_pref:
                return " ".join(parts[i + width :])
    if len(parts) >= 2 and _name_token_is_suffix(parts[-1]):
        return " ".join(parts[-2:])
    return parts[-1]


def display_name_preferred_surname(
    preferred: str, fullname: str = "", *, surname: str = "", fallback: str = ""
) -> str:
    """iSAMS preferredName + surname.

    When ``surname`` is the iSAMS surname field it is used as written.
    Otherwise the surname is taken from the full name.
    """
    preferred = (preferred or "").strip()
    fullname = (fullname or "").strip()
    surname = (surname or "").strip()
    if not surname and preferred and fullname:
        surname = _surname_from_fullname(fullname, preferred)
    if preferred and surname:
        if preferred.casefold() == surname.casefold() or preferred.casefold().endswith(
            " " + surname.casefold()
        ):
            return preferred
        return f"{preferred} {surname}".strip()
    return preferred or surname or fullname or (fallback or "").strip()


_ISAMS_NAME_TTL_SECONDS = 10 * 60
_isams_name_lock = threading.Lock()
_isams_name_cache: Dict[str, Any] = {"at": 0.0, "by_email": None}


def isams_names_by_email() -> Dict[str, Dict[str, str]]:
    """Cached iSAMS preferredName and surname, keyed by school email."""
    now = time.time()
    with _isams_name_lock:
        cached = _isams_name_cache.get("by_email")
        if isinstance(cached, dict) and now - float(_isams_name_cache.get("at") or 0) < _ISAMS_NAME_TTL_SECONDS:
            return cached
    try:
        from isams_registration import (
            isams_get_token,
            isams_preferred_surname_by_email,
            load_isams_config_from_env,
        )

        cfg = load_isams_config_from_env()
        names = isams_preferred_surname_by_email(cfg, isams_get_token(cfg))
    except Exception:
        with _isams_name_lock:
            cached = _isams_name_cache.get("by_email")
            return cached if isinstance(cached, dict) else {}
    with _isams_name_lock:
        _isams_name_cache["at"] = time.time()
        _isams_name_cache["by_email"] = names
    return names


def _emails_from_field(raw: str) -> List[str]:
    """
    Parse one community `email` column value into individual addresses.
    Handles a single email or delimited lists such as ``a@x.com;b@x.com``.
    """
    out: List[str] = []
    seen: set[str] = set()
    text = str(raw or "").replace(",", ";")
    for chunk in text.split(";"):
        em = _norm_email(chunk)
        if em and "@" in em and em not in seen:
            seen.add(em)
            out.append(em)
    return out


def _email_in_column(column_value: str, em: str) -> bool:
    """True when normalised ``em`` is one of the addresses stored in ``column_value``."""
    if not em:
        return False
    field = str(column_value or "").strip()
    if not field:
        return False
    if _norm_email(field) == em:
        return True
    return em in _emails_from_field(field)


def _row_has_email(row: Dict[str, Any], em: str) -> bool:
    return _email_in_column(str(row.get("email") or ""), em)


def _role_to_category(role: str) -> str:
    r = (role or "").strip().lower()
    if not r:
        return "staff"
    if r in ("student", "pupil") or r.startswith("student"):
        return "student"
    if r in ("parent", "guardian") or "parent" in r:
        return "parent"
    if "sport" in r:
        return "sports"
    return "staff"


def _display_role_value(role: str, category: str) -> str:
    r = (role or "").strip()
    if r:
        return r
    if category == "student":
        return "Student"
    if category == "parent":
        return "Parents"
    if category == "sports":
        return "Sports"
    if category == "none":
        return "visitor"
    return "Staff"


def _pick_person_row(rows: List[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    """Choose one community row using GAS priority: staff, sports, student, parent."""
    if not rows:
        return "none", {}
    best_cat = "none"
    best_row: Dict[str, Any] = {}
    best_rank = len(_CATEGORY_ORDER)
    for row in rows:
        cat = _role_to_category(str(row.get("role") or ""))
        rank = _CATEGORY_ORDER.index(cat) if cat in _CATEGORY_ORDER else len(_CATEGORY_ORDER)
        if rank < best_rank:
            best_rank = rank
            best_cat = cat
            best_row = row
    return best_cat, best_row


def _rows_matching_email(rows: List[Dict[str, Any]], em: str) -> List[Dict[str, Any]]:
    matched: List[Dict[str, Any]] = []
    for row in rows:
        if _row_has_email(row, em):
            matched.append(row)
    return matched


def _fetch_community_rows_for_email(sb: Any, em: str) -> List[Dict[str, Any]]:
    """Match halcyon_community — column may be ``email`` or ``email;email`` (token match)."""
    table = _community_table()
    cols = "school_id,fullname,email,preferred_name,role"
    attempts: List[Any] = [
        lambda: sb.table(table).select(cols).eq("email", em).execute(),
        lambda: sb.table(table).select(cols).ilike("email", f"%{em}%").limit(50).execute(),
    ]
    if "@" in em:
        _local, domain = em.split("@", 1)
        if domain:
            attempts.append(
                lambda d=domain: sb.table(table)
                .select(cols)
                .ilike("email", f"%@{d}")
                .limit(3000)
                .execute()
            )
    for attempt in attempts:
        try:
            res = attempt()
        except Exception:
            continue
        matched = _rows_matching_email(res.data or [], em)
        if matched:
            return matched
    return []


def lookup_person_in_community(sb: Any, email: str) -> Dict[str, Any]:
    em = _norm_email(email)
    if not em or "@" not in em:
        return {
            "ok": False,
            "error": "Invalid email.",
            "category": "none",
            "preferredName": "",
            "fullname": "",
            "role": "",
            "email": em,
        }
    rows = _fetch_community_rows_for_email(sb, em)
    category, row = _pick_person_row(rows)
    if not row:
        return {
            "ok": False,
            "error": "Email not found in community directory.",
            "category": "none",
            "preferredName": "",
            "fullname": "",
            "role": "",
            "email": em,
        }
    fullname = str(row.get("fullname") or "").strip()
    preferred = str(row.get("preferred_name") or "").strip() or fullname
    role_raw = str(row.get("role") or "").strip()
    role_value = _display_role_value(role_raw, category)
    return {
        "ok": True,
        "category": category,
        "preferredName": preferred,
        "fullname": fullname,
        "role": role_value,
        "email": em,
        "schoolId": str(row.get("school_id") or "").strip(),
    }


def _late_yes_no(now: datetime) -> str:
    t = now.hour * 3600 + now.minute * 60 + now.second
    cut = 8 * 3600 + 46 * 60
    return "Yes" if t > cut else "No"


def _fmt_date_time(now: datetime) -> Tuple[str, str]:
    return now.strftime("%d/%m/%Y"), now.strftime("%H:%M:%S")


def _pg_date(value: str, *, fallback: Optional[datetime] = None) -> str:
    """ISO YYYY-MM-DD for Postgres ``date`` columns (sign-in logs may still use dd/mm/yyyy text)."""
    s = (value or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date().isoformat()
        except ValueError:
            pass
    for fmt in ("%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date().isoformat()
        except ValueError:
            continue
    ref = fallback or datetime.now(_school_tz())
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=_school_tz())
    return ref.astimezone(_school_tz()).date().isoformat()


def _last_status_today(sb: Any, em: str, date_str: str) -> str:
    del date_str  # legacy sheet date; logs use created_at when available
    table = _signin_log_table()
    tz = _school_tz()
    start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    try:
        res = (
            sb.table(table)
            .select("status,created_at")
            .eq("email", em)
            .gte("created_at", start.isoformat())
            .lt("created_at", end.isoformat())
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if rows:
            return str(rows[0].get("status") or "").strip()
    except Exception:
        pass
    try:
        res = (
            sb.table(table)
            .select("status,time,date,created_at")
            .eq("email", em)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        rows = res.data or []
        today = datetime.now(tz).strftime("%d/%m/%Y")
        for row in rows:
            if str(row.get("date") or "").strip() == today:
                return str(row.get("status") or "").strip()
    except Exception:
        return ""
    return ""


def today_sign_in_status_map(sb: Any) -> Dict[str, bool]:
    """
    Latest sign-in/out status per email for today (Europe/London day).
    True = currently In; False = Out or no log today.
    """
    table = _signin_log_table()
    tz = _school_tz()
    start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    today_sheet = start.strftime("%d/%m/%Y")
    status_by_email: Dict[str, str] = {}

    def _apply_row(row: Dict[str, Any]) -> None:
        em = _norm_email(str(row.get("email") or ""))
        if not em:
            return
        st = str(row.get("status") or "").strip()
        if not st:
            return
        # Keep first seen when iterating newest-first
        if em not in status_by_email:
            status_by_email[em] = st

    try:
        res = (
            sb.table(table)
            .select("email,status,created_at")
            .gte("created_at", start.isoformat())
            .lt("created_at", end.isoformat())
            .order("created_at", desc=True)
            .execute()
        )
        for row in res.data or []:
            _apply_row(row)
    except Exception:
        pass

    if not status_by_email:
        try:
            res = (
                sb.table(table)
                .select("email,status,date,time,created_at")
                .order("created_at", desc=True)
                .limit(5000)
                .execute()
            )
            for row in res.data or []:
                if str(row.get("date") or "").strip() != today_sheet:
                    continue
                _apply_row(row)
        except Exception:
            pass

    return {em: st.lower() == "in" for em, st in status_by_email.items()}


def _format_log_time(row: Dict[str, Any], tz: ZoneInfo) -> str:
    raw = row.get("scanned_at") or row.get("created_at")
    if raw:
        try:
            s = str(raw).replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            else:
                dt = dt.astimezone(tz)
            return dt.strftime("%H:%M")
        except Exception:
            pass
    t = str(row.get("time") or "").strip()
    if t:
        return t[:5] if len(t) >= 5 else t
    return "—"


def _student_years_by_email(sb: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        res = sb.table(_registration_table()).select("email,year").execute()
        for row in res.data or []:
            em = _norm_email(str(row.get("email") or ""))
            if not em:
                continue
            y = row.get("year")
            out[em] = str(y).strip() if y is not None and str(y).strip() else "—"
    except Exception:
        pass
    return out


def _grade_for_person(em: str, role: str, years: Dict[str, str]) -> str:
    cat = _role_to_category(role)
    if cat != "student":
        return "—"
    return years.get(em, "—")


def _row_is_today(
    row: Dict[str, Any],
    start: datetime,
    end: datetime,
    today_sheet: str,
    tz: ZoneInfo,
) -> bool:
    for key in ("scanned_at", "created_at"):
        raw = row.get(key)
        if not raw:
            continue
        try:
            s = str(raw).replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            else:
                dt = dt.astimezone(tz)
            if start <= dt < end:
                return True
        except Exception:
            continue
    if str(row.get("date") or "").strip() == today_sheet:
        return True
    return False


def _fetch_today_log_rows(sb: Any) -> List[Dict[str, Any]]:
    """Today's sign-in/out log rows (newest first). Uses only columns that exist on the table."""
    table = _signin_log_table()
    tz = _school_tz()
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    today_sheet = start.strftime("%d/%m/%Y")

    col_sets = (
        "email,name,role,status,scanned_at,created_at",
        "email,name,role,status,scanned_at",
        "email,status,scanned_at,created_at",
        "email,status,created_at",
    )

    for cols in col_sets:
        try:
            res = (
                sb.table(table)
                .select(cols)
                .gte("created_at", start.isoformat())
                .lt("created_at", end.isoformat())
                .order("created_at", desc=True)
                .execute()
            )
            if res.data:
                return list(res.data)
        except Exception:
            continue

    for cols in col_sets:
        try:
            res = (
                sb.table(table)
                .select(cols)
                .order("created_at", desc=True)
                .limit(3000)
                .execute()
            )
            rows = res.data or []
            if rows:
                return [r for r in rows if _row_is_today(r, start, end, today_sheet, tz)]
        except Exception:
            continue
    return []


def _today_visitor_in_by_email(sb: Any) -> Dict[str, Dict[str, Any]]:
    """Latest halcyon_visitor_in row per email for today (Europe/London)."""
    tz = _school_tz()
    today_iso = datetime.now(tz).date().isoformat()
    out: Dict[str, Dict[str, Any]] = {}
    try:
        res = (
            sb.table(_visitor_in_table())
            .select("id,email,additional_visitors,signed_in_at")
            .eq("sign_date", today_iso)
            .order("signed_in_at", desc=True)
            .execute()
        )
    except Exception:
        return out
    for row in res.data or []:
        em = _norm_email(str(row.get("email") or ""))
        if not em or em in out:
            continue
        try:
            extra = int(row.get("additional_visitors") or 0)
        except (TypeError, ValueError):
            extra = 0
        out[em] = {
            "visitorInId": row.get("id"),
            "additionalVisitors": max(0, extra),
        }
    return out


def _format_expected_time(raw: Any) -> str:
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    if len(s) >= 5 and s[2] == ":":
        return s[:5]
    return s


def _visitor_expected_api_row(row: Dict[str, Any]) -> Dict[str, Any]:
    first = str(row.get("first_name") or "").strip()
    surname = str(row.get("surname") or "").strip()
    full = str(row.get("full_name") or "").strip()
    if not full:
        full = " ".join(p for p in (first, surname) if p).strip()
    try:
        extra = int(row.get("additional_visitors") or 0)
    except (TypeError, ValueError):
        extra = 0
    return {
        "id": row.get("id"),
        "email": str(row.get("email") or "").strip(),
        "firstName": first,
        "surname": surname,
        "name": full or str(row.get("email") or "").strip(),
        "meetWith": str(row.get("meet_with") or "").strip(),
        "reasonForVisit": str(row.get("reason_for_visit") or "").strip(),
        "additionalVisitors": max(0, extra),
        "companyName": str(row.get("company_name") or "").strip(),
        "staffNotes": str(row.get("staff_notes") or "").strip(),
        "visitDate": str(row.get("visit_date") or ""),
        "expectedTime": _format_expected_time(row.get("expected_time")),
        "status": str(row.get("status") or "expected").strip(),
    }


def _parse_expected_time(value: Any) -> Optional[str]:
    """Return HH:MM:SS for Postgres time or None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    parts = s.split(":")
    if len(parts) >= 2:
        try:
            h = int(parts[0])
            m = int(parts[1])
            if 0 <= h <= 23 and 0 <= m <= 59:
                return f"{h:02d}:{m:02d}:00"
        except (TypeError, ValueError):
            return None
    return None


def list_visitor_expected_pending_today(sb: Any) -> List[Dict[str, Any]]:
    """Expected visitors for today who have not been registered yet."""
    tz = _school_tz()
    today_iso = datetime.now(tz).date().isoformat()
    try:
        res = (
            sb.table(_visitor_expected_table())
            .select("*")
            .eq("visit_date", today_iso)
            .eq("status", "expected")
            .order("expected_time", desc=False)
            .execute()
        )
    except Exception:
        return []
    rows = res.data or []
    rows.sort(
        key=lambda r: (
            str(r.get("expected_time") or "99:99:99"),
            str(r.get("created_at") or ""),
        )
    )
    return [_visitor_expected_api_row(r) for r in rows]


def create_visitor_expected(sb: Any, body: Dict[str, Any]) -> Dict[str, Any]:
    """Staff pre-registration for a visitor (not a sign-in yet)."""
    em = _norm_email(str(body.get("email") or ""))
    if not em or "@" not in em:
        return {"ok": False, "error": "Valid email is required."}
    first = str(body.get("firstName") or body.get("first_name") or "").strip()
    surname = str(body.get("surname") or body.get("lastName") or body.get("last_name") or "").strip()
    if not first or not surname:
        return {"ok": False, "error": "First name and surname are required."}

    tz = _school_tz()
    now = datetime.now(tz)
    visit_raw = str(body.get("visitDate") or body.get("visit_date") or "").strip()
    visit_iso = _pg_date(visit_raw, fallback=now) if visit_raw else now.date().isoformat()

    display = str(body.get("displayName") or body.get("display_name") or "").strip()
    full = display or " ".join(p for p in (first, surname) if p).strip()
    meet = str(body.get("meetWith") or body.get("meet_with") or "").strip()
    reason = str(
        body.get("reasonForVisit") or body.get("reason_for_visit") or body.get("reason") or ""
    ).strip()
    company = str(body.get("companyName") or body.get("company_name") or "").strip()
    staff_notes = str(body.get("staffNotes") or body.get("staff_notes") or "").strip()[:2000]
    created_by = str(body.get("createdBy") or body.get("created_by") or "").strip()[:320]
    add_raw = body.get("additionalVisitors", body.get("additional_visitors", 0))
    try:
        additional = int(add_raw) if add_raw is not None and str(add_raw).strip() != "" else 0
    except (TypeError, ValueError):
        additional = 0
    additional = max(0, min(99, additional))

    expected_time = _parse_expected_time(
        body.get("expectedTime") or body.get("expected_time")
    )

    row: Dict[str, Any] = {
        "visit_date": visit_iso,
        "email": em,
        "first_name": first,
        "surname": surname,
        "full_name": full,
        "meet_with": meet,
        "reason_for_visit": reason,
        "additional_visitors": additional,
        "company_name": company,
        "staff_notes": staff_notes,
        "status": "expected",
        "created_by": created_by,
    }
    if expected_time:
        row["expected_time"] = expected_time

    try:
        res = sb.table(_visitor_expected_table()).insert(row).execute()
    except Exception as exc:
        msg = str(exc)
        if "does not exist" in msg or "42P01" in msg:
            return {
                "ok": False,
                "error": "Expected visitors table missing. Run sql/018_halcyon_visitor_expected.sql in Supabase.",
            }
        return {"ok": False, "error": msg}

    inserted = (res.data or [{}])[0] if res.data else {**row, "id": None}
    api_row = _visitor_expected_api_row({**row, **inserted})
    return {"ok": True, "expected": api_row}


def _fetch_visitor_expected_by_id(sb: Any, expected_id: int) -> Optional[Dict[str, Any]]:
    try:
        res = (
            sb.table(_visitor_expected_table())
            .select("*")
            .eq("id", expected_id)
            .limit(1)
            .execute()
        )
    except Exception:
        return None
    rows = res.data or []
    return rows[0] if rows else None


def cancel_visitor_expected(sb: Any, expected_id: int) -> Dict[str, Any]:
    row = _fetch_visitor_expected_by_id(sb, expected_id)
    if not row:
        return {"ok": False, "error": "Pre-registration not found."}
    if str(row.get("status") or "") != "expected":
        return {"ok": False, "error": "That pre-registration is no longer pending."}
    try:
        sb.table(_visitor_expected_table()).update({"status": "cancelled"}).eq("id", expected_id).execute()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "id": expected_id, "status": "cancelled"}


def register_visitor_expected(sb: Any, expected_id: int) -> Dict[str, Any]:
    """Sign a pre-registered visitor in and mark the expected row checked in."""
    row = _fetch_visitor_expected_by_id(sb, expected_id)
    if not row:
        return {"ok": False, "error": "Pre-registration not found."}
    if str(row.get("status") or "") != "expected":
        return {"ok": False, "error": "That visitor is already registered or removed."}

    tz = _school_tz()
    today_iso = datetime.now(tz).date().isoformat()
    visit_iso = str(row.get("visit_date") or "")
    if visit_iso != today_iso:
        return {
            "ok": False,
            "error": "Only today's expected visitors can be registered from Who's In.",
        }

    em = str(row.get("email") or "").strip()
    first = str(row.get("first_name") or "").strip()
    surname = str(row.get("surname") or "").strip()
    full = str(row.get("full_name") or "").strip() or " ".join(p for p in (first, surname) if p).strip()
    try:
        additional = int(row.get("additional_visitors") or 0)
    except (TypeError, ValueError):
        additional = 0

    sign_body: Dict[str, Any] = {
        "email": em,
        "method": "manual",
        "forceStatus": "In",
        "displayName": full,
        "firstName": first,
        "surname": surname,
        "meetWith": str(row.get("meet_with") or "").strip(),
        "reasonForVisit": str(row.get("reason_for_visit") or "").strip(),
        "additionalVisitors": max(0, additional),
        "companyName": str(row.get("company_name") or "").strip(),
        "roleHint": "visitor",
    }

    result = record_sign_in_out(sb, sign_body)
    if not result.get("ok"):
        return result

    now = datetime.now(tz)
    try:
        sb.table(_visitor_expected_table()).update(
            {
                "status": "checked_in",
                "checked_in_at": now.isoformat(),
            }
        ).eq("id", expected_id).execute()
    except Exception:
        pass

    return {
        "ok": True,
        "expectedId": expected_id,
        "signIn": result,
        "skipped": bool(result.get("skipped")),
    }


def update_visitor_additional_visitors(sb: Any, email: str, additional: int) -> Dict[str, Any]:
    """Set additional_visitors on today's visitor_in row (Who's In correction)."""
    em = _norm_email(email)
    if not em or "@" not in em:
        return {"ok": False, "error": "Invalid email."}
    try:
        count = int(additional)
    except (TypeError, ValueError):
        return {"ok": False, "error": "additionalVisitors must be a number."}
    if count < 0:
        count = 0
    if count > 99:
        count = 99

    if _last_status_today(sb, em, "").lower() != "in":
        return {"ok": False, "error": "That person is not signed in today."}

    role = ""
    person = lookup_person_in_community(sb, em)
    if person.get("ok"):
        role = str(person.get("role") or "").strip()
    for row in _fetch_today_log_rows(sb):
        if _norm_email(str(row.get("email") or "")) == em:
            log_role = str(row.get("role") or "").strip()
            if log_role:
                role = log_role
            break
    if str(role or "").strip().lower() != "visitor":
        return {"ok": False, "error": "Additional guests apply to visitors only."}

    tz = _school_tz()
    now = datetime.now(tz)
    today_iso = _pg_date("", fallback=now)

    row_id: Optional[int] = None
    try:
        res = (
            sb.table(_visitor_in_table())
            .select("id,email,signed_in_at")
            .eq("sign_date", today_iso)
            .order("signed_in_at", desc=True)
            .execute()
        )
        for row in res.data or []:
            if _norm_email(str(row.get("email") or "")) == em:
                row_id = row.get("id")
                break
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    try:
        if row_id is not None:
            sb.table(_visitor_in_table()).update({"additional_visitors": count}).eq("id", row_id).execute()
        else:
            sb.table(_visitor_in_table()).insert(
                {
                    "email": em,
                    "first_name": "",
                    "surname": "",
                    "full_name": "",
                    "meet_with": "",
                    "reason_for_visit": "",
                    "additional_visitors": count,
                    "company_name": "",
                    "mailing_list_opt_in": False,
                    "sign_date": today_iso,
                    "signed_in_at": now.isoformat(),
                }
            ).execute()
    except Exception as exc:
        msg = str(exc)
        if "does not exist" in msg or "42P01" in msg:
            return {
                "ok": False,
                "error": "Visitor In table missing. Run sql/016_halcyon_visitor_in.sql in Supabase.",
            }
        return {"ok": False, "error": msg}

    return {"ok": True, "email": em, "additionalVisitors": count}


def today_whos_in_out_snapshot(sb: Any) -> Dict[str, Any]:
    """Latest sign-in/out row per email today, split into In and Out lists."""
    tz = _school_tz()
    now = datetime.now(tz)
    visitor_in = _today_visitor_in_by_email(sb)
    latest: Dict[str, Dict[str, Any]] = {}

    for row in _fetch_today_log_rows(sb):
        em = _norm_email(str(row.get("email") or ""))
        if not em or em in latest:
            continue
        latest[em] = row

    years = _student_years_by_email(sb)
    isams_names = isams_names_by_email()
    in_rows: List[Dict[str, Any]] = []
    out_rows: List[Dict[str, Any]] = []

    for em, row in latest.items():
        status = str(row.get("status") or "").strip().lower()
        log_name = str(row.get("name") or "").strip()
        role = str(row.get("role") or "").strip()
        name = log_name
        person = lookup_person_in_community(sb, em)
        isams_name = isams_names.get(em) or {}
        if isams_name.get("surname"):
            preferred = str(isams_name.get("preferred") or "").strip()
            if not preferred and person.get("ok"):
                preferred = str(person.get("preferredName") or "").strip()
            name = display_name_preferred_surname(
                preferred, surname=str(isams_name.get("surname") or ""), fallback=log_name
            )
            if not role and person.get("ok"):
                role = str(person.get("role") or "").strip()
        elif person.get("ok"):
            preferred = str(person.get("preferredName") or "").strip()
            fullname = str(person.get("fullname") or "").strip()
            name = display_name_preferred_surname(preferred, fullname, fallback=log_name)
            if not role:
                role = str(person.get("role") or "").strip()
        time_label = _format_log_time(row, tz)
        sort_key = str(row.get("scanned_at") or row.get("created_at") or "")
        entry = {
            "email": em,
            "name": name or em,
            "role": role,
            "time": time_label,
            "grade": _grade_for_person(em, role, years),
            "_sort": sort_key,
        }
        if str(role or "").strip().lower() == "visitor":
            vinfo = visitor_in.get(em, {})
            entry["additionalVisitors"] = vinfo.get("additionalVisitors", 0)
            entry["visitorInId"] = vinfo.get("visitorInId")
        if status == "in":
            in_rows.append(entry)
        elif status == "out":
            out_rows.append(entry)

    def _sort_key(r: Dict[str, Any]) -> str:
        return str(r.get("_sort") or "")

    in_rows.sort(key=_sort_key, reverse=True)
    out_rows.sort(key=_sort_key, reverse=True)
    for r in in_rows + out_rows:
        r.pop("_sort", None)

    expected_visitors = list_visitor_expected_pending_today(sb)

    return {
        "ok": True,
        "in": in_rows,
        "out": out_rows,
        "expectedVisitors": expected_visitors,
        "updatedAt": now.isoformat(),
        "inCount": len(in_rows),
        "outCount": len(out_rows),
        "expectedCount": len(expected_visitors),
    }


def _period_name_to_db_col(period_name: str) -> Optional[str]:
    from isams_registration import (
        REGISTRATION_PERIOD_SPECS,
        period_friendly_name_to_db_column,
        registration_period_ui_label,
    )

    raw = str(period_name or "").strip()
    if not raw:
        return None
    col = period_friendly_name_to_db_column(raw)
    if col:
        return col
    ui = registration_period_ui_label(raw)
    for db_col, lbl in REGISTRATION_PERIOD_SPECS:
        if lbl == ui or lbl == raw:
            return db_col
    return None


def _current_period_db_col(sb: Any) -> Optional[str]:
    try:
        from isams_registration import (
            get_current_period_info,
            get_current_time_hhmm,
            isams_get_token,
            load_isams_config_from_env,
            today_local_date,
        )

        school_key = os.environ.get("REGISTRATION_SCHOOL_KEY", "halcyon").strip()
        cfg = load_isams_config_from_env()
        today_s = today_local_date(cfg).isoformat()
        period_name = ""
        meta_res = (
            sb.table("registration_snapshots")
            .select("current_period_name")
            .eq("school_key", school_key)
            .eq("snapshot_date", today_s)
            .limit(1)
            .execute()
        )
        if meta_res.data:
            period_name = str(meta_res.data[0].get("current_period_name") or "").strip()
        if not period_name:
            token = isams_get_token(cfg)
            hhmm = get_current_time_hhmm(cfg)
            live = get_current_period_info(cfg, token, hhmm)
            if live and live.get("name"):
                period_name = str(live.get("name") or "").strip()
        return _period_name_to_db_col(period_name)
    except Exception:
        return None
    return None


def _registration_row_for_student(
    sb: Any,
    em: str,
    person: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Match halcyon_registration_data row by school_id (preferred) or email."""
    from isams_registration import REGISTRATION_PERIOD_SPECS

    em_norm = _norm_email(em)
    if not em_norm:
        return None
    period_cols = [c for c, _ in REGISTRATION_PERIOD_SPECS]
    select_cols = "email,school_id," + ",".join(period_cols)
    if person is None:
        person = lookup_person_in_community(sb, em_norm)
    sid = str((person or {}).get("schoolId") or "").strip()
    try:
        if sid:
            res = (
                sb.table(_registration_table())
                .select(select_cols)
                .eq("school_id", sid)
                .limit(1)
                .execute()
            )
            if res.data:
                return res.data[0]
        res = (
            sb.table(_registration_table())
            .select(select_cols)
            .eq("email", em_norm)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]
        res = sb.table(_registration_table()).select(select_cols).execute()
        for row in res.data or []:
            if _norm_email(str(row.get("email") or "")) == em_norm:
                return row
    except Exception:
        return None
    return None


def _student_present_on_registration(
    sb: Any,
    em: str,
    person: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    True when the student has at least one present (✅) mark on today's registration grid.
    Lets students sign out via QR even if they never signed in on the PWA.
    """
    from isams_registration import REGISTRATION_PERIOD_SPECS

    row = _registration_row_for_student(sb, em, person=person)
    if not row:
        return False
    for col, _ in REGISTRATION_PERIOD_SPECS:
        if row.get(col) is True:
            return True
    return False


def _decide_status(category: str, last_status: str, on_reg: bool) -> str:
    in_from_log = last_status == "In"
    in_from_reg = category == "student" and on_reg and last_status != "Out"
    virt_in = in_from_log or in_from_reg
    return "Out" if virt_in else "In"


def _name_from_prior_sign_in_today(sb: Any, em: str) -> str:
    """Use name from today's most recent In log row (visitor out without displayName)."""
    try:
        res = (
            sb.table(_signin_log_table())
            .select("name,status,scanned_at")
            .eq("email", em)
            .order("scanned_at", desc=True)
            .limit(30)
            .execute()
        )
        for row in res.data or []:
            if str(row.get("status") or "").strip() != "In":
                continue
            n = str(row.get("name") or "").strip()
            if n and "@" not in n:
                return n
    except Exception:
        pass
    return ""


def _parse_force_status(body: Dict[str, Any]) -> str:
    raw = str(body.get("forceStatus") or body.get("force_status") or "").strip()
    if raw in ("In", "Out"):
        return raw
    return ""


def _apply_directory_hints(
    category: str,
    name: str,
    role_col: str,
    display_name_opt: str,
    body: Dict[str, Any],
) -> Tuple[str, str, str, str]:
    """Use client directory hints when community lookup missed (same source: halcyon_community preload)."""
    role_hint = str(body.get("roleHint") or body.get("role_hint") or "").strip()
    full_hint = str(body.get("fullname") or body.get("fullName") or "").strip()
    pref_hint = str(body.get("preferredName") or body.get("preferred_name") or "").strip()
    if category != "none":
        return category, name, role_col, name or display_name_opt
    hinted_name = full_hint or display_name_opt or pref_hint
    if role_hint and hinted_name:
        cat = _role_to_category(role_hint)
        return cat, hinted_name, role_hint, hinted_name
    if role_hint:
        cat = _role_to_category(role_hint)
        return cat, name or display_name_opt, role_hint, name or display_name_opt
    if hinted_name:
        return category, hinted_name, role_col, hinted_name
    return category, name, role_col, name or display_name_opt


def _mailing_opt_in_body(body: Dict[str, Any]) -> bool:
    raw = body.get("mailingListOptIn", body.get("mailing_list_opt_in"))
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return False
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _visitor_in_form_payload(body: Dict[str, Any], email: str) -> Optional[Dict[str, Any]]:
    """Extract Visitor In form fields from POST body (manual sign-in)."""
    first = str(body.get("firstName") or body.get("first_name") or "").strip()
    surname = str(body.get("surname") or body.get("lastName") or body.get("last_name") or "").strip()
    meet = str(body.get("meetWith") or body.get("meet_with") or "").strip()
    reason = str(
        body.get("reasonForVisit") or body.get("reason_for_visit") or body.get("reason") or ""
    ).strip()
    company = str(body.get("companyName") or body.get("company_name") or "").strip()
    add_raw = body.get("additionalVisitors", body.get("additional_visitors", 0))
    try:
        additional = int(add_raw) if add_raw is not None and str(add_raw).strip() != "" else 0
    except (TypeError, ValueError):
        additional = 0
    if additional < 0:
        additional = 0
    display = str(body.get("displayName") or body.get("display_name") or "").strip()
    full = display or " ".join(p for p in (first, surname) if p).strip()
    has_form = bool(first or surname or meet or reason or company or additional or _mailing_opt_in_body(body))
    if not has_form:
        return None
    return {
        "email": str(email or body.get("email") or "").strip(),
        "first_name": first,
        "surname": surname,
        "full_name": full,
        "meet_with": meet,
        "reason_for_visit": reason,
        "additional_visitors": additional,
        "company_name": company,
        "mailing_list_opt_in": _mailing_opt_in_body(body),
    }


def _host_emails_for_meet_with(sb: Any, meet_with: str) -> List[str]:
    """Staff emails for the Visitor In "Here to meet with" name or address."""
    label = str(meet_with or "").strip()
    if not label:
        return []
    if "@" in label:
        return _emails_from_field(label)

    wanted = " ".join(label.split()).casefold()
    last = label.split()[-1].replace("%", "").replace("_", "")
    if not last:
        return []
    table = _community_table()
    rows: List[Dict[str, Any]] = []
    seen_rows: set[str] = set()
    for column in ("fullname", "preferred_name"):
        try:
            res = (
                sb.table(table)
                .select("fullname,email,preferred_name,role")
                .ilike(column, f"%{last}%")
                .limit(200)
                .execute()
            )
        except Exception:
            continue
        for row in res.data or []:
            key = str(row.get("email") or "") + "|" + str(row.get("fullname") or "")
            if key in seen_rows:
                continue
            seen_rows.add(key)
            rows.append(row)

    emails: List[str] = []
    seen_emails: set[str] = set()
    for row in rows:
        if _role_to_category(str(row.get("role") or "")) != "staff":
            continue
        fullname = str(row.get("fullname") or "").strip()
        preferred = str(row.get("preferred_name") or "").strip()
        names = {
            display_name_preferred_surname(preferred, fullname).casefold(),
            fullname.casefold(),
            preferred.casefold(),
        }
        if wanted not in names:
            continue
        for em in _emails_from_field(str(row.get("email") or "")):
            if em not in seen_emails:
                seen_emails.add(em)
                emails.append(em)
    return emails


def record_visitor_in_details(
    sb: Any,
    body: Dict[str, Any],
    email: str,
    *,
    sign_date: str,
    signed_at: datetime,
) -> Optional[str]:
    """
    Append Visitor In form row to Supabase. Returns error message or None (best-effort).
    """
    payload = _visitor_in_form_payload(body, email)
    if not payload or not payload.get("email") or "@" not in str(payload["email"]):
        return None
    row = {
        **payload,
        "sign_date": _pg_date(sign_date, fallback=signed_at),
        "signed_in_at": signed_at.isoformat(),
    }
    try:
        sb.table(_visitor_in_table()).insert(row).execute()
    except Exception as exc:
        msg = str(exc)
        if "does not exist" in msg or "42P01" in msg:
            return (
                f"Visitor In table missing. Run signInApp_server/sql/016_halcyon_visitor_in.sql "
                f"in Supabase SQL Editor."
            )
        return msg
    from visitor_welcome_email import send_host_visitor_notice, send_visitor_welcome_email

    visitor_email = str(payload["email"])
    send_visitor_welcome_email(visitor_email)
    meet = str(payload.get("meet_with") or "").strip()
    if meet:
        visitor_name = str(payload.get("full_name") or "").strip() or visitor_email
        visitor_count = 1 + int(payload.get("additional_visitors") or 0)
        for host_email in _host_emails_for_meet_with(sb, meet):
            send_host_visitor_notice(
                host_email,
                visitor_name=visitor_name,
                visitor_email=visitor_email,
                visitor_count=visitor_count,
                reason=str(payload.get("reason_for_visit") or "").strip(),
            )
    return None


def _attach_visitor_in_side_effects(
    result: Dict[str, Any],
    body: Dict[str, Any],
    email: str,
    force_status: Optional[str],
    *,
    sb: Any,
    sign_date: str,
    signed_at: datetime,
) -> Dict[str, Any]:
    if force_status != "In":
        return result
    body_with_email = {**body, "email": email}

    db_err = record_visitor_in_details(
        sb, body_with_email, email, sign_date=sign_date, signed_at=signed_at
    )
    if db_err:
        result["visitorIn"] = {"ok": False, "error": db_err}
    elif _visitor_in_form_payload(body_with_email, email):
        result["visitorIn"] = {"ok": True}
    return result


def record_visitor_in_from_request(sb: Any, body: Dict[str, Any]) -> Optional[str]:
    """Persist Visitor In form to Supabase (API helper). Returns error message or None."""
    em = str(body.get("email") or "").strip()
    if not em or "@" not in em:
        return "Invalid email."
    now = datetime.now(_school_tz())
    date_str, _ = _fmt_date_time(now)
    return record_visitor_in_details(sb, body, em, sign_date=date_str, signed_at=now)


def record_sign_in_out(sb: Any, body: Dict[str, Any]) -> Dict[str, Any]:
    em_raw = str(body.get("email") or "").strip()
    em = _norm_email(em_raw)
    if not em or "@" not in em:
        return {"ok": False, "error": "Invalid email."}

    method = str(body.get("method") or "qr-code").strip().lower()
    if method not in ("qr-code", "manual"):
        method = "qr-code"
    display_name_opt = str(body.get("displayName") or "").strip()

    now = datetime.now(_school_tz())
    date_str, time_str = _fmt_date_time(now)
    late = _late_yes_no(now)

    person = lookup_person_in_community(sb, em)
    if not person.get("ok") and method == "qr-code":
        err = person.get("error") or "Email not found in community directory."
        record_rejected_scan(sb, em, err, source="qr-server")
        return {
            "ok": False,
            "error": err,
            "email": em,
        }
    category = str(person.get("category") or "none")
    name = str(person.get("fullname") or person.get("preferredName") or "").strip()
    if not name and display_name_opt:
        name = display_name_opt
    role_col = str(person.get("role") or "").strip()
    if category == "none":
        role_col = role_col or ""
        if display_name_opt and not name:
            name = display_name_opt
        if not name:
            name = _name_from_prior_sign_in_today(sb, em)

    category, name, role_col, _hinted = _apply_directory_hints(
        category, name, role_col, display_name_opt, body
    )
    if category == "none" and not role_col:
        role_col = "visitor"

    resolved_name = name or display_name_opt
    if not resolved_name and category != "none":
        resolved_name = str(person.get("fullname") or person.get("preferredName") or "").strip()

    force_status = _parse_force_status(body)
    last_status = _last_status_today(sb, em, date_str)
    if force_status:
        if last_status == force_status:
            return _attach_visitor_in_side_effects(
                {
                    "ok": True,
                    "status": force_status,
                    "role": role_col or "visitor",
                    "name": resolved_name,
                    "preferredName": str(person.get("preferredName") or "").strip(),
                    "fullname": str(person.get("fullname") or resolved_name or "").strip(),
                    "category": category,
                    "late": late,
                    "skipped": True,
                },
                body,
                em_raw if em_raw else em,
                force_status,
                sb=sb,
                sign_date=date_str,
                signed_at=now,
            )
        status = force_status
    else:
        on_reg = category == "student" and _student_present_on_registration(
            sb, em, person=person if person.get("ok") else None
        )
        status = _decide_status(category, last_status, on_reg)

    row: Dict[str, Any] = {
        "name": resolved_name,
        "email": em_raw if em_raw else em,
        "role": role_col or "visitor",
        "method": method,
        "status": status,
        "scanned_at": now.isoformat(),
    }
    insert_error: Optional[str] = None
    for extra in (
        {"date": date_str, "time": time_str, "late": late},
        {"sign_date": date_str, "sign_time": time_str, "is_late": late == "Yes"},
    ):
        try:
            sb.table(_signin_log_table()).insert({**row, **extra}).execute()
            insert_error = None
            break
        except Exception as exc:
            msg = str(exc)
            if "Could not find the" in msg or "does not exist" in msg:
                continue
            insert_error = msg
            break
    else:
        try:
            sb.table(_signin_log_table()).insert(row).execute()
            insert_error = None
        except Exception as exc:
            insert_error = str(exc)

    if insert_error:
        low = insert_error.lower()
        if "permission denied" in low or "42501" in insert_error:
            insert_error = (
                "Supabase permission denied on halcyon_signInOutLogs. "
                "Run signInApp_server/sql/005_halcyon_signInOutLogs_grants.sql in the SQL Editor."
            )
        return {"ok": False, "error": insert_error}

    result: Dict[str, Any] = {
        "ok": True,
        "status": status,
        "role": role_col,
        "name": resolved_name,
        "preferredName": str(person.get("preferredName") or "").strip(),
        "fullname": str(person.get("fullname") or resolved_name or "").strip(),
        "category": category,
        "late": late,
    }

    return _attach_visitor_in_side_effects(
        result,
        body,
        em_raw if em_raw else em,
        force_status,
        sb=sb,
        sign_date=date_str,
        signed_at=now,
    )


def record_rejected_scan(
    sb: Any,
    email: str,
    reason: str,
    *,
    source: str = "qr",
) -> None:
    """Best-effort log when a scan is rejected (table optional until SQL 015 is applied)."""
    em = _norm_email(email)
    if not em or "@" not in em:
        return
    table = _rejected_scan_table()
    now = datetime.now(_school_tz())
    row = {
        "email": em,
        "reason": str(reason or "rejected")[:500],
        "source": str(source or "qr")[:64],
        "scanned_at": now.isoformat(),
    }
    try:
        sb.table(table).insert(row).execute()
    except Exception:
        pass


def bulk_sign_out_people(sb: Any, people: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Sign out one or more people (Who's In page / manual).
    Each item must include ``email``; ``name`` and ``role`` improve log rows when community lookup misses.
    """
    signed: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []

    if not people:
        return {"ok": False, "error": "No people selected.", "signedOut": [], "skipped": [], "failed": []}

    for item in people:
        em = _norm_email(str(item.get("email") or ""))
        if not em or "@" not in em:
            failed.append({"email": em or "", "error": "Invalid email."})
            continue
        body: Dict[str, Any] = {
            "email": em,
            "method": "manual",
            "forceStatus": "Out",
            "displayName": str(item.get("name") or "").strip(),
            "roleHint": str(item.get("role") or "").strip(),
        }
        result = record_sign_in_out(sb, body)
        if result.get("ok"):
            entry = {
                "email": em,
                "name": str(result.get("name") or body["displayName"] or em).strip(),
            }
            if result.get("skipped"):
                skipped.append({**entry, "reason": "already_out"})
            else:
                signed.append(entry)
        else:
            failed.append({"email": em, "error": str(result.get("error") or "Sign out failed.")})

    return {
        "ok": len(failed) == 0 and (len(signed) > 0 or len(skipped) > 0),
        "signedOut": signed,
        "skipped": skipped,
        "failed": failed,
        "signedOutCount": len(signed),
        "skippedCount": len(skipped),
    }


def bulk_sign_in_people(sb: Any, people: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Sign back in one or more people (Who's Out page / manual).
    Each item must include ``email``; ``name`` and ``role`` improve log rows when community lookup misses.
    """
    signed: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []

    if not people:
        return {"ok": False, "error": "No people selected.", "signedIn": [], "skipped": [], "failed": []}

    for item in people:
        em = _norm_email(str(item.get("email") or ""))
        if not em or "@" not in em:
            failed.append({"email": em or "", "error": "Invalid email."})
            continue
        body: Dict[str, Any] = {
            "email": em,
            "method": "manual",
            "forceStatus": "In",
            "displayName": str(item.get("name") or "").strip(),
            "roleHint": str(item.get("role") or "").strip(),
        }
        result = record_sign_in_out(sb, body)
        if result.get("ok"):
            entry = {
                "email": em,
                "name": str(result.get("name") or body["displayName"] or em).strip(),
            }
            if result.get("skipped"):
                skipped.append({**entry, "reason": "already_in"})
            else:
                signed.append(entry)
        else:
            failed.append({"email": em, "error": str(result.get("error") or "Sign in failed.")})

    return {
        "ok": len(failed) == 0 and (len(signed) > 0 or len(skipped) > 0),
        "signedIn": signed,
        "skipped": skipped,
        "failed": failed,
        "signedInCount": len(signed),
        "skippedCount": len(skipped),
    }
