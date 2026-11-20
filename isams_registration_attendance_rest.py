"""
REST API fetch for whole-year registration attendance (replaces batch RegistrationManager).

Uses the same OAuth credentials as sync_registration.py / isams_registration.py.
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

import httpx

from isams_registration import (
    REGISTRATION_PERIOD_SPECS,
    _registration_code_id_set,
    _registration_is_registered_flag,
    isams_get_students,
    isams_get_token,
    load_isams_config_from_env,
    period_friendly_name_to_db_column,
    registration_status_unmarked,
    today_local_date,
)

REST_ATTENDANCE_FROM = "2026-08-25"
REST_THROTTLE_SECONDS = 0.05


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def attendance_source() -> str:
    """``rest`` (default) or ``batch``."""
    return (_env("REGISTRATION_ATTENDANCE_SOURCE", "rest") or "rest").lower()


def rest_backfill_from_date(today: Optional[date] = None) -> date:
    """Earliest date to REST-sync (current academic year from Aug 2026 onward)."""
    configured = _env("REGISTRATION_ATTENDANCE_REST_FROM", REST_ATTENDANCE_FROM)
    floor = datetime.strptime(configured, "%Y-%m-%d").date()
    from registration_data_wholeyear import academic_year_start_year_on, academic_year_bounds

    start_year = academic_year_start_year_on(today or date.today())
    year_start = datetime.strptime(academic_year_bounds(start_year)[0], "%Y-%m-%d").date()
    return max(floor, year_start)


def rest_backfill_bounds(today: Optional[date] = None) -> Tuple[date, date]:
    cfg = load_isams_config_from_env()
    target = today_local_date(cfg) - timedelta(days=1)
    return rest_backfill_from_date(today), target


def period_label_for_friendly_name(friendly_name: str) -> Optional[str]:
    col = period_friendly_name_to_db_column(friendly_name)
    if not col:
        return None
    for key, label in REGISTRATION_PERIOD_SPECS:
        if key == col:
            return label
    return None


def _throttle() -> None:
    delay = float(_env("ISAMS_REST_THROTTLE_SECONDS", str(REST_THROTTLE_SECONDS)) or "0")
    if delay > 0:
        time.sleep(delay)


def _response_message(response: httpx.Response) -> str:
    try:
        body = response.json() if response.text.strip() else {}
        if isinstance(body, dict):
            return str(body.get("message") or body.get("error") or "")
    except Exception:
        pass
    return str(response.text or "")


def _register_not_ready_message(message: str) -> bool:
    low = message.lower()
    return any(
        phrase in low
        for phrase in (
            "registration statuses have not been generated",
            "registration status does not exist",
            "registration period could not be found",
        )
    )


def _get_json(cfg: Dict[str, str], token: str, path: str) -> Dict[str, Any]:
    url = f"{cfg['base_url']}{path}"
    with httpx.Client(timeout=120.0) as client:
        r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    if r.status_code >= 400:
        raise RuntimeError(f"REST GET {path} failed ({r.status_code}): {(r.text or '')[:400]}")
    try:
        body = r.json() if r.text.strip() else {}
    except Exception as exc:
        raise RuntimeError(f"REST GET {path} returned invalid JSON") from exc
    return body if isinstance(body, dict) else {}


def _get_json_optional(cfg: Dict[str, str], token: str, path: str) -> Optional[Dict[str, Any]]:
    """Return None when iSAMS has no register generated yet for a period."""
    url = f"{cfg['base_url']}{path}"
    with httpx.Client(timeout=120.0) as client:
        r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    if r.status_code >= 400:
        message = _response_message(r)
        if _register_not_ready_message(message):
            return None
        raise RuntimeError(f"REST GET {path} failed ({r.status_code}): {message[:400]}")
    try:
        body = r.json() if r.text.strip() else {}
    except Exception as exc:
        raise RuntimeError(f"REST GET {path} returned invalid JSON") from exc
    return body if isinstance(body, dict) else {}


def _with_page(path: str, page: int, page_size: int) -> str:
    parsed = urlparse(path)
    params = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k not in ("page", "pageSize")]
    params.extend([("page", str(page)), ("pageSize", str(page_size))])
    return urlunparse(parsed._replace(query=urlencode(params)))


def _paginate(
    cfg: Dict[str, str],
    token: str,
    path: str,
    *,
    list_keys: Tuple[str, ...],
    page_size: int = 1000,
) -> List[Dict[str, Any]]:
    page = 1
    out: List[Dict[str, Any]] = []
    while True:
        body = _get_json(cfg, token, _with_page(path, page, page_size))
        chunk: List[Dict[str, Any]] = []
        if isinstance(body, list):
            chunk = body
        else:
            for key in list_keys:
                val = body.get(key)
                if isinstance(val, list):
                    chunk = val
                    break
        out.extend(chunk)
        total_pages = int(body.get("totalPages") or 1) if isinstance(body, dict) else 1
        if page >= total_pages or not chunk:
            break
        page += 1
        _throttle()
    return out


def _parse_api_date(value: Any) -> date:
    s = str(value or "").strip()
    if not s:
        raise ValueError("empty date")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).date()


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
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(tz).date().isoformat()


def get_school_terms(cfg: Dict[str, str], token: str) -> List[Dict[str, Any]]:
    body = _get_json(cfg, token, "/api/school/terms")
    terms = list(body.get("terms") or [])
    terms.sort(key=lambda term: str(term.get("startDate") or ""))
    return terms


def term_clipped_ranges(
    start_date: str, end_date: str, terms: List[Dict[str, Any]]
) -> List[Tuple[str, str]]:
    """iSAMS requires registration period queries to fall inside a school term."""
    req_start = datetime.strptime(start_date, "%Y-%m-%d").date()
    req_end = datetime.strptime(end_date, "%Y-%m-%d").date()
    ranges: List[Tuple[date, date]] = []
    for term in terms:
        try:
            term_start = _parse_api_date(term.get("startDate"))
            term_end = _parse_api_date(term.get("finishDate"))
        except ValueError:
            continue
        clip_start = max(req_start, term_start)
        clip_end = min(req_end, term_end)
        if clip_start <= clip_end:
            ranges.append((clip_start, clip_end))

    if not ranges:
        return []

    ranges.sort()
    merged: List[Tuple[date, date]] = [ranges[0]]
    for clip_start, clip_end in ranges[1:]:
        last_start, last_end = merged[-1]
        if clip_start <= last_end + timedelta(days=1):
            merged[-1] = (last_start, max(last_end, clip_end))
        else:
            merged.append((clip_start, clip_end))
    return [(a.isoformat(), b.isoformat()) for a, b in merged]


def _fetch_periods_for_day(cfg: Dict[str, str], token: str, day_iso: str) -> List[Dict[str, Any]]:
    start_dt = f"{day_iso}T00:00:00"
    end_dt = f"{day_iso}T23:59:59"
    path = f"/api/registration/periods?startDate={start_dt}&endDate={end_dt}"
    return _paginate(cfg, token, path, list_keys=("registrationPeriods",))


def get_registration_periods_in_range(
    cfg: Dict[str, str], token: str, start_date: str, end_date: str
) -> List[Dict[str, Any]]:
    from registration_data_wholeyear import school_days_between

    terms = get_school_terms(cfg, token)
    school_days = school_days_between(start_date, end_date)
    if not school_days:
        return []

    periods: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    unmapped_names: set[str] = set()
    raw_count = 0

    for day in school_days:
        day_iso = day["iso"]
        if not term_clipped_ranges(day_iso, day_iso, terms):
            continue
        day_periods = _fetch_periods_for_day(cfg, token, day_iso)
        raw_count += len(day_periods)
        for period in day_periods:
            period_id = str(period.get("id") or "")
            if period_id and period_id in seen_ids:
                continue
            friendly = str(period.get("friendlyName") or "")
            label = period_label_for_friendly_name(friendly)
            if not label:
                if friendly:
                    unmapped_names.add(friendly)
                continue
            if period_id:
                seen_ids.add(period_id)
            period = dict(period)
            period["_periodLabel"] = label
            periods.append(period)
        _throttle()

    if unmapped_names:
        sample = ", ".join(sorted(unmapped_names)[:8])
        print(f"  WARN: unmapped period name(s): {sample}")

    print(
        f"  REST: {len(periods)} mapped period(s) from {raw_count} raw "
        f"across {len(school_days)} weekday(s)"
    )
    return periods


def period_start_date(cfg: Dict[str, str], period: Dict[str, Any]) -> Optional[date]:
    day_iso = period_day_iso(cfg, period)
    if day_iso:
        return datetime.strptime(day_iso, "%Y-%m-%d").date()
    try:
        return _parse_api_date(period.get("start"))
    except ValueError:
        return None


def list_register_statuses(
    cfg: Dict[str, str], token: str, registration_period_id: Any
) -> Tuple[List[Dict[str, Any]], bool]:
    """Return (rows, register_missing). register_missing=True when iSAMS has no register yet."""
    path = f"/api/registration/register/{registration_period_id}"
    page = 1
    page_size = 1000
    out: List[Dict[str, Any]] = []
    while True:
        body = _get_json_optional(cfg, token, _with_page(path, page, page_size))
        if body is None:
            return [], True
        chunk: List[Dict[str, Any]] = []
        if isinstance(body, list):
            chunk = body
        else:
            for key in ("registrationStatuses", "students", "items"):
                val = body.get(key)
                if isinstance(val, list):
                    chunk = val
                    break
        out.extend(chunk)
        total_pages = int(body.get("totalPages") or 1) if isinstance(body, dict) else 1
        if page >= total_pages or not chunk:
            break
        page += 1
        _throttle()
    _throttle()
    return out, False


def fetch_registration_codes_rest(
    cfg: Dict[str, str], token: str
) -> Tuple[List[Dict[str, str]], Dict[str, Dict[str, str]]]:
    """Return (code_rows, code_by_id) compatible with parse_registration_codes / attendance_symbol."""
    present = _paginate(cfg, token, "/api/registration/presentcodes", list_keys=("presentCodes",))
    absence = _paginate(cfg, token, "/api/registration/absencecodes", list_keys=("absenceCodes",))

    code_rows: List[Dict[str, str]] = []
    code_by_id: Dict[str, Dict[str, str]] = {}
    seen_letters: set[str] = set()

    for item in present + absence:
        code_id = str(item.get("id") or "")
        letter = str(item.get("code") or "").strip()
        name = str(item.get("name") or item.get("description") or "").strip()
        if code_id:
            code_by_id[code_id] = {"code": letter, "name": name}
        if letter and letter not in seen_letters:
            seen_letters.add(letter)
            code_rows.append({"code": letter, "name": name})

    code_rows.sort(key=lambda row: (row["code"] == "-", row["code"]))
    return code_rows, code_by_id


def rest_status_to_batch(status: Dict[str, Any], *, period_id: str) -> Dict[str, Any]:
    pupil_id = str(status.get("schoolId") or status.get("pupilId") or "").strip().lstrip("'")
    is_present = status.get("isPresent") is True
    is_late = status.get("isLate") is True
    minutes = status.get("numberOfMinutesLate")
    comment = str(status.get("registrationComment") or "").strip()

    has_absence = _registration_code_id_set(status.get("absenceCodeId"))
    has_present = _registration_code_id_set(status.get("presentCodeId"))
    is_registered = _registration_is_registered_flag(status)

    code_id = ""
    registered = False

    if registration_status_unmarked(status):
        registered = False
    elif has_absence or (is_registered and not is_present) or status.get("isOutOfSchool") is True:
        registered = False
        if has_absence:
            code_id = str(status["absenceCodeId"])
    else:
        registered = True
        if has_present and not has_absence:
            code_id = str(status["presentCodeId"])

    return {
        "PupilId": pupil_id,
        "PeriodId": str(period_id),
        "Registered": "1" if registered else "0",
        "Late": "1" if is_late else "0",
        "Code": code_id,
        "Reason": comment,
        "MinutesLate": str(minutes if minutes is not None else ""),
    }


def fetch_pupil_lookup_rest(cfg: Dict[str, str], token: str) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    for student in isams_get_students(cfg, token):
        school_id = str(student.get("schoolId") or "").strip()
        if not school_id:
            continue
        lookup[school_id] = {
            "SchoolId": school_id,
            "Fullname": str(student.get("fullName") or student.get("preferredName") or school_id).strip(),
            "NCYear": str(student.get("yearGroup") or student.get("yearGroupName") or "").strip(),
            "Form": str(student.get("formGroup") or student.get("divisionName") or "").strip(),
        }
    return lookup


def _periods_to_batch_node(cfg: Dict[str, str], periods: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []
    for period in periods:
        period_id = period.get("id")
        label = period.get("_periodLabel") or period_label_for_friendly_name(
            str(period.get("friendlyName") or "")
        )
        day_iso = period_day_iso(cfg, period)
        start = str(period.get("start") or "")
        if period_id is None or not label or not start or not day_iso:
            continue
        nodes.append(
            {
                "@Id": str(period_id),
                "FriendlyName": label,
                "Start": f"{day_iso}T{start[11:]}" if len(start) >= 11 else start,
            }
        )
    return nodes


def fetch_rest_registration_data(start_date: str, end_date: str) -> Dict[str, Any]:
    """
    Build a batch-shaped RegistrationManager payload for ``registration_attendance_db``.
    """
    cfg = load_isams_config_from_env()
    token = isams_get_token(cfg)

    code_rows, code_by_id = fetch_registration_codes_rest(cfg, token)
    periods = get_registration_periods_in_range(cfg, token, start_date, end_date)

    yesterday = today_local_date(cfg) - timedelta(days=1)
    statuses: List[Dict[str, Any]] = []
    skipped_missing = 0
    fetched_registers = 0

    for index, period in enumerate(periods, start=1):
        period_id = str(period.get("id") or "")
        if not period_id:
            continue
        start_day = period_start_date(cfg, period)
        if start_day and start_day > yesterday:
            continue
        if len(periods) > 40 and index % 20 == 0:
            print(f"  REST: fetching registers {index}/{len(periods)}…")
        rows, register_missing = list_register_statuses(cfg, token, period_id)
        if register_missing:
            skipped_missing += 1
            continue
        fetched_registers += 1
        for row in rows:
            statuses.append(rest_status_to_batch(row, period_id=period_id))

    if skipped_missing:
        print(f"  REST: skipped {skipped_missing} period(s) with no generated register")
    print(f"  REST: loaded {len(statuses)} mark(s) from {fetched_registers} register(s)")

    registration_codes = [
        {"@Id": cid, "Code": meta["code"], "Name": meta["name"]}
        for cid, meta in sorted(code_by_id.items(), key=lambda item: item[1]["code"])
    ]

    return {
        "iSAMS": {
            "RegistrationManager": {
                "RegistrationPeriods": {"RegistrationPeriod": _periods_to_batch_node(cfg, periods)},
                "RegistrationStatuses": {"RegistrationStatus": statuses},
                "RegistrationCodes": {"RegistrationCode": registration_codes},
                "_restCodeRows": code_rows,
            }
        }
    }
