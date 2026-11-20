"""Supabase storage for whole-year registration attendance (/registration-wholeyear)."""

from __future__ import annotations

import base64
import gzip
import os
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from supabase import create_client

from isams_registration import load_isams_config_from_env, today_local_date
from registration_data_wholeyear import (
    PERIOD_ORDER,
    academic_year_meta,
    academic_year_start_year_on,
    attendance_symbol,
    default_academic_start_years,
    fetch_batch_data,
    fetch_pupil_lookup,
    parse_periods,
    parse_registration_codes,
    pupil_sort_key,
    registration_code_lookup,
    school_days_between,
    sparse_pupil_day_map,
)

from isams_registration_attendance_rest import (
    attendance_source,
    fetch_rest_registration_data,
    rest_backfill_bounds,
    rest_backfill_from_date,
)

UPSERT_BATCH = 200

TABLE_STUDENTS = os.environ.get(
    "SUPABASE_REGISTRATION_ATTENDANCE_STUDENTS_TABLE", "halcyon_registration_attendance_students"
).strip()
TABLE_CODES = os.environ.get(
    "SUPABASE_REGISTRATION_ATTENDANCE_CODES_TABLE", "halcyon_registration_attendance_codes"
).strip()
TABLE_DAYS = os.environ.get(
    "SUPABASE_REGISTRATION_ATTENDANCE_DAYS_TABLE", "halcyon_registration_attendance_days"
).strip()
TABLE_SYNC = os.environ.get(
    "SUPABASE_REGISTRATION_ATTENDANCE_SYNC_TABLE", "halcyon_registration_attendance_sync"
).strip()


def school_key() -> str:
    return os.environ.get("REGISTRATION_SCHOOL_KEY", "halcyon").strip() or "halcyon"


def require_supabase() -> Any:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY required for attendance sync")
    return create_client(url, key)


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _chunked(items: List[Dict[str, Any]], size: int) -> List[List[Dict[str, Any]]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def yesterday_local() -> date:
    cfg = load_isams_config_from_env()
    return today_local_date(cfg) - timedelta(days=1)


def backfill_bounds(start_years: List[int]) -> Tuple[date, date]:
    if attendance_source() == "rest":
        return rest_backfill_bounds()
    if not start_years:
        start_years = default_academic_start_years()
    starts = [datetime.strptime(academic_year_meta(y)["start"], "%Y-%m-%d").date() for y in start_years]
    return min(starts), yesterday_local()


def fetch_registration_data(start_date: str, end_date: str, *, http_method: str = "post") -> Dict[str, Any]:
    if attendance_source() == "rest":
        return fetch_rest_registration_data(start_date, end_date)
    return fetch_batch_data(start_date, end_date, http_method=http_method)


def discover_start_years_from_db(sb: Any, sk: str) -> List[int]:
    """Infer academic start years from the earliest/latest day_date stored in Supabase."""
    from registration_data_wholeyear import academic_year_start_year_on

    try:
        res_min = (
            sb.table(TABLE_DAYS)
            .select("day_date")
            .eq("school_key", sk)
            .order("day_date")
            .limit(1)
            .execute()
        )
        res_max = (
            sb.table(TABLE_DAYS)
            .select("day_date")
            .eq("school_key", sk)
            .order("day_date", desc=True)
            .limit(1)
            .execute()
        )
    except Exception:
        return []
    rows_min = res_min.data or []
    rows_max = res_max.data or []
    if not rows_min or not rows_max:
        return []
    min_d = datetime.strptime(str(rows_min[0]["day_date"]), "%Y-%m-%d").date()
    max_d = datetime.strptime(str(rows_max[0]["day_date"]), "%Y-%m-%d").date()
    start_y = academic_year_start_year_on(min_d)
    end_y = academic_year_start_year_on(max_d)
    return list(range(start_y, end_y + 1))


def resolve_sync_start_years(
    sb: Optional[Any] = None,
    sk: Optional[str] = None,
    *,
    explicit: Optional[List[int]] = None,
) -> List[int]:
    """Years to sync/backfill: default window plus any years with data in Supabase."""
    years = set(explicit or default_academic_start_years())
    if sb is not None and sk:
        years.update(discover_start_years_from_db(sb, sk))
    return sorted(years)


def resolve_html_start_years(
    *,
    explicit: Optional[List[int]] = None,
) -> List[int]:
    """Years embedded in the HTML viewer (keep small — App Platform serves this per request)."""
    if explicit:
        return explicit
    return default_academic_start_years()


_SYNC_STATE_COLUMNS = (
    "school_key",
    "backfill_from",
    "backfill_through",
    "backfill_target",
    "last_incremental_date",
    "html_generated_at",
    "updated_at",
)


def get_sync_state(sb: Any, sk: str, *, include_html: bool = False) -> Optional[Dict[str, Any]]:
    columns = "*" if include_html else ",".join(_SYNC_STATE_COLUMNS)
    try:
        res = sb.table(TABLE_SYNC).select(columns).eq("school_key", sk).limit(1).execute()
    except Exception as exc:
        msg = str(exc)
        if "does not exist" in msg or "42P01" in msg:
            raise RuntimeError(
                f"{TABLE_SYNC} table missing. "
                "Run signInApp_server/sql/008_registration_attendance_wholeyear.sql in Supabase."
            ) from exc
        raise
    rows = res.data or []
    return rows[0] if rows else None


def upsert_sync_state(sb: Any, sk: str, payload: Dict[str, Any], *, start_years: Optional[List[int]] = None) -> None:
    existing = get_sync_state(sb, sk) or {}
    merged: Dict[str, Any] = {**existing, **payload, "school_key": sk}
    if not merged.get("backfill_from") or not merged.get("backfill_target"):
        backfill_from, target = backfill_bounds(start_years or default_academic_start_years())
        merged.setdefault("backfill_from", backfill_from.isoformat())
        merged.setdefault("backfill_target", target.isoformat())
    row = {
        "school_key": sk,
        "backfill_from": merged.get("backfill_from"),
        "backfill_through": merged.get("backfill_through"),
        "backfill_target": merged.get("backfill_target"),
        "last_incremental_date": merged.get("last_incremental_date"),
        "updated_at": datetime.utcnow().isoformat(),
    }
    if "html_generated_at" in payload:
        row["html_generated_at"] = payload.get("html_generated_at")
    else:
        row["html_generated_at"] = existing.get("html_generated_at")
    # Only touch html_content when explicitly saving HTML (avoid re-uploading ~350KB+ on every cursor update).
    if "html_content" in payload:
        row["html_content"] = payload.get("html_content")
    sb.table(TABLE_SYNC).upsert(row, on_conflict="school_key").execute()


HTML_CACHE_PREFIX = "gzip:b64:"


def _encode_html_cache(html_content: str) -> str:
    compressed = gzip.compress(html_content.encode("utf-8"), compresslevel=6)
    return HTML_CACHE_PREFIX + base64.b64encode(compressed).decode("ascii")


def _decode_html_cache(stored: str) -> bytes:
    if stored.startswith(HTML_CACHE_PREFIX):
        compressed = base64.b64decode(stored[len(HTML_CACHE_PREFIX) :])
        return gzip.decompress(compressed)
    return stored.encode("utf-8")


def save_html_cache(sb: Any, sk: str, html_content: str, *, start_years: Optional[List[int]] = None) -> None:
    encoded = _encode_html_cache(html_content)
    upsert_sync_state(
        sb,
        sk,
        {
            "html_content": encoded,
            "html_generated_at": datetime.utcnow().isoformat(),
        },
        start_years=start_years,
    )


def load_html_cache(sb: Any, sk: str) -> Optional[bytes]:
    try:
        res = sb.table(TABLE_SYNC).select("html_content").eq("school_key", sk).limit(1).execute()
    except Exception as exc:
        msg = str(exc)
        if "html_content" in msg and ("does not exist" in msg or "42703" in msg):
            return None
        raise
    rows = res.data or []
    if not rows:
        return None
    html = rows[0].get("html_content")
    if not html:
        return None
    return _decode_html_cache(str(html))


def ensure_sync_state(sb: Any, sk: str, start_years: List[int]) -> Dict[str, Any]:
    backfill_from, target = backfill_bounds(start_years)
    state = get_sync_state(sb, sk)
    if not state:
        upsert_sync_state(
            sb,
            sk,
            {
                "backfill_from": backfill_from.isoformat(),
                "backfill_through": None,
                "backfill_target": target.isoformat(),
                "last_incremental_date": None,
                "html_generated_at": None,
            },
            start_years=start_years,
        )
        return get_sync_state(sb, sk) or {}

    updates: Dict[str, Any] = {}
    stored_from = state.get("backfill_from")
    if attendance_source() == "rest":
        rest_from = rest_backfill_from_date().isoformat()
        if stored_from != rest_from:
            updates["backfill_from"] = rest_from
            if stored_from and stored_from < rest_from:
                updates["backfill_through"] = None
                updates["last_incremental_date"] = None
    elif stored_from and backfill_from.isoformat() < stored_from:
        updates["backfill_from"] = backfill_from.isoformat()
    if state.get("backfill_target") != target.isoformat():
        updates["backfill_target"] = target.isoformat()
    if updates:
        upsert_sync_state(sb, sk, updates, start_years=start_years)
        state = get_sync_state(sb, sk) or state
    return state


def _index_statuses_by_period(data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    rm = data.get("iSAMS", {}).get("RegistrationManager", {})
    status_node = rm.get("RegistrationStatuses", {})
    raw = status_node.get("RegistrationStatus") if isinstance(status_node, dict) else None
    by_period: Dict[str, List[Dict[str, Any]]] = {}
    for item in _as_list(raw):
        period_id = str(item.get("PeriodId") or "")
        if period_id:
            by_period.setdefault(period_id, []).append(item)
    return by_period


def _student_row(sk: str, pupil_id: str, pupil_lookup: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    pupil = pupil_lookup.get(pupil_id, {})
    name = str(pupil.get("Fullname") or pupil.get("FullName") or pupil_id).strip()
    nc_year = str(pupil.get("NCYear") or "").strip()
    form = str(pupil.get("Form") or pupil.get("DivisionName") or "").strip()
    label_parts = [name]
    if nc_year:
        label_parts.append(f"Y{nc_year}")
    if form:
        label_parts.append(form)
    return {
        "school_key": sk,
        "pupil_id": pupil_id,
        "name": name,
        "nc_year": nc_year,
        "label": " - ".join(label_parts),
        "updated_at": datetime.utcnow().isoformat(),
    }


def upsert_students(sb: Any, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    for batch in _chunked(rows, UPSERT_BATCH):
        sb.table(TABLE_STUDENTS).upsert(batch, on_conflict="school_key,pupil_id").execute()


def sync_students_from_isams(sb: Any, sk: str) -> int:
    """Upsert current iSAMS pupils and remove leavers from the students table."""
    from registration_data_wholeyear import fetch_pupil_lookup

    lookup = fetch_pupil_lookup()
    if not lookup:
        return 0
    rows = [_student_row(sk, pupil_id, lookup) for pupil_id in lookup]
    upsert_students(sb, rows)
    # Keep leavers in the table so prior academic years can still show them.
    # Current-year picker uses live iSAMS ids via year studentIds in build_page_data_from_db.
    return len(rows)


def upsert_codes(sb: Any, sk: str, code_rows: List[Dict[str, str]]) -> None:
    if not code_rows:
        return
    rows = [
        {
            "school_key": sk,
            "code": row["code"],
            "name": row.get("name") or "",
            "updated_at": datetime.utcnow().isoformat(),
        }
        for row in code_rows
    ]
    for batch in _chunked(rows, UPSERT_BATCH):
        sb.table(TABLE_CODES).upsert(batch, on_conflict="school_key,code").execute()


def upsert_day_marks(sb: Any, sk: str, day_date: str, marks: Dict[str, Any]) -> None:
    sb.table(TABLE_DAYS).upsert(
        {
            "school_key": sk,
            "day_date": day_date,
            "marks": marks,
            "updated_at": datetime.utcnow().isoformat(),
        },
        on_conflict="school_key,day_date",
    ).execute()


def _slot_to_period_id(data: Dict[str, Any]) -> Dict[str, str]:
    rm = data.get("iSAMS", {}).get("RegistrationManager", {})
    if not rm.get("RegistrationPeriods"):
        return {}
    _, slot_to_id = parse_periods(data)
    return slot_to_id


def ingest_batch_data(
    sb: Any,
    sk: str,
    data: Dict[str, Any],
    school_days: List[Dict[str, str]],
    pupil_lookup: Dict[str, Dict[str, Any]],
) -> int:
    if not school_days:
        return 0

    code_rows, code_by_id = parse_registration_codes(data)
    code_names = registration_code_lookup(code_rows)
    upsert_codes(sb, sk, code_rows)

    slot_to_id = _slot_to_period_id(data)
    if not slot_to_id and school_days:
        first = school_days[0]["iso"]
        last = school_days[-1]["iso"]
        print(f"WARN: no registration periods in iSAMS for {first} → {last} — storing empty days")
    by_period = _index_statuses_by_period(data)
    student_rows: Dict[str, Dict[str, Any]] = {}
    days_written = 0

    for day in school_days:
        day_iso = day["iso"]
        day_marks: Dict[str, Dict[str, Dict[str, str]]] = {}

        for period_name in PERIOD_ORDER:
            period_id = slot_to_id.get(f"{day_iso}|{period_name}")
            if not period_id:
                continue
            for status in by_period.get(period_id, []):
                pupil_id = str(status.get("PupilId") or "")
                if not pupil_id:
                    continue
                mark, css_class, title = attendance_symbol(
                    status, code_by_id=code_by_id, code_names=code_names
                )
                day_marks.setdefault(pupil_id, {})[period_name] = {
                    "mark": mark,
                    "class": css_class,
                    "title": title,
                }
                student_rows[pupil_id] = _student_row(sk, pupil_id, pupil_lookup)

        upsert_day_marks(sb, sk, day_iso, day_marks)
        days_written += 1

    upsert_students(sb, list(student_rows.values()))
    return days_written


def sync_backfill_chunk(
    sb: Any,
    sk: str,
    start_years: List[int],
    *,
    http_method: str = "post",
    catch_up: bool = False,
) -> Tuple[int, bool]:
    from registration_data_wholeyear import month_chunks

    state = ensure_sync_state(sb, sk, start_years)
    backfill_from = datetime.strptime(state["backfill_from"], "%Y-%m-%d").date()
    target = datetime.strptime(state["backfill_target"], "%Y-%m-%d").date()
    if state.get("backfill_through"):
        cursor = datetime.strptime(state["backfill_through"], "%Y-%m-%d").date() + timedelta(days=1)
    else:
        cursor = backfill_from

    if cursor > target:
        return 0, True

    pupil_lookup = fetch_pupil_lookup()
    total_days = 0
    chunks = month_chunks(cursor.isoformat(), target.isoformat())
    if not chunks:
        upsert_sync_state(
            sb, sk, {"backfill_through": target.isoformat(), "backfill_target": target.isoformat()}, start_years=start_years
        )
        return 0, True

    for chunk_start, chunk_end in chunks:
        chunk_start_dt = datetime.strptime(chunk_start, "%Y-%m-%d").date()
        chunk_end_dt = datetime.strptime(chunk_end, "%Y-%m-%d").date()
        school_days = school_days_between(chunk_start, chunk_end)
        print(f"Backfill chunk: {chunk_start} → {chunk_end} ({len(school_days)} weekdays)")
        data = fetch_registration_data(chunk_start, chunk_end, http_method=http_method)
        total_days += ingest_batch_data(sb, sk, data, school_days, pupil_lookup)
        upsert_sync_state(
            sb,
            sk,
            {"backfill_through": chunk_end, "backfill_target": target.isoformat()},
            start_years=start_years,
        )
        if not catch_up:
            break

    state = get_sync_state(sb, sk) or {}
    through = state.get("backfill_through")
    complete = bool(through and datetime.strptime(through, "%Y-%m-%d").date() >= target)
    if complete and not state.get("last_incremental_date"):
        upsert_sync_state(sb, sk, {"last_incremental_date": target.isoformat()}, start_years=start_years)
    return total_days, complete


def _count_days_in_range(sb: Any, sk: str, start_iso: str, end_iso: str) -> int:
    try:
        res = (
            sb.table(TABLE_DAYS)
            .select("day_date", count="exact")
            .eq("school_key", sk)
            .gte("day_date", start_iso)
            .lte("day_date", end_iso)
            .limit(1)
            .execute()
        )
        return int(res.count or 0)
    except Exception:
        return 0


def _reopen_backfill_for_new_years(
    sb: Any, sk: str, start_years: List[int], state: Dict[str, Any]
) -> Dict[str, Any]:
    if not backfill_is_complete(state):
        return state

    _, target = backfill_bounds(start_years)
    through_str = state.get("backfill_through")
    through = datetime.strptime(through_str, "%Y-%m-%d").date() if through_str else None

    rest_from = rest_backfill_from_date() if attendance_source() == "rest" else None
    for sy in sorted(start_years):
        meta = academic_year_meta(sy)
        year_start = datetime.strptime(meta["start"], "%Y-%m-%d").date()
        if rest_from and year_start < rest_from:
            continue
        if year_start > target:
            continue
        year_end = min(datetime.strptime(meta["end"], "%Y-%m-%d").date(), target)
        needs_backfill = False
        if through is None or year_start > through:
            needs_backfill = True
        elif _count_days_in_range(sb, sk, meta["start"], year_end.isoformat()) == 0:
            needs_backfill = True
        if needs_backfill:
            upsert_sync_state(
                sb,
                sk,
                {
                    "backfill_through": (year_start - timedelta(days=1)).isoformat(),
                    "last_incremental_date": None,
                },
                start_years=start_years,
            )
            return get_sync_state(sb, sk) or state
    return state


def refresh_backfill_target(sb: Any, sk: str, start_years: List[int]) -> Dict[str, Any]:
    """Ensure sync row exists, backfill_target is yesterday, and new years trigger backfill."""
    state = ensure_sync_state(sb, sk, start_years)
    return _reopen_backfill_for_new_years(sb, sk, start_years, state)


def backfill_is_complete(state: Dict[str, Any]) -> bool:
    if state.get("last_incremental_date"):
        return True
    through = state.get("backfill_through")
    target = state.get("backfill_target")
    if not through or not target:
        return False
    return through >= target


def sync_incremental_catch_up(
    sb: Any,
    sk: str,
    *,
    http_method: str = "post",
) -> Tuple[int, List[str]]:
    """
    Sync every weekday from the day after last_incremental_date through yesterday.
    Handles missed cron runs (auto previously only synced a single day).
    """
    state = get_sync_state(sb, sk) or {}
    end = yesterday_local()
    last = state.get("last_incremental_date")
    if last:
        start = datetime.strptime(str(last), "%Y-%m-%d").date() + timedelta(days=1)
    else:
        start = end

    if start > end:
        return 0, []

    written: List[str] = []
    total = 0
    day = start
    while day <= end:
        if day.weekday() < 5:
            n = sync_incremental_day(sb, sk, day, http_method=http_method)
            total += n
            written.append(day.isoformat())
        day += timedelta(days=1)
    return total, written


def sync_incremental_day(
    sb: Any,
    sk: str,
    day: date,
    *,
    http_method: str = "post",
) -> int:
    if day.weekday() >= 5:
        print(f"Skip incremental sync for weekend {day.isoformat()}")
        upsert_sync_state(sb, sk, {"last_incremental_date": day.isoformat()})
        return 0

    pupil_lookup = fetch_pupil_lookup()
    iso = day.isoformat()
    school_days = [{"iso": iso, "weekday": day.strftime("%a"), "label": day.strftime("%d %b")}]
    print(f"Incremental sync: {iso}")
    data = fetch_registration_data(iso, iso, http_method=http_method)
    days_written = ingest_batch_data(sb, sk, data, school_days, pupil_lookup)
    upsert_sync_state(sb, sk, {"last_incremental_date": iso})
    return days_written


def load_students(sb: Any, sk: str) -> List[Dict[str, str]]:
    res = sb.table(TABLE_STUDENTS).select("pupil_id,name,nc_year,label").eq("school_key", sk).execute()
    students = [
        {"id": row["pupil_id"], "name": row["name"], "ncYear": row["nc_year"], "label": row["label"]}
        for row in (res.data or [])
    ]
    students.sort(key=lambda row: pupil_sort_key(row["name"], row["ncYear"]))
    return students


def load_codes(sb: Any, sk: str) -> List[Dict[str, str]]:
    res = sb.table(TABLE_CODES).select("code,name").eq("school_key", sk).execute()
    rows = [{"code": row["code"], "name": row["name"]} for row in (res.data or [])]
    rows.sort(key=lambda row: (row["code"] == "-", row["code"]))
    return rows


def load_day_marks_for_range(sb: Any, sk: str, start: str, end: str) -> Dict[str, Dict[str, Dict[str, Dict[str, str]]]]:
    attendance: Dict[str, Dict[str, Dict[str, Dict[str, str]]]] = {}
    res = (
        sb.table(TABLE_DAYS)
        .select("day_date,marks")
        .eq("school_key", sk)
        .gte("day_date", start)
        .lte("day_date", end)
        .execute()
    )
    for row in res.data or []:
        day_iso = str(row["day_date"])
        marks = row.get("marks") or {}
        if not isinstance(marks, dict):
            continue
        for pupil_id, periods in marks.items():
            if isinstance(periods, dict):
                attendance.setdefault(str(pupil_id), {})[day_iso] = periods
    return attendance


def _parse_nc_year(value: Any) -> Optional[int]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        digits = "".join(ch for ch in raw if ch.isdigit())
        return int(digits) if digits else None


def _adjust_form_for_nc_year(form: str, nc_year: str) -> str:
    """If form looks like G6 / Y6, rewrite the year digit to match nc_year."""
    text = str(form or "").strip()
    if not text or not nc_year:
        return text
    # Forms are often G9X / Y10A — rewrite the year digits even when a letter follows.
    return re.sub(r"([GgYy])\d+", lambda m: f"{m.group(1)}{nc_year}", text, count=1)


def _student_label(name: str, nc_year: str, form: str = "") -> str:
    parts = [str(name or "").strip() or "Student"]
    if nc_year:
        parts.append(f"Y{nc_year}")
    if form:
        parts.append(form)
    return " - ".join(parts)


def _year_student_record(
    base: Dict[str, str],
    *,
    year_start: int,
    current_start: int,
    current_ids: set[str],
    last_marked_year_by_id: Dict[str, int],
) -> Dict[str, str]:
    """
    Build a student row as they should appear in one academic year.

    Continuing pupils: roll NC year back from the live iSAMS year.
    Leavers: use last-known NC year as of the latest year they have marks, then roll back.
    """
    pid = str(base.get("id") or "")
    name = str(base.get("name") or pid).strip() or pid
    stored_nc = _parse_nc_year(base.get("ncYear"))
    form = ""
    label = str(base.get("label") or "")
    # Recover form from label when present: "Name - Y6 - G6"
    if " - " in label:
        bits = [b.strip() for b in label.split(" - ") if b.strip()]
        if len(bits) >= 3:
            form = bits[-1]

    if pid in current_ids and stored_nc is not None:
        ref_year = current_start
        ref_nc = stored_nc
    elif stored_nc is not None:
        ref_year = last_marked_year_by_id.get(pid, year_start)
        ref_nc = stored_nc
    else:
        ref_year = year_start
        ref_nc = None

    if ref_nc is None:
        nc_year = str(base.get("ncYear") or "").strip()
    else:
        nc_val = ref_nc - (ref_year - year_start)
        if nc_val < 0:
            nc_val = ref_nc
        nc_year = str(nc_val)

    form_for_year = _adjust_form_for_nc_year(form, nc_year) if nc_year else form
    return {
        "id": pid,
        "name": name,
        "ncYear": nc_year,
        "label": _student_label(name, nc_year, form_for_year),
    }


def build_page_data_from_db(sb: Any, sk: str, start_years: Optional[List[int]] = None) -> Dict[str, Any]:
    start_years = resolve_html_start_years(explicit=start_years)

    students = load_students(sb, sk)
    code_rows = load_codes(sb, sk)
    students_by_id = {str(s["id"]): s for s in students}
    # Live iSAMS roster for the current academic year only (leavers stay in students table
    # for prior-year pickers, but must not appear in the current year list).
    try:
        from registration_data_wholeyear import fetch_pupil_lookup

        current_ids = {str(pid) for pid in (fetch_pupil_lookup() or {})}
    except Exception:
        current_ids = set(students_by_id)

    current_start = academic_year_start_year_on(date.today())
    year_views: List[Dict[str, Any]] = []
    today = date.today()
    # pupil_id -> latest academic start year where they have marks (for leaver year-group math)
    last_marked_year_by_id: Dict[str, int] = {}

    provisional: List[Dict[str, Any]] = []
    for start_year in start_years:
        meta = academic_year_meta(start_year)
        days = school_days_between(meta["start"], meta["end"])
        raw_attendance = load_day_marks_for_range(sb, sk, meta["start"], meta["end"])
        attendance: Dict[str, Dict[str, Dict[str, Dict[str, str]]]] = {}

        for pupil_id, raw_by_day in raw_attendance.items():
            day_map = sparse_pupil_day_map(days, raw_by_day)
            if day_map:
                pid = str(pupil_id)
                attendance[pid] = day_map
                prev = last_marked_year_by_id.get(pid)
                if prev is None or start_year > prev:
                    last_marked_year_by_id[pid] = start_year

        start_d = datetime.strptime(meta["start"], "%Y-%m-%d").date()
        end_d = datetime.strptime(meta["end"], "%Y-%m-%d").date()
        is_current_year = start_d <= today <= end_d
        if is_current_year:
            year_student_ids = sorted(current_ids)
            for pupil_id in year_student_ids:
                attendance.setdefault(str(pupil_id), {})
        else:
            year_student_ids = sorted(attendance.keys())

        provisional.append(
            {
                "meta": meta,
                "start_year": start_year,
                "days": days,
                "attendance": attendance,
                "studentIds": year_student_ids,
                "is_current_year": is_current_year,
            }
        )

    if not provisional:
        raise RuntimeError("No academic year views could be built from Supabase")

    for block in provisional:
        meta = block["meta"]
        start_year = block["start_year"]
        year_students: List[Dict[str, str]] = []
        for pupil_id in block["studentIds"]:
            pid = str(pupil_id)
            base = students_by_id.get(pid) or {
                "id": pid,
                "name": pid,
                "ncYear": "",
                "label": pid,
            }
            if pid not in students_by_id:
                students_by_id[pid] = dict(base)
            year_students.append(
                _year_student_record(
                    base,
                    year_start=start_year,
                    current_start=current_start,
                    current_ids=current_ids,
                    last_marked_year_by_id=last_marked_year_by_id,
                )
            )
        year_students.sort(key=lambda row: pupil_sort_key(row["name"], row["ncYear"]))
        year_views.append(
            {
                "id": meta["id"],
                "label": meta["label"],
                "start": meta["start"],
                "end": meta["end"],
                "days": block["days"],
                "attendance": block["attendance"],
                "studentIds": [s["id"] for s in year_students],
                "students": year_students,
            }
        )

    students = sorted(
        students_by_id.values(),
        key=lambda row: pupil_sort_key(row["name"], row["ncYear"]),
    )

    default_year_id = year_views[-1]["id"]
    for view in year_views:
        start = datetime.strptime(view["start"], "%Y-%m-%d").date()
        end = datetime.strptime(view["end"], "%Y-%m-%d").date()
        if start <= today <= end:
            default_year_id = view["id"]
            break

    return {
        "periods": PERIOD_ORDER,
        "students": students,
        "registrationCodes": code_rows,
        "years": year_views,
        "defaultYearId": default_year_id,
        "generatedAt": datetime.now().strftime("%d %b %Y, %H:%M"),
    }
