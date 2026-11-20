"""
iSAMS registration logic ported from GoogleAppsScript/Isams.gs (subset used for Registration sheet).

Used by:
  - sync_registration.py (cron on DigitalOcean)
  - app.py (optional live current-period name; snapshot body comes from Supabase)
"""

from __future__ import annotations

import os
import re
from datetime import UTC, date, datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx


def _s(x: Any) -> str:
    if x is None:
        return ""
    return str(x).strip()


def school_id_as_text(school_id: Any) -> str:
    s = str(school_id)
    return s if s.startswith("'") else "'" + s


def school_id_raw(school_id: Any) -> str:
    return str(school_id).strip().lstrip("'")


# Stored in halcyon_registration_data period columns (text, not iSAMS code letters).
REG_STATUS_O = "O"  # not on this period register → UI 〇
REG_STATUS_P = "P"  # present → UI ✅
REG_STATUS_A = "A"  # absent / out → UI ❌
REG_STATUS_DASH = "-"  # on register, unmarked
REG_STATUS_CODES = frozenset({REG_STATUS_O, REG_STATUS_P, REG_STATUS_A, REG_STATUS_DASH})

# DB / UI column order (matches public.halcyon_registration_data period columns).
REGISTRATION_PERIOD_SPECS: List[Tuple[str, str]] = [
    ("eca1", "ECA 1"),
    ("pd1", "Pd 1"),
    ("pd2", "Pd 2"),
    ("pd3", "Pd 3"),
    ("pd4", "Pd 4"),
    ("pd5", "Pd 5"),
    ("pd6", "Pd 6"),
    ("eca2", "ECA 2"),
]


def registration_period_ui_label(friendly_name: str) -> str:
    """Map iSAMS friendlyName to Registration grid header label (Pd 1, ECA 1, …)."""
    col = period_friendly_name_to_db_column(friendly_name)
    if col:
        for db_col, label in REGISTRATION_PERIOD_SPECS:
            if db_col == col:
                return label
    return _s(friendly_name)


def period_day_iso(cfg: Dict[str, str], period: Dict[str, Any]) -> Optional[str]:
    """School-local calendar date for a registration period start."""
    raw = str(period.get("start") or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    tz = ZoneInfo(cfg["tz"])
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(tz).date().isoformat()


def period_friendly_name_to_db_column(friendly_name: str) -> Optional[str]:
    """Map iSAMS registration period friendlyName to halcyon_registration_data column."""
    key = " ".join(_s(friendly_name).lower().split())
    if key.startswith("period "):
        rest = key[7:].strip()
        if rest.startswith("eca "):
            key = rest
        elif rest.startswith("pd "):
            key = rest
        elif rest.isdigit():
            key = f"pd {rest}"
        else:
            key = rest
    pd_match = re.fullmatch(r"p\s*(\d+)", key)
    if pd_match:
        key = f"pd {pd_match.group(1)}"
    mapping = {
        "period eca 1": "eca1",
        "period eca 2": "eca2",
        "eca 1": "eca1",
        "eca1": "eca1",
        "pd 1": "pd1",
        "pd1": "pd1",
        "period 1": "pd1",
        "pd 2": "pd2",
        "pd2": "pd2",
        "period 2": "pd2",
        "pd 3": "pd3",
        "pd3": "pd3",
        "period 3": "pd3",
        "pd 4": "pd4",
        "pd4": "pd4",
        "period 4": "pd4",
        "pd 5": "pd5",
        "pd5": "pd5",
        "period 5": "pd5",
        "pd 6": "pd6",
        "pd6": "pd6",
        "period 6": "pd6",
        "eca 2": "eca2",
        "eca2": "eca2",
    }
    return mapping.get(key)


def parse_student_year_int(student: Dict[str, Any]) -> int:
    y = str(student.get("yearGroupName") or student.get("yearGroup") or "").strip()
    m = re.search(r"(\d+)", y)
    if not m:
        return 0
    try:
        v = int(m.group(1))
    except ValueError:
        return 0
    if v < 0:
        return 0
    if v > 32767:
        return 32767
    return v


def student_school_email(student: Dict[str, Any]) -> str:
    return _s(
        student.get("schoolEmailAddress")
        or student.get("schoolEmail")
        or student.get("emailAddress")
        or student.get("email")
    )


def load_isams_config_from_env() -> Dict[str, str]:
    base = os.environ.get("ISAMS_BASE_URL", "").strip().rstrip("/")
    cid = os.environ.get("ISAMS_CLIENT_ID", "").strip()
    secret = os.environ.get("ISAMS_CLIENT_SECRET", "").strip()
    scope = os.environ.get("ISAMS_SCOPE", "restapi").strip()
    tz = os.environ.get("SCHOOL_TIMEZONE", "Europe/London").strip()
    if not base or not cid or not secret:
        raise RuntimeError(
            "Missing iSAMS env: ISAMS_BASE_URL, ISAMS_CLIENT_ID, ISAMS_CLIENT_SECRET"
        )
    return {
        "base_url": base,
        "client_id": cid,
        "client_secret": secret,
        "scope": scope,
        "tz": tz,
    }


def isams_get_token(cfg: Dict[str, str]) -> str:
    url = f"{cfg['base_url']}/auth/connect/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "scope": cfg["scope"],
    }
    with httpx.Client(timeout=30.0) as client:
        r = client.post(url, data=data)
        r.raise_for_status()
        j = r.json()
    if j.get("error"):
        raise RuntimeError(str(j.get("error_description") or j.get("error")))
    tok = j.get("access_token")
    if not tok:
        raise RuntimeError("iSAMS token response missing access_token")
    return str(tok)


def parse_iso_to_local_hhmm(iso: Optional[str], tz: ZoneInfo) -> str:
    if not iso:
        return "00:00"
    s = str(iso).strip()
    if not s:
        return "00:00"
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return "00:00"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    local = dt.astimezone(tz)
    return f"{local.hour:02d}:{local.minute:02d}"


def get_current_time_hhmm(cfg: Dict[str, str]) -> str:
    tz = ZoneInfo(cfg["tz"])
    now = datetime.now(tz)
    return f"{now.hour:02d}:{now.minute:02d}"


def hhmm_to_minutes(hhmm: str) -> int:
    parts = str(hhmm or "00:00").strip().split(":")
    try:
        h = int(parts[0]) if parts else 0
        m = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return 0
    return h * 60 + m


def period_start_finish_hhmm(
    period: Dict[str, Any], tz: ZoneInfo
) -> Tuple[str, str]:
    return (
        parse_iso_to_local_hhmm(period.get("start"), tz),
        parse_iso_to_local_hhmm(period.get("finish"), tz),
    )


def minutes_elapsed_in_period(
    period: Dict[str, Any], cfg: Dict[str, str], current_hhmm: str
) -> Optional[int]:
    """Minutes since period start when current_hhmm is inside the period; else None."""
    tz = ZoneInfo(cfg["tz"])
    start, finish = period_start_finish_hhmm(period, tz)
    if not (start <= current_hhmm <= finish):
        return None
    return hhmm_to_minutes(current_hhmm) - hhmm_to_minutes(start)


def period_has_started(
    period: Dict[str, Any], cfg: Dict[str, str], current_hhmm: str
) -> bool:
    tz = ZoneInfo(cfg["tz"])
    start, _finish = period_start_finish_hhmm(period, tz)
    return hhmm_to_minutes(current_hhmm) >= hhmm_to_minutes(start)


def find_period_by_friendly_name(
    periods: List[Dict[str, Any]], friendly_name: str
) -> Optional[Dict[str, Any]]:
    want = " ".join(str(friendly_name or "").strip().lower().split())
    for p in periods:
        got = " ".join(str(p.get("friendlyName") or "").strip().lower().split())
        if got == want:
            return p
    return None


def today_local_date(cfg: Dict[str, str]) -> date:
    tz = ZoneInfo(cfg["tz"])
    return datetime.now(tz).date()


def format_last_update_now(cfg: Dict[str, str]) -> str:
    """Match Apps Script: HH:mm:ss dd-MM-yyyy in school timezone."""
    tz = ZoneInfo(cfg["tz"])
    return datetime.now(tz).strftime("%H:%M:%S %d-%m-%Y")


def isams_get_students(cfg: Dict[str, str], token: str) -> List[Dict[str, Any]]:
    base = cfg["base_url"]
    headers = {"Authorization": f"Bearer {token}"}
    out: List[Dict[str, Any]] = []
    page = 1
    page_size = 1000
    with httpx.Client(timeout=120.0) as client:
        while True:
            url = f"{base}/api/students?page={page}&pageSize={page_size}"
            r = client.get(url, headers=headers)
            r.raise_for_status()
            j = r.json()
            out.extend(list(j.get("students") or []))
            total_pages = int(j.get("totalPages") or 1)
            if page >= total_pages:
                break
            page += 1
    return out


def isams_get_employees(cfg: Dict[str, str], token: str) -> List[Dict[str, Any]]:
    base = cfg["base_url"]
    headers = {"Authorization": f"Bearer {token}"}
    out: List[Dict[str, Any]] = []
    page = 1
    page_size = 200
    with httpx.Client(timeout=120.0) as client:
        while True:
            url = f"{base}/api/humanresources/employees?page={page}&pageSize={page_size}"
            r = client.get(url, headers=headers)
            r.raise_for_status()
            j = r.json()
            out.extend(list(j.get("employees") or []))
            total_pages = int(j.get("totalPages") or 1)
            if page >= total_pages:
                break
            page += 1
    return out


def isams_preferred_surname_by_email(cfg: Dict[str, str], token: str) -> Dict[str, Dict[str, str]]:
    """School email → iSAMS preferredName and surname for current students and employees."""
    names: Dict[str, Dict[str, str]] = {}

    def add(row: Dict[str, Any]) -> None:
        email = _s(row.get("schoolEmailAddress")).lower()
        surname = _s(row.get("surname"))
        preferred = _s(row.get("preferredName")) or _s(row.get("forename"))
        if "@" not in email or not surname:
            return
        left = bool(_s(row.get("leavingDate")))
        current = names.get(email)
        if current and not current.get("left") and left:
            return
        names[email] = {"preferred": preferred, "surname": surname, "left": left}

    for student in isams_get_students(cfg, token):
        add(student)
    for employee in isams_get_employees(cfg, token):
        add(employee)
    return {
        email: {"preferred": row["preferred"], "surname": row["surname"]}
        for email, row in names.items()
    }


def sort_students_by_year_then_name(studentlist: List[Dict[str, Any]]) -> None:
    """Numeric year ascending, then full name (case-insensitive)."""
    def keyfn(s: Dict[str, Any]) -> Tuple[int, str]:
        yi = parse_student_year_int(s)
        n = str(s.get("fullName") or "").strip().lower()
        return (yi, n)

    studentlist.sort(key=keyfn)


def get_registration_periods(cfg: Dict[str, str], token: str) -> List[Dict[str, Any]]:
    """All periods returned by iSAMS (unfiltered). Prefer get_registration_periods_for_date."""
    url = f"{cfg['base_url']}/api/registration/periods?pageSize=1000"
    with httpx.Client(timeout=60.0) as client:
        r = client.get(url, headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        j = r.json()
    return list(j.get("registrationPeriods") or [])


def get_registration_periods_for_date(
    cfg: Dict[str, str], token: str, day: date
) -> List[Dict[str, Any]]:
    """Registration periods for one school-local day (avoids mixing other days' registers)."""
    day_iso = day.isoformat()
    start_dt = f"{day_iso}T00:00:00"
    end_dt = f"{day_iso}T23:59:59"
    url = (
        f"{cfg['base_url']}/api/registration/periods"
        f"?startDate={start_dt}&endDate={end_dt}&pageSize=1000"
    )
    with httpx.Client(timeout=60.0) as client:
        r = client.get(url, headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        j = r.json()
    return list(j.get("registrationPeriods") or [])


def get_today_registration_periods(cfg: Dict[str, str], token: str) -> List[Dict[str, Any]]:
    return get_registration_periods_for_date(cfg, token, today_local_date(cfg))


def get_current_period_info(
    cfg: Dict[str, str], token: str, current_hhmm: str
) -> Optional[Dict[str, Any]]:
    tz = ZoneInfo(cfg["tz"])
    today_s = today_local_date(cfg).isoformat()
    for p in get_today_registration_periods(cfg, token):
        if period_day_iso(cfg, p) != today_s:
            continue
        start = parse_iso_to_local_hhmm(p.get("start"), tz)
        finish = parse_iso_to_local_hhmm(p.get("finish"), tz)
        if start <= current_hhmm <= finish:
            return {
                "name": str(p.get("friendlyName") or "").strip(),
                "id": p.get("id"),
                "expected": int(p.get("numberOfStudents") or 0),
            }
    return None


def _registration_code_id_set(raw: Any) -> bool:
    return raw not in (None, "", 0)


def _registration_is_registered_flag(d: Dict[str, Any]) -> bool:
    return d.get("isRegistered") in (1, "1", True)


def registration_status_unmarked(d: Dict[str, Any]) -> bool:
    """True only when iSAMS has no register row for this student/period."""
    msg = str(d.get("message") or "")
    return "Registration Status does not exist" in msg


def registration_status_code_from_body(d: Dict[str, Any]) -> str:
    """
    Map iSAMS register JSON to stored status O / P / A / - (no absence reason codes).
    """
    if registration_status_unmarked(d):
        return REG_STATUS_O
    if d.get("isOutOfSchool") is True:
        return REG_STATUS_A
    if d.get("isPresent") is True:
        return REG_STATUS_P
    if _registration_is_registered_flag(d) and d.get("isPresent") is False:
        return REG_STATUS_A
    if not _registration_is_registered_flag(d):
        return REG_STATUS_DASH
    return REG_STATUS_DASH


def registration_db_code_to_symbol(code: Any) -> str:
    """Map halcyon_registration_data column value to grid emoji."""
    if code is True:
        return "✅"
    if code is False:
        return "❌"
    c = str(code or "").strip()
    if c == REG_STATUS_P:
        return "✅"
    if c == REG_STATUS_A:
        return "❌"
    if c == REG_STATUS_DASH:
        return "-"
    return "〇"


def normalize_registration_db_code(raw: Any) -> str:
    """Coerce legacy bool/null or text into O/P/A/-."""
    if raw is True:
        return REG_STATUS_P
    if raw is False:
        return REG_STATUS_A
    c = str(raw or "").strip()
    if c in REG_STATUS_CODES:
        return c
    return REG_STATUS_O


def registration_status_symbol_from_body(d: Dict[str, Any]) -> str:
    """Map iSAMS register JSON to Registration UI symbols (✅ / ❌ / - / 〇)."""
    return registration_db_code_to_symbol(registration_status_code_from_body(d))


def fetch_period_attendance_map(
    cfg: Dict[str, str],
    token: str,
    period_id: Any,
    *,
    period: Optional[Dict[str, Any]] = None,
    current_hhmm: Optional[str] = None,
) -> Dict[str, str]:
    """Bulk register for one period → school_id → O/P/A/- for pupils on that register."""
    if period is not None:
        hhmm = current_hhmm if current_hhmm is not None else get_current_time_hhmm(cfg)
        if not period_has_started(period, cfg, hhmm):
            return {}

    from isams_registration_attendance_rest import list_register_statuses

    rows, register_missing = list_register_statuses(cfg, token, period_id)
    if register_missing:
        return {}
    out: Dict[str, str] = {}
    for row in rows:
        sid = school_id_raw(row.get("schoolId"))
        if not sid:
            continue
        out[sid] = registration_status_code_from_body(row)
    return out


def fetch_attendance_symbol(
    cfg: Dict[str, str], token: str, period_id: Any, school_id: Any
) -> str:
    return registration_status_symbol_from_body(
        _fetch_register_status_body(cfg, token, period_id, school_id)
    )


def fetch_attendance_is_present(
    cfg: Dict[str, str], token: str, period_id: Any, school_id: Any
) -> Optional[bool]:
    """True = present, False = absent, None = unmarked or not on register."""
    code = registration_status_code_from_body(
        _fetch_register_status_body(cfg, token, period_id, school_id)
    )
    if code == REG_STATUS_P:
        return True
    if code == REG_STATUS_A:
        return False
    return None


def _fetch_register_status_body(
    cfg: Dict[str, str], token: str, period_id: Any, school_id: Any
) -> Dict[str, Any]:
    sid = str(school_id).strip().lstrip("'")
    if not sid:
        return {"message": "Registration Status does not exist"}
    base = cfg["base_url"]
    enc = quote(sid, safe="")
    url = f"{base}/api/registration/register/{period_id}/students/{enc}?pageSize=1000"
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url, headers={"Authorization": f"Bearer {token}"})
        text = r.text or ""
    try:
        d = r.json() if text.strip() else {}
    except Exception:
        d = {}
    return d if isinstance(d, dict) else {}


def _status_cell(raw: Any) -> str:
    if raw in ("True", True):
        return "✅"
    if raw in ("False", False):
        return "❌"
    if raw in ("O", "o", ""):
        return "〇"
    return registration_db_code_to_symbol(raw)


def build_registration_table(
    cfg: Dict[str, str], token: str, studentlist: List[Dict[str, Any]], current_hhmm: str
) -> Tuple[List[str], List[List[str]], str]:
    """
    Port of isamsGetRegisters(..., sheet=None) tableRows output.
    Returns (period_columns, table_rows_without_header_row0, current_period_name).
    table_rows include header row as first element... actually GS tableRows includes header as first push.
    We'll return headers list + body rows (students + TOTAL + EXPECTED) i.e. full grid rows including header in [0].
    """
    tz = ZoneInfo(cfg["tz"])
    periods = get_registration_periods(cfg, token)
    period_cols_only = [str(p.get("friendlyName") or "").strip() for p in periods]
    period_columns = ["Name", "Year"] + period_cols_only + ["School Id"]

    rgstr_prd_data_row: List[Dict[str, Any]] = []
    for p in periods:
        period_name = str(p.get("friendlyName") or "").strip()
        start_time = parse_iso_to_local_hhmm(p.get("start"), tz)
        finish_time = parse_iso_to_local_hhmm(p.get("finish"), tz)
        if start_time <= current_hhmm <= finish_time:
            for stu in studentlist:
                sid = stu.get("schoolId")
                att_url = (
                    f"{cfg['base_url']}/api/registration/register/{p.get('id')}/students/"
                    f"{quote(str(sid).strip(), safe='')}?pageSize=1000"
                )
                with httpx.Client(timeout=30.0) as client:
                    ar = client.get(att_url, headers={"Authorization": f"Bearer {token}"})
                    try:
                        dict_attnd = ar.json()
                    except Exception:
                        dict_attnd = {}
                period_id_data = registration_status_code_from_body(dict_attnd)
                rec = {"School Id": str(sid), "Name": stu.get("fullName")}
                rec[period_name] = period_id_data
                rgstr_prd_data_row.append(rec)

    by_school_id: Dict[str, Dict[str, Any]] = {}
    for student in studentlist:
        sid = str(student.get("schoolId"))
        by_school_id[sid] = {
            "Name": student.get("fullName"),
            "Year": student.get("yearGroupName") or student.get("yearGroup") or "",
            "School Id": school_id_as_text(student.get("schoolId")),
        }
        for col in period_cols_only:
            by_school_id[sid][col] = ""

    for record in rgstr_prd_data_row:
        sid = str(record.get("School Id"))
        row = by_school_id.get(sid)
        if not row:
            continue
        for key, val in record.items():
            if key in ("School Id", "Name"):
                continue
            if key in period_cols_only:
                row[key] = True if val is True else (False if val is False else val)

    student_ids = [str(s.get("schoolId")) for s in studentlist]
    table_rows: List[List[str]] = []
    table_rows.append(period_columns)
    for sid in student_ids:
        row = by_school_id.get(sid)
        if not row:
            continue
        r = []
        for col in period_columns:
            v = row.get(col)
            if col == "School Id":
                r.append(str(v) if v is not None else "")
            elif col in period_cols_only:
                r.append(_status_cell(v))
            else:
                r.append(_s(v))
        table_rows.append(r)

    totals: Dict[str, Any] = {c: "" for c in period_columns}
    totals["Name"] = "TOTAL"
    for col in period_cols_only:
        count = 0
        for sid in student_ids:
            v = by_school_id.get(sid, {}).get(col)
            if v in ("True", True, "✅"):
                count += 1
        totals[col] = count
    table_rows.append([totals.get(c, "") for c in period_columns])

    rgstr_period_rows = []
    for p in periods:
        period_name = str(p.get("friendlyName") or "").strip()
        rgstr_period_rows.append(
            [
                period_name,
                p.get("id"),
                parse_iso_to_local_hhmm(p.get("start"), tz),
                parse_iso_to_local_hhmm(p.get("finish"), tz),
                int(p.get("numberOfStudents") or 0),
            ]
        )
    expected_values: Dict[str, Any] = {c: "" for c in period_columns}
    expected_values["Name"] = "EXPECTED"
    for pr in rgstr_period_rows:
        pname = str(pr[0])
        if pname in expected_values:
            expected_values[pname] = pr[4]
    table_rows.append([expected_values.get(c, "") for c in period_columns])

    cpi = get_current_period_info(cfg, token, current_hhmm)
    current_name = (cpi or {}).get("name") or ""
    return period_columns, table_rows, str(current_name).strip()


def update_current_period_column_only(
    cfg: Dict[str, str],
    token: str,
    headers: List[str],
    rows: List[List[str]],
    current_hhmm: str,
) -> Optional[Tuple[List[str], List[List[str]], str]]:
    """
    Port of isamsUpdateCurrentPeriodOnly: mutate rows in-place (list of lists).
    rows must include row0 = header, then students, then TOTAL, then EXPECTED.
    Returns new (headers, rows, current_period_name) or None if skipped.
    """
    pinfo = get_current_period_info(cfg, token, current_hhmm)
    if not pinfo:
        return None
    period_name = str(pinfo.get("name") or "").strip()
    period_id = pinfo.get("id")
    expected = int(pinfo.get("expected") or 0)
    if not period_name or period_id is None:
        return None

    if len(rows) < 4:
        return None
    header = [str(h).strip() for h in rows[0]]
    try:
        period_col_index = header.index(period_name)
    except ValueError:
        return None
    try:
        school_id_col_index = header.index("School Id")
    except ValueError:
        return None

    last_row_idx = len(rows) - 1
    last_student_idx = last_row_idx - 2  # 0-based: before TOTAL and EXPECTED
    if last_student_idx < 1:
        return None

    total_present = 0
    for r in range(1, last_student_idx + 1):
        sid = rows[r][school_id_col_index]
        sym = fetch_attendance_symbol(cfg, token, period_id, sid)
        while len(rows[r]) <= period_col_index:
            rows[r].append("")
        rows[r][period_col_index] = sym
        if sym == "✅":
            total_present += 1

    total_row_idx = last_student_idx + 1
    exp_row_idx = last_student_idx + 2
    while len(rows[total_row_idx]) <= period_col_index:
        rows[total_row_idx].append("")
    while len(rows[exp_row_idx]) <= period_col_index:
        rows[exp_row_idx].append("")
    rows[total_row_idx][period_col_index] = str(total_present)
    rows[exp_row_idx][period_col_index] = str(expected)

    return header, rows, period_name
