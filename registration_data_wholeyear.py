#!/usr/bin/env python3
"""
Fetch registration periods + attendance from iSAMS (REST by default, batch optional) and build
a whole-year (or custom range) HTML grid:

  - Rows: ECA 1 → Pd 6 → ECA 2
  - Columns: school days (Mon–Fri), earliest date on the left
  - Column headers: weekday + date stacked vertically
  - Student picker to view individual attendance marks

Run:
  cd signInApp_server
  ./run_registration_wholeyear.sh                    # --sync auto (default on droplet)
  ./run_registration_wholeyear.sh --sync backfill --catch-up
  ./run_registration_wholeyear.sh --sync incremental
  ../.venv/bin/python registration_data_wholeyear.py --years 3   # legacy in-memory rebuild

View after generate:
  http://127.0.0.1:8080/registration-wholeyear   (with uvicorn running)
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
from dotenv import load_dotenv

from isams_batch_filters import (
    batch_key,
    batch_post_filtered,
    batch_url,
    build_filters_xml,
    registration_manager_keys,
    resolve_filter_style,
)
from isams_registration import REGISTRATION_PERIOD_SPECS

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / ".env", override=True)

STATIC_DIR = _ROOT / "static" / "registration_wholeyear"
HTML_PATH = STATIC_DIR / "attendance.html"
PERIOD_ORDER = [label for _, label in REGISTRATION_PERIOD_SPECS]


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip().strip("'\"")


ACADEMIC_YEAR_START_MONTH = 8
ACADEMIC_YEAR_START_DAY = 25
ACADEMIC_YEAR_END_MONTH = 6
ACADEMIC_YEAR_END_DAY = 30


def academic_year_bounds(start_year: int) -> Tuple[str, str]:
    """Halcyon school year: 25 Aug → 30 Jun."""
    return (
        date(start_year, ACADEMIC_YEAR_START_MONTH, ACADEMIC_YEAR_START_DAY).isoformat(),
        date(start_year + 1, ACADEMIC_YEAR_END_MONTH, ACADEMIC_YEAR_END_DAY).isoformat(),
    )


def academic_year_start_year_on(day: date) -> int:
    boundary = date(day.year, ACADEMIC_YEAR_START_MONTH, ACADEMIC_YEAR_START_DAY)
    if day >= boundary:
        return day.year
    return day.year - 1


def academic_year_meta(start_year: int) -> Dict[str, str]:
    start, end = academic_year_bounds(start_year)
    short_end = start_year + 1
    return {
        "id": f"{start_year}-{short_end}",
        "label": f"{start_year}–{short_end}",
        "start": start,
        "end": end,
    }


def default_academic_start_years(today: Optional[date] = None) -> List[int]:
    """
    Years to embed in the HTML viewer:
    previous academic year and the current one, plus the next when iSAMS is
    likely to expose it (spring term, or within ~2 months of the late-Aug start).
    """
    today = today or date.today()
    current = academic_year_start_year_on(today)
    years = {current - 1, current}
    next_start = date(current + 1, ACADEMIC_YEAR_START_MONTH, ACADEMIC_YEAR_START_DAY)
    days_until_next = (next_start - today).days
    if today.month <= ACADEMIC_YEAR_END_MONTH or days_until_next <= 62:
        years.add(current + 1)
    return sorted(years)


def current_academic_year_range(today: Optional[date] = None) -> Tuple[str, str]:
    """Default single-year CLI range: current academic year (25 Aug → 30 Jun)."""
    start_year = academic_year_start_year_on(today or date.today())
    return academic_year_bounds(start_year)


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _attr(record: Dict[str, Any], name: str) -> Any:
    if name in record:
        return record[name]
    return record.get(f"@{name}")


def month_chunks(start: str, end: str) -> List[Tuple[str, str]]:
    """Split a date range into calendar-month chunks for batch API requests."""
    start_dt = datetime.strptime(start, "%Y-%m-%d").date()
    end_dt = datetime.strptime(end, "%Y-%m-%d").date()
    chunks: List[Tuple[str, str]] = []
    cursor = date(start_dt.year, start_dt.month, 1)
    while cursor <= end_dt:
        if cursor.month == 12:
            next_month = date(cursor.year + 1, 1, 1)
        else:
            next_month = date(cursor.year, cursor.month + 1, 1)
        chunk_end = min(end_dt, next_month - timedelta(days=1))
        chunk_start = max(start_dt, cursor)
        chunks.append((chunk_start.isoformat(), chunk_end.isoformat()))
        cursor = next_month
    return chunks


def _merge_registration_manager(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    src_rm = source.get("iSAMS", {}).get("RegistrationManager")
    if not isinstance(src_rm, dict):
        return
    if "iSAMS" not in target:
        target["iSAMS"] = {}
    tgt_rm = target["iSAMS"].setdefault("RegistrationManager", {})

    for key, value in src_rm.items():
        if key in ("RegistrationPeriods", "RegistrationStatuses", "OutOfSchool"):
            tgt_node = tgt_rm.setdefault(key, {})
            if not isinstance(tgt_node, dict):
                tgt_rm[key] = tgt_node = {}
            for inner_key, inner_value in (value or {}).items():
                if inner_key.startswith("@"):
                    tgt_node[inner_key] = inner_value
                    continue
                existing = _as_list(tgt_node.get(inner_key))
                incoming = _as_list(inner_value)
                tgt_node[inner_key] = existing + incoming
        elif key not in tgt_rm or tgt_rm[key] is None:
            tgt_rm[key] = value


def fetch_batch_data(start_date: str, end_date: str, *, http_method: str = "post") -> Dict[str, Any]:
    chunks = month_chunks(start_date, end_date)
    merged: Dict[str, Any] = {"iSAMS": {"RegistrationManager": {}}}

    for index, (chunk_start, chunk_end) in enumerate(chunks, start=1):
        if len(chunks) > 1:
            print(f"  Fetching chunk {index}/{len(chunks)}: {chunk_start} → {chunk_end}")
        xml = build_filters_xml(chunk_start, chunk_end, style="auto")
        chunk = batch_post_filtered(xml, method=http_method)  # type: ignore[arg-type]
        _merge_registration_manager(merged, chunk)

    return merged


def school_days_between(start: str, end: str) -> List[Dict[str, str]]:
    start_dt = datetime.strptime(start, "%Y-%m-%d").date()
    end_dt = datetime.strptime(end, "%Y-%m-%d").date()
    days: List[Dict[str, str]] = []
    current = start_dt
    while current <= end_dt:
        if current.weekday() < 5:
            days.append(
                {
                    "iso": current.isoformat(),
                    "weekday": current.strftime("%a"),
                    "label": current.strftime("%d %b"),
                }
            )
        current += timedelta(days=1)
    return days


def fetch_pupil_lookup() -> Dict[str, Dict[str, Any]]:
    try:
        from isams_registration_attendance_rest import attendance_source, fetch_pupil_lookup_rest
        from isams_registration import isams_get_token, load_isams_config_from_env

        if attendance_source() == "rest":
            cfg = load_isams_config_from_env()
            token = isams_get_token(cfg)
            return fetch_pupil_lookup_rest(cfg, token)
    except Exception:
        pass

    ref_key = _env("ISAMS_BATCH_REFERENCE_API_KEY") or _env("ISAMS_BATCH_LEGACY_API_KEY")
    if not ref_key:
        return {}
    try:
        resp = requests.post(batch_url(), params={"apiKey": ref_key}, timeout=120)
        resp.raise_for_status()
        pupils = _as_list(
            resp.json()["iSAMS"]["PupilManager"]["CurrentPupils"]["Pupil"]
        )
    except Exception:
        return {}

    lookup: Dict[str, Dict[str, Any]] = {}
    for pupil in pupils:
        school_id = str(pupil.get("SchoolId") or "")
        if school_id:
            lookup[school_id] = pupil
    return lookup


def parse_registration_codes(
    data: Dict[str, Any],
) -> Tuple[List[Dict[str, str]], Dict[str, Dict[str, str]]]:
    rm = data.get("iSAMS", {}).get("RegistrationManager", {})
    codes_node = rm.get("RegistrationCodes", {})
    raw = codes_node.get("RegistrationCode") if isinstance(codes_node, dict) else None
    rows: List[Dict[str, str]] = []
    by_id: Dict[str, Dict[str, str]] = {}
    seen_letters: set[str] = set()
    for item in _as_list(raw):
        code_id = str(_attr(item, "Id") or "")
        letter = str(item.get("Code") or "").strip()
        name = str(item.get("Name") or "").strip()
        if code_id:
            by_id[code_id] = {"code": letter, "name": name}
        if letter and letter not in seen_letters:
            seen_letters.add(letter)
            rows.append({"code": letter, "name": name})
    rows.sort(key=lambda row: (row["code"] == "-", row["code"]))
    return rows, by_id


def registration_code_lookup(codes: List[Dict[str, str]]) -> Dict[str, str]:
    return {row["code"]: row["name"] for row in codes}


def parse_periods(data: Dict[str, Any]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Return period_id -> friendly_name, and 'YYYY-MM-DD|Period name' -> period_id."""
    rm = data.get("iSAMS", {}).get("RegistrationManager", {})
    periods_node = rm.get("RegistrationPeriods")
    if not periods_node:
        raise RuntimeError(
            "RegistrationPeriods missing. Use the OAuth batch key with full filters "
            "(see ISAMS_OAUTH_CLIENT_ID / ISAMS_BATCH_FILTER_STYLE=full)."
        )

    raw = periods_node.get("RegistrationPeriod") if isinstance(periods_node, dict) else periods_node
    id_to_name: Dict[str, str] = {}
    slot_to_id: Dict[str, str] = {}

    for item in _as_list(raw):
        period_id = str(_attr(item, "Id") or "")
        name = str(item.get("FriendlyName") or item.get("friendlyName") or "").strip()
        start = str(item.get("Start") or item.get("start") or "")
        day = start[:10] if len(start) >= 10 else ""
        if period_id and name and day:
            id_to_name[period_id] = name
            slot_to_id[f"{day}|{name}"] = period_id

    return id_to_name, slot_to_id


def attendance_symbol(
    status: Optional[Dict[str, Any]],
    *,
    code_by_id: Dict[str, Dict[str, str]],
    code_names: Dict[str, str],
) -> Tuple[str, str, str]:
    """Return (short mark, css class, title)."""
    if not status:
        return ("-", "empty", "No registration record")

    registered = str(status.get("Registered", "")) == "1"
    late = str(status.get("Late", "")) == "1"
    raw_code = str(status.get("Code") or "").strip()
    reason = str(status.get("Reason") or "").strip()
    minutes = str(status.get("MinutesLate") or "").strip()

    if registered and late:
        title = f"Late ({minutes} min)" if minutes and minutes != "0" else "Late"
        if reason:
            title = f"{title} — {reason}"
        return ("L", "late", title)

    if registered:
        return ("P", "present", "Present")

    if not registered and not raw_code:
        return ("-", "empty", "No registration record")

    if raw_code:
        resolved = code_by_id.get(raw_code, {})
        letter = str(resolved.get("code") or raw_code).strip()
        name = str(resolved.get("name") or code_names.get(letter) or "").strip()
        if name:
            title = f"{letter} — {name}"
        elif reason:
            title = reason
        else:
            title = letter
        return (letter[:3], "absent", title)

    return ("A", "absent", reason or "Absent (no code recorded)")


def is_empty_attendance_cell(cell: Dict[str, str]) -> bool:
    return cell.get("class") == "empty" and str(cell.get("mark") or "-") in ("-", "")


def sparse_pupil_day_map(
    days: List[Dict[str, str]],
    raw_by_day: Dict[str, Dict[str, Dict[str, str]]],
) -> Dict[str, Dict[str, Dict[str, str]]]:
    """Keep only days/periods with a real registration mark (omit empty placeholders)."""
    day_map: Dict[str, Dict[str, Dict[str, str]]] = {}
    for day in days:
        iso = day["iso"]
        stored = raw_by_day.get(iso, {})
        period_cells: Dict[str, Dict[str, str]] = {}
        for period_name in PERIOD_ORDER:
            cell = stored.get(period_name)
            if cell and not is_empty_attendance_cell(cell):
                period_cells[period_name] = cell
        if period_cells:
            day_map[iso] = period_cells
    return day_map


def sparse_pupil_day_map_from_statuses(
    school_days: List[Dict[str, str]],
    pupil_periods: Dict[str, Dict[str, Any]],
    slot_to_id: Dict[str, str],
    *,
    code_by_id: Dict[str, Dict[str, str]],
    code_names: Dict[str, str],
) -> Dict[str, Dict[str, Dict[str, str]]]:
    day_map: Dict[str, Dict[str, Dict[str, str]]] = {}
    for day in school_days:
        iso = day["iso"]
        period_cells: Dict[str, Dict[str, str]] = {}
        for period_name in PERIOD_ORDER:
            period_id = slot_to_id.get(f"{iso}|{period_name}")
            status = pupil_periods.get(period_id) if period_id else None
            mark, css_class, title = attendance_symbol(
                status,
                code_by_id=code_by_id,
                code_names=code_names,
            )
            if css_class == "empty" and mark == "-":
                continue
            period_cells[period_name] = {
                "mark": mark,
                "class": css_class,
                "title": title,
            }
        if period_cells:
            day_map[iso] = period_cells
    return day_map


def pupil_sort_key(name: str, nc_year: str) -> Tuple[int, str]:
    try:
        year = int(nc_year)
    except ValueError:
        year = 999
    return (year, name.casefold())


def build_attendance_model(
    data: Dict[str, Any],
    pupil_lookup: Dict[str, Dict[str, Any]],
    code_names: Dict[str, str],
    code_by_id: Dict[str, Dict[str, str]],
    code_rows: List[Dict[str, str]],
    school_days: List[Dict[str, str]],
) -> Dict[str, Any]:
    id_to_name, slot_to_id = parse_periods(data)

    rm = data.get("iSAMS", {}).get("RegistrationManager", {})
    status_node = rm.get("RegistrationStatuses", {})
    raw_status = status_node.get("RegistrationStatus") if isinstance(status_node, dict) else None

    by_pupil_period: Dict[str, Dict[str, Dict[str, Any]]] = {}
    pupil_ids: set[str] = set()

    for item in _as_list(raw_status):
        pupil_id = str(item.get("PupilId") or "")
        period_id = str(item.get("PeriodId") or "")
        if not pupil_id or not period_id:
            continue
        pupil_ids.add(pupil_id)
        by_pupil_period.setdefault(pupil_id, {})[period_id] = item

    student_rows: List[Dict[str, str]] = []
    attendance: Dict[str, Dict[str, Dict[str, Dict[str, str]]]] = {}

    for pupil_id in pupil_ids:
        pupil = pupil_lookup.get(pupil_id, {})
        name = str(pupil.get("Fullname") or pupil.get("FullName") or pupil_id).strip()
        nc_year = str(pupil.get("NCYear") or "").strip()
        form = str(pupil.get("Form") or pupil.get("DivisionName") or "").strip()
        label_parts = [name]
        if nc_year:
            label_parts.append(f"Y{nc_year}")
        if form:
            label_parts.append(form)
        student_rows.append(
            {
                "id": pupil_id,
                "name": name,
                "ncYear": nc_year,
                "label": " - ".join(label_parts),
            }
        )

        pupil_periods = by_pupil_period.get(pupil_id, {})
        attendance[pupil_id] = sparse_pupil_day_map_from_statuses(
            school_days,
            pupil_periods,
            slot_to_id,
            code_by_id=code_by_id,
            code_names=code_names,
        )

    student_rows.sort(key=lambda row: pupil_sort_key(row["name"], row["ncYear"]))

    return {
        "periods": PERIOD_ORDER,
        "days": school_days,
        "students": student_rows,
        "attendance": attendance,
        "codeNames": code_names,
        "registrationCodes": code_rows,
    }


def merge_student_lists(existing: List[Dict[str, str]], incoming: List[Dict[str, str]]) -> List[Dict[str, str]]:
    by_id = {row["id"]: row for row in existing}
    for row in incoming:
        by_id.setdefault(row["id"], row)
    merged = list(by_id.values())
    merged.sort(key=lambda row: pupil_sort_key(row["name"], row["ncYear"]))
    return merged


def build_multi_year_page_data(
    start_years: List[int],
    *,
    http_method: str = "post",
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    pupil_lookup = fetch_pupil_lookup()
    all_students: List[Dict[str, str]] = []
    year_views: List[Dict[str, Any]] = []
    code_rows: List[Dict[str, str]] = []
    code_by_id: Dict[str, Dict[str, str]] = {}
    period_rows: List[Dict[str, Any]] = []

    for start_year in start_years:
        meta = academic_year_meta(start_year)
        school_days = school_days_between(meta["start"], meta["end"])
        if not school_days:
            continue

        print(f"Fetching {meta['label']}: {meta['start']} → {meta['end']} ({len(school_days)} weekdays)")
        data = fetch_batch_data(meta["start"], meta["end"], http_method=http_method)
        rm = data.get("iSAMS", {}).get("RegistrationManager", {})
        if not rm.get("RegistrationPeriods"):
            print(f"  WARN: no registration periods for {meta['label']} yet — empty grid")
            year_views.append(
                {
                    "id": meta["id"],
                    "label": meta["label"],
                    "start": meta["start"],
                    "end": meta["end"],
                    "days": school_days,
                    "attendance": {},
                }
            )
            continue

        year_codes, year_code_by_id = parse_registration_codes(data)
        code_by_id.update(year_code_by_id)
        if not code_rows:
            code_rows = year_codes

        year_model = build_attendance_model(
            data,
            pupil_lookup,
            registration_code_lookup(year_codes),
            year_code_by_id,
            year_codes,
            school_days,
        )
        all_students = merge_student_lists(all_students, year_model["students"])
        year_views.append(
            {
                "id": meta["id"],
                "label": meta["label"],
                "start": meta["start"],
                "end": meta["end"],
                "days": year_model["days"],
                "attendance": year_model["attendance"],
            }
        )

        _, slot_to_id = parse_periods(data)
        period_rows.extend(
            {
                "id": period_id,
                "friendly_name": slot.split("|", 1)[1],
                "date": slot.split("|", 1)[0],
                "academic_year": meta["label"],
            }
            for slot, period_id in sorted(slot_to_id.items())
        )

    if not year_views:
        raise RuntimeError("No academic year data could be loaded")

    default_year_id = year_views[-1]["id"]
    today = date.today()
    for view in year_views:
        start = datetime.strptime(view["start"], "%Y-%m-%d").date()
        end = datetime.strptime(view["end"], "%Y-%m-%d").date()
        if start <= today <= end:
            default_year_id = view["id"]
            break

    page_data = {
        "periods": PERIOD_ORDER,
        "students": all_students,
        "registrationCodes": code_rows,
        "years": year_views,
        "defaultYearId": default_year_id,
    }
    return page_data, period_rows


def embed_page_json(page_data: Dict[str, Any]) -> str:
    """JSON safe for embedding in <script type=\"application/json\"> (no raw </script>)."""
    return json.dumps(page_data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def build_html(page_data: Dict[str, Any], *, title: str, generated_at: str, range_label: str) -> str:
    page_json = embed_page_json(page_data)

    styles = """
    .page-registration-attendance .ra-content {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text, #1f2937);
      line-height: 1.35;
      padding: 0.5rem 0.65rem 1rem;
      --text: #1f2937;
      --muted: #6b7280;
      --border: #d1d5db;
      --head: #f3f4f6;
      --card: #ffffff;
      --present: #dcfce7;
      --present-border: #86efac;
      --late: #fef3c7;
      --late-border: #fcd34d;
      --absent: #fee2e2;
      --absent-border: #fca5a5;
      --empty: #f9fafb;
      --day-col-width: 33px;
      --day-head-height: 144px;
      --day-head-font: 0.93rem;
      --cell-font: 0.72rem;
    }
    .page-registration-attendance .ra-content .ra-header,
    .page-registration-attendance .ra-content .grid-panel,
    .page-registration-attendance .ra-content .codes-panel {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 20px;
      margin-bottom: 24px;
    }
    .page-registration-attendance .ra-content h1 {
      margin: 0 0 4px;
      font-size: 1.7rem;
    }
    .page-registration-attendance .ra-content h2 {
      margin: 0 0 16px;
      font-size: 1.25rem;
      font-weight: 400;
    }
    .page-registration-attendance .ra-content .header-layout {
      display: block;
    }
    .page-registration-attendance .ra-content .header-main {
      min-width: 0;
    }
    .page-registration-attendance .ra-content .meta-group {
      margin-top: 2px;
    }
    .page-registration-attendance .ra-content .control-row {
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      grid-template-rows: auto auto;
      gap: 10px 16px;
      align-items: end;
      margin-top: 8px;
    }
    .page-registration-attendance .ra-content .control-academic-year {
      grid-column: 1;
      grid-row: 2;
    }
    .page-registration-attendance .ra-content .control-year-group {
      grid-column: 2;
      grid-row: 1;
    }
    .page-registration-attendance .ra-content .control-search {
      grid-column: 2;
      grid-row: 2;
    }
    .page-registration-attendance .ra-content .control-students {
      grid-column: 3;
      grid-row: 1 / span 2;
      align-self: stretch;
    }
    .page-registration-attendance .ra-content .meta {
      color: var(--muted);
      margin: 0;
      font-size: 0.9rem;
      line-height: 1.5;
    }
    .page-registration-attendance .ra-content .field {
      display: flex;
      flex-direction: column;
      gap: 6px;
      min-width: 0;
    }
    .page-registration-attendance .ra-content .field label {
      font-weight: 700;
      font-size: 0.95rem;
      color: var(--text);
    }
    .page-registration-attendance .ra-content .field input,
    .page-registration-attendance .ra-content .field select {
      padding: 8px 10px;
      border: 1px solid var(--border);
      border-radius: 8px;
      font: inherit;
      font-size: 0.9rem;
      background: #fff;
      color: var(--text);
      width: 100%;
      height: 38px;
      min-height: 38px;
      box-sizing: border-box;
    }
    .page-registration-attendance .ra-content .field.field-students select[multiple] {
      flex: 1 1 auto;
      height: auto;
      min-height: 96px;
      padding: 6px 8px;
    }
    .page-registration-attendance .ra-content .field-hint {
      font-weight: 400;
      font-size: 0.78rem;
      color: var(--muted);
    }
    .page-registration-attendance .ra-content .legend {
      display: flex;
      gap: 16px;
      margin-top: 0;
      margin-bottom: 16px;
      flex-wrap: wrap;
    }
    .page-registration-attendance .ra-content .legend-item {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 0.9rem;
      color: var(--text);
    }
    .page-registration-attendance .ra-content .legend-swatch {
      width: 18px;
      height: 18px;
      border-radius: 4px;
      border: 1px solid var(--border);
    }
    .page-registration-attendance .ra-content .legend-swatch.present {
      background: var(--present);
      border-color: var(--present-border);
    }
    .page-registration-attendance .ra-content .legend-swatch.late {
      background: var(--late);
      border-color: var(--late-border);
    }
    .page-registration-attendance .ra-content .legend-swatch.absent {
      background: var(--absent);
      border-color: var(--absent-border);
    }
    .page-registration-attendance .ra-content .legend-swatch.empty {
      background: var(--empty);
    }
    .page-registration-attendance .ra-content .panel-title {
      margin: 0 0 12px;
      font-size: 0.95rem;
      font-weight: 700;
    }
    .page-registration-attendance .ra-content .panel-subtitle {
      margin: 0 0 8px;
      font-size: 0.95rem;
      font-weight: 700;
    }
    .page-registration-attendance .ra-content .grid-scroll {
      overflow: auto;
      max-height: min(75vh, calc(100vh - 360px));
      width: 100%;
    }
    .page-registration-attendance .ra-content table {
      border-collapse: collapse;
      table-layout: fixed;
    }
    .page-registration-attendance .ra-content th,
    .page-registration-attendance .ra-content td {
      border: 1px solid var(--border);
      text-align: center;
      vertical-align: middle;
      padding: 0;
      font-weight: 400;
    }
    .page-registration-attendance .ra-content th.period-head,
    .page-registration-attendance .ra-content td.period-head {
      position: sticky;
      left: 0;
      z-index: 2;
      background: var(--head);
      font-weight: 400;
      font-size: var(--day-head-font);
      min-width: 64px;
      padding: 6px 8px;
      text-align: left;
      color: var(--muted);
    }
    .page-registration-attendance .ra-content thead th {
      position: sticky;
      top: 0;
      z-index: 3;
      background: var(--head);
    }
    .page-registration-attendance .ra-content thead th.day-col {
      min-width: var(--day-col-width);
      max-width: var(--day-col-width);
      width: var(--day-col-width);
      height: var(--day-head-height);
      padding: 2px 0;
      vertical-align: bottom;
    }
    .page-registration-attendance .ra-content thead th.corner {
      left: 0;
      z-index: 4;
      min-width: 64px;
      width: 64px;
      font-size: 0.72rem;
      font-weight: 400;
      color: var(--muted);
      text-align: left;
      padding: 6px 8px;
    }
    .page-registration-attendance .ra-content .day-header-vertical {
      writing-mode: vertical-rl;
      transform: rotate(180deg);
      white-space: nowrap;
      font-size: var(--day-head-font);
      font-weight: 400;
      line-height: 1;
      display: inline-block;
      color: var(--muted);
    }
    .page-registration-attendance .ra-content td.cell {
      width: var(--day-col-width);
      min-width: var(--day-col-width);
      max-width: var(--day-col-width);
      height: 28px;
      font-size: var(--cell-font);
      font-weight: 400;
      padding: 0;
    }
    .page-registration-attendance .ra-content tr.student-label-row th.student-label {
      font-weight: 700;
      font-size: 0.85rem;
      color: var(--text);
      text-align: left;
      background: #eef2f7;
    }
    .page-registration-attendance .ra-content tr.student-label-row td.student-label-gap {
      background: #eef2f7;
      border-bottom: 2px solid var(--border);
    }
    .page-registration-attendance .ra-content tr.student-gap-row th.gap-cell,
    .page-registration-attendance .ra-content tr.student-gap-row td.gap-cell {
      height: 10px;
      padding: 0;
      border-left: 1px solid var(--border);
      border-right: 1px solid var(--border);
      background: var(--card);
    }
    .page-registration-attendance .ra-content tr.student-gap-row th.gap-cell {
      border-left: 1px solid var(--border);
      background: var(--card);
      left: 0;
    }
    .page-registration-attendance .ra-content tr.student-gap-row td.gap-cell {
      border-top: none;
      border-bottom: none;
    }
    .page-registration-attendance .ra-content td.cell.present { background: var(--present); }
    .page-registration-attendance .ra-content td.cell.late { background: var(--late); }
    .page-registration-attendance .ra-content td.cell.absent { background: var(--absent); }
    .page-registration-attendance .ra-content td.cell.empty {
      background: var(--empty);
      color: var(--muted);
      font-weight: 400;
    }
    .page-registration-attendance .ra-content th.day-col.is-today {
      background: #fef9c3;
    }
    .page-registration-attendance .ra-content td.cell.is-today-col {
      box-shadow: inset 0 0 0 1px #facc15;
    }
    .page-registration-attendance .ra-content .code-key-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
      gap: 8px 16px;
      font-size: 0.85rem;
    }
    .page-registration-attendance .ra-content .code-key-item {
      display: flex;
      gap: 8px;
      align-items: baseline;
    }
    .page-registration-attendance .ra-content .code-key-item strong {
      flex: 0 0 auto;
      min-width: 2rem;
      text-align: center;
      background: var(--head);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 1px 4px;
      font-size: 0.78rem;
      font-weight: 600;
    }
    """

    body_inner = f"""
    <div class="wrap">
    <header class="ra-header">
      <div class="header-layout">
        <div class="header-main">
          <h1>Registration attendance</h1>
          <div class="meta-group">
            <p class="meta">Generated {html.escape(generated_at)}</p>
          </div>
          <div class="control-row">
            <div class="field control-academic-year">
              <label for="year-picker">Academic year</label>
              <select id="year-picker" aria-label="Academic year"></select>
            </div>
            <div class="field control-year-group">
              <label for="year-group-compare">Compare year group</label>
              <select id="year-group-compare" aria-label="Add a whole year group to comparison">
                <option value="">Select year group…</option>
              </select>
            </div>
            <div class="field control-search">
              <label for="student-search">Search student</label>
              <input id="student-search" type="search" placeholder="Type a name…" autocomplete="off">
            </div>
            <div class="field field-students control-students">
              <label for="student-picker">Students <span class="field-hint">(Ctrl/Cmd+click for multiple)</span></label>
              <select id="student-picker" multiple size="6" aria-label="Select students to compare"></select>
            </div>
          </div>
        </div>
      </div>
    </header>
    <section class="grid-panel">
      <div class="grid-scroll">
        <table id="attendance-grid">
          <thead id="grid-head"></thead>
          <tbody id="grid-body"></tbody>
        </table>
      </div>
    </section>
    <section class="codes-panel">
      <h2 class="panel-title">Key</h2>
      <div class="legend">
        <div class="legend-item"><span class="legend-swatch present"></span> P Present</div>
        <div class="legend-item"><span class="legend-swatch late"></span> L Late</div>
        <div class="legend-item"><span class="legend-swatch absent"></span> Code / A Absent</div>
        <div class="legend-item"><span class="legend-swatch empty"></span> - No record</div>
      </div>
      <h3 class="panel-subtitle">Registration codes</h3>
      <div class="code-key-grid" id="code-key"></div>
    </section>
    </div>
"""

    tail_scripts = f"""
  <script id="page-data" type="application/json">{page_json}</script>
  <script>
    const DATA = JSON.parse(document.getElementById("page-data").textContent);
    const yearPicker = document.getElementById("year-picker");
    const studentSearch = document.getElementById("student-search");
    const yearGroupCompare = document.getElementById("year-group-compare");
    const studentPicker = document.getElementById("student-picker");
    const gridHead = document.getElementById("grid-head");
    const gridBody = document.getElementById("grid-body");
    const codeKey = document.getElementById("code-key");
    const gridScroll = document.querySelector(".grid-scroll");

    let activeYear = null;

    function getTodayIso() {{
      const today = new Date();
      return [
        today.getFullYear(),
        String(today.getMonth() + 1).padStart(2, "0"),
        String(today.getDate()).padStart(2, "0"),
      ].join("-");
    }}

    function pickDefaultYearId(years) {{
      if (DATA.defaultYearId && years.some((year) => year.id === DATA.defaultYearId)) {{
        return DATA.defaultYearId;
      }}
      const todayIso = getTodayIso();
      const todayTime = new Date(todayIso + "T00:00:00").getTime();
      for (const year of years) {{
        const startTime = new Date(year.start + "T00:00:00").getTime();
        const endTime = new Date(year.end + "T00:00:00").getTime();
        if (todayTime >= startTime && todayTime <= endTime) {{
          return year.id;
        }}
      }}
      return years[years.length - 1] && years[years.length - 1].id;
    }}

    function pickScrollDayIso(days) {{
      const todayIso = getTodayIso();
      if (days.some((day) => day.iso === todayIso)) {{
        return todayIso;
      }}
      const todayTime = new Date(todayIso + "T00:00:00").getTime();
      const nextDay = days.find((day) => new Date(day.iso + "T00:00:00").getTime() >= todayTime);
      return nextDay ? nextDay.iso : (days.length ? days[days.length - 1].iso : null);
    }}

    function scrollToDayColumn(iso) {{
      if (!iso || !gridScroll || !activeYear) return;
      const colIndex = activeYear.days.findIndex((day) => day.iso === iso);
      if (colIndex < 0) return;
      const dayCell = gridHead.querySelectorAll("th.day-col")[colIndex];
      if (!dayCell) return;
      const targetLeft = dayCell.offsetLeft - (gridScroll.clientWidth / 2) + (dayCell.offsetWidth / 2);
      gridScroll.scrollLeft = Math.max(0, targetLeft);
    }}

    function yearById(id) {{
      return (DATA.years || []).find((year) => year.id === id) || null;
    }}

    function ensureYearPicker() {{
      const years = DATA.years || [];
      yearPicker.innerHTML = years
        .map((year) => `<option value="${{escapeHtml(year.id)}}">${{escapeHtml(year.label)}} (${{escapeHtml(year.start)}} – ${{escapeHtml(year.end)}})</option>`)
        .join("");
      const defaultId = pickDefaultYearId(years);
      if (defaultId && years.some((year) => year.id === defaultId)) {{
        yearPicker.value = defaultId;
      }}
      activeYear = yearById(yearPicker.value);
    }}

    function onYearChange() {{
      activeYear = yearById(yearPicker.value);
      populateYearGroupCompare();
      renderPicker();
    }}

    function escapeHtml(value) {{
      return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }}

    function filteredStudents() {{
      const q = studentSearch.value.trim().toLowerCase();
      let list = studentsForActiveYear();
      if (!q) return list;
      return list.filter((s) => s.label.toLowerCase().includes(q));
    }}

    function studentsForActiveYear() {{
      if (!activeYear) return DATA.students || [];
      // Prefer per-year student rows (correct NC year / form for that academic year).
      if (Array.isArray(activeYear.students) && activeYear.students.length) {{
        return activeYear.students;
      }}
      const roster = activeYear.studentIds;
      if (Array.isArray(roster) && roster.length) {{
        const allowed = new Set(roster.map(String));
        return (DATA.students || []).filter((s) => allowed.has(String(s.id)));
      }}
      const att = activeYear.attendance || {{}};
      return (DATA.students || []).filter((s) => {{
        const days = att[s.id];
        return days && typeof days === "object" && Object.keys(days).length > 0;
      }});
    }}

    function selectedStudentIds() {{
      return Array.from(studentPicker.selectedOptions).map((option) => option.value);
    }}

    function setSelectedStudentIds(ids) {{
      const idSet = new Set(ids);
      Array.from(studentPicker.options).forEach((option) => {{
        option.selected = idSet.has(option.value);
      }});
    }}

    function studentById(id) {{
      if (activeYear && Array.isArray(activeYear.students) && activeYear.students.length) {{
        const hit = activeYear.students.find((s) => String(s.id) === String(id));
        if (hit) return hit;
      }}
      return (DATA.students || []).find((s) => s.id === id) || null;
    }}

    function populateYearGroupCompare() {{
      const groups = [...new Set(studentsForActiveYear().map((s) => s.ncYear).filter(Boolean))]
        .sort((a, b) => Number(a) - Number(b));
      const prev = yearGroupCompare.value;
      yearGroupCompare.innerHTML =
        `<option value="">Select year group…</option>` +
        groups.map((year) => `<option value="${{escapeHtml(year)}}">Year ${{escapeHtml(year)}}</option>`).join("");
      if (prev && groups.map(String).includes(String(prev))) yearGroupCompare.value = prev;
    }}

    function onYearGroupCompare() {{
      const ncYear = yearGroupCompare.value;
      if (!ncYear) return;
      const ids = new Set(selectedStudentIds());
      filteredStudents()
        .filter((s) => s.ncYear === ncYear)
        .forEach((s) => ids.add(s.id));
      const nextIds = [...ids];
      if (nextIds.length) setSelectedStudentIds(nextIds);
      yearGroupCompare.value = "";
      renderGrid();
    }}

    function renderPeriodRows(pupilId) {{
      const rows = activeYear.attendance[pupilId] || {{}};
      const todayIso = getTodayIso();
      return DATA.periods.map((periodName) => {{
        const cells = activeYear.days.map((day) => {{
          const cell = (rows[day.iso] && rows[day.iso][periodName]) || {{ mark: "-", class: "empty", title: "No registration record" }};
          const todayClass = day.iso === todayIso ? " is-today-col" : "";
          return `<td class="cell ${{escapeHtml(cell.class)}}${{todayClass}}" title="${{escapeHtml(cell.title)}}">${{escapeHtml(cell.mark)}}</td>`;
        }}).join("");
        return `<tr><th class="period-head">${{escapeHtml(periodName)}}</th>${{cells}}</tr>`;
      }}).join("");
    }}

    function renderGapRow() {{
      const gapCells = activeYear.days.map(() => `<td class="cell gap-cell"></td>`).join("");
      return `<tr class="student-gap-row"><th class="period-head gap-cell"></th>${{gapCells}}</tr>`;
    }}

    function renderStudentLabelRow(student) {{
      const label = student ? student.label : "Student";
      const gapCells = activeYear.days.map(() => `<td class="cell student-label-gap"></td>`).join("");
      return `<tr class="student-label-row"><th class="period-head student-label">${{escapeHtml(label)}}</th>${{gapCells}}</tr>`;
    }}

    function renderCodeKey() {{
      const codes = DATA.registrationCodes || [];
      codeKey.innerHTML = codes.map((item) =>
        `<div class="code-key-item"><strong>${{escapeHtml(item.code)}}</strong><span>${{escapeHtml(item.name)}}</span></div>`
      ).join("");
    }}

    function renderPicker() {{
      const students = filteredStudents();
      const prevSelected = new Set(selectedStudentIds());
      studentPicker.innerHTML = students
        .map((s) => `<option value="${{escapeHtml(s.id)}}">${{escapeHtml(s.label)}}</option>`)
        .join("");
      const validPrev = students.filter((s) => prevSelected.has(s.id)).map((s) => s.id);
      if (validPrev.length) setSelectedStudentIds(validPrev);
      else if (students.length) setSelectedStudentIds([students[0].id]);
      renderGrid();
    }}

    function renderGrid() {{
      if (!activeYear) return;
      const pupilIds = selectedStudentIds();
      const todayIso = getTodayIso();
      const scrollIso = pickScrollDayIso(activeYear.days);
      const dayHeaders = activeYear.days.map((day) => {{
        const todayClass = day.iso === todayIso ? " is-today" : "";
        return `<th class="day-col${{todayClass}}"><span class="day-header-vertical">${{escapeHtml(day.weekday + " " + day.label)}}</span></th>`;
      }}).join("");
      gridHead.innerHTML = `<tr><th class="corner">Period</th>${{dayHeaders}}</tr>`;

      if (!pupilIds.length) {{
        gridBody.innerHTML = `<tr><th class="period-head">—</th>${{activeYear.days.map(() => `<td class="cell empty">—</td>`).join("")}}</tr>`;
        return;
      }}

      gridBody.innerHTML = pupilIds.map((pupilId, index) => {{
        const student = studentById(pupilId);
        let block = renderStudentLabelRow(student) + renderPeriodRows(pupilId);
        if (index < pupilIds.length - 1) {{
          block += renderGapRow() + renderGapRow();
        }}
        return block;
      }}).join("");

      requestAnimationFrame(() => scrollToDayColumn(scrollIso));
    }}

    yearPicker.addEventListener("change", onYearChange);
    studentSearch.addEventListener("input", renderPicker);
    studentPicker.addEventListener("change", renderGrid);
    yearGroupCompare.addEventListener("change", onYearGroupCompare);
    renderCodeKey();
    populateYearGroupCompare();
    ensureYearPicker();
    renderPicker();
  </script>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" href="/static/signin/styles.css" />
  <link rel="stylesheet" href="/static/signin/halcyon-shell.css" />
  <link rel="stylesheet" href="/static/signin/halcyon-subpages.css?v=15" />
  <link rel="stylesheet" href="/static/signin/halcyon-tools-section.css?v=4" />
  <style>{styles}</style>
</head>
<body class="halcyon-page halcyon-tools-section">
  <script src="/static/signin/halcyon-registration-attendance-gate.js?v=1"></script>
  <div class="halcyon-app">
    <div id="halcyonNavMount"></div>
    <script>window.HALCYON_NAV_MODE = 'tools'; window.HALCYON_SECTION_PAGE = 'registrationattendance';</script>
    <script src="/static/signin/halcyon-nav.js?v=32"></script>
    <script src="/static/signin/halcyon-shell.js?v=9"></script>
    <main class="halcyon-main page-registration-attendance">
      <div class="ra-content">
        {body_inner}
      </div>
    </main>
  </div>
{tail_scripts}
</body>
</html>
"""


def upload_to_supabase_storage(
    local_path: Path, remote_name: str, content_type: str = "text/html; charset=utf-8"
) -> bool:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    bucket = os.environ.get(
        "SUPABASE_REGISTRATION_WHOLEYEAR_BUCKET",
        os.environ.get("SUPABASE_TIMETABLED_ROOMS_BUCKET", "timetabled-rooms"),
    ).strip()
    if not url or not key:
        print("WARN: Supabase not configured — HTML saved locally only.", file=sys.stderr)
        return False
    try:
        from supabase_storage_http import upload_storage_file

        upload_storage_file(bucket, remote_name, local_path, content_type=content_type)
        print(f"Uploaded to Supabase storage {bucket}/{remote_name}")
        return True
    except Exception as exc:
        print(f"WARN: could not upload {remote_name}: {exc}", file=sys.stderr)
        return False


def write_periods_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    pd.DataFrame(rows).to_csv(path, index=False)


def resolve_start_years(
    args: argparse.Namespace,
    sb: Any = None,
    sk: Optional[str] = None,
) -> List[int]:
    if args.years and args.years > 0:
        current = academic_year_start_year_on(date.today())
        explicit = [current - offset for offset in range(args.years - 1, -1, -1)]
    else:
        explicit = None
    if sb is not None and sk:
        from registration_attendance_db import resolve_sync_start_years

        return resolve_sync_start_years(sb, sk, explicit=explicit)
    if explicit:
        return explicit
    return default_academic_start_years()


def build_and_upload_from_db(
    sb: Any,
    sk: str,
    start_years: List[int],
    args: argparse.Namespace,
) -> None:
    from registration_attendance_db import build_page_data_from_db, resolve_html_start_years, save_html_cache

    html_years = start_years if args.years and args.years > 0 else resolve_html_start_years()
    t0 = datetime.now()
    print(f"Building page data from Supabase ({len(html_years)} academic year(s))…")
    page_data = build_page_data_from_db(sb, sk, html_years)
    print(f"Page data built in {(datetime.now() - t0).total_seconds():.1f}s")
    html_path = None if args.no_html else Path(args.html or HTML_PATH)
    if not html_path:
        return

    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = page_data.get("generatedAt") or datetime.now().strftime("%d %b %Y, %H:%M")
    year_labels = ", ".join(year["label"] for year in page_data["years"])
    html_content = build_html(
        page_data,
        title="Registration attendance",
        generated_at=generated_at,
        range_label=year_labels,
    )
    tmp_path = html_path.with_suffix(".html.tmp")
    tmp_path.write_text(html_content, encoding="utf-8")
    tmp_path.replace(html_path)
    print(f"Saved HTML: {html_path}")
    try:
        save_html_cache(sb, sk, html_content, start_years=start_years)
        print(
            "Saved HTML cache to Supabase "
            f"(halcyon_registration_attendance_sync.html_content, {len(html_content)//1024}KB raw)"
        )
    except Exception as exc:
        print(f"ERROR: could not save HTML cache to Supabase: {exc}", file=sys.stderr)
        raise
    if upload_to_supabase_storage(html_path, "registration_wholeyear_attendance.html"):
        print("Uploaded HTML to Supabase Storage (optional CDN copy)")
    print("View in app: http://127.0.0.1:8080/registration-wholeyear")

    total_days = sum(len(year["days"]) for year in page_data["years"])
    print(f"School days (all years): {total_days}")
    print(f"Students: {len(page_data['students'])}")


def run_db_sync(args: argparse.Namespace) -> None:
    from registration_attendance_db import (
        backfill_is_complete,
        refresh_backfill_target,
        require_supabase,
        school_key,
        sync_backfill_chunk,
        sync_incremental_catch_up,
        sync_incremental_day,
        sync_students_from_isams,
        yesterday_local,
    )

    from isams_registration_attendance_rest import attendance_source, rest_backfill_from_date

    sb = require_supabase()
    sk = school_key()
    start_years = resolve_start_years(args, sb, sk)
    source = attendance_source()
    print(f"Attendance source: {source}")
    if source == "rest":
        print(f"REST backfill from: {rest_backfill_from_date().isoformat()} (prior years stay in Supabase)")
    print(f"Academic years: {', '.join(academic_year_meta(y)['label'] for y in start_years)}")
    print(f"Supabase school_key: {sk}")

    mode = args.sync

    def refresh_students() -> None:
        count = sync_students_from_isams(sb, sk)
        if count:
            print(f"Student list: refreshed {count} pupil(s) from iSAMS")
        else:
            print("WARN: student list unchanged (iSAMS pupil lookup unavailable)", file=sys.stderr)

    if mode == "build-html":
        refresh_students()
        build_and_upload_from_db(sb, sk, start_years, args)
        return

    if mode == "refresh-students":
        refresh_students()
        if not args.no_html:
            build_and_upload_from_db(sb, sk, start_years, args)
        return

    refresh_students()
    refresh_backfill_target(sb, sk, start_years)

    if getattr(args, "refresh_from", None):
        from datetime import timedelta

        from registration_attendance_db import upsert_sync_state

        refresh_day = datetime.strptime(args.refresh_from, "%Y-%m-%d").date()
        upsert_sync_state(
            sb,
            sk,
            {
                "backfill_through": (refresh_day - timedelta(days=1)).isoformat(),
                "last_incremental_date": None,
            },
            start_years=start_years,
        )
        print(f"Reset backfill cursor to re-sync from {args.refresh_from}")

    if mode == "backfill":
        days, complete = sync_backfill_chunk(
            sb, sk, start_years, http_method=args.method, catch_up=args.catch_up
        )
        print(f"Backfill wrote {days} weekday(s); complete={complete}")
        if not args.no_html:
            build_and_upload_from_db(sb, sk, start_years, args)
        return

    if mode == "incremental":
        day = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else yesterday_local()
        days = sync_incremental_day(sb, sk, day, http_method=args.method)
        print(f"Incremental wrote {days} day(s) for {day.isoformat()}")
        if not args.no_html:
            build_and_upload_from_db(sb, sk, start_years, args)
        return

    if mode == "auto":
        state = refresh_backfill_target(sb, sk, start_years)
        if backfill_is_complete(state):
            end = yesterday_local()
            last = state.get("last_incremental_date")
            if last == end.isoformat():
                print(f"Already synced through {end.isoformat()}")
            else:
                days, synced = sync_incremental_catch_up(sb, sk, http_method=args.method)
                if synced:
                    print(f"Auto incremental: {days} weekday row(s) for {', '.join(synced)}")
                else:
                    print(f"Auto incremental: nothing to sync after {last or '—'} (through {end.isoformat()})")
        else:
            catch_up = source == "rest"
            days, complete = sync_backfill_chunk(
                sb, sk, start_years, http_method=args.method, catch_up=catch_up
            )
            print(f"Auto backfill: {days} weekday(s); complete={complete}")
        if not args.no_html:
            build_and_upload_from_db(sb, sk, start_years, args)
        return

    raise SystemExit(f"Unknown sync mode: {mode}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch API registration attendance grid")
    parser.add_argument(
        "--start",
        help=f"Single-range mode only: start date (default whole-year mode uses Aug 25 → Jun 30 years)",
    )
    parser.add_argument("--end", help="Single-range mode only: end date")
    parser.add_argument(
        "--years",
        type=int,
        default=0,
        metavar="N",
        help="Embed N academic years in HTML (0 = auto: previous, current, and next in spring)",
    )
    parser.add_argument("--method", choices=("post", "get"), default="post")
    parser.add_argument("--html", nargs="?", const=str(HTML_PATH), metavar="PATH", default=str(HTML_PATH))
    parser.add_argument("--no-html", action="store_true", help="Skip HTML generation")
    parser.add_argument("--csv", metavar="PATH", help="Optional CSV of registration periods")
    parser.add_argument("--open", action="store_true", help="Open HTML in browser after generate")
    parser.add_argument("--json", action="store_true", help="Print attendance JSON summary to stdout")
    parser.add_argument(
        "--sync",
        choices=("backfill", "incremental", "build-html", "refresh-students", "auto"),
        help=(
            "Supabase-backed sync: backfill (month chunks), incremental (one day), "
            "build-html (from DB only), refresh-students (iSAMS pupil list + HTML), "
            "auto (backfill until caught up, then yesterday daily)"
        ),
    )
    parser.add_argument(
        "--catch-up",
        action="store_true",
        help="With --sync backfill: process all pending month chunks in one run",
    )
    parser.add_argument(
        "--date",
        help="With --sync incremental: YYYY-MM-DD (default: yesterday in school timezone)",
    )
    parser.add_argument(
        "--refresh-from",
        metavar="YYYY-MM-DD",
        help="Re-sync from this date (resets backfill cursor before backfill/auto)",
    )
    args = parser.parse_args()

    if args.sync:
        from isams_registration_attendance_rest import attendance_source

        if attendance_source() != "rest":
            try:
                key = batch_key()
            except RuntimeError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                raise SystemExit(1) from exc
            style = resolve_filter_style()
            print(f"Batch URL: {batch_url()}")
            print(f"Batch key: ...{key[-8:]}")
            print(f"Filter style: {style}")
        print(f"Sync mode: {args.sync}")
        try:
            run_db_sync(args)
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        return

    try:
        key = batch_key()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    style = resolve_filter_style()
    print(f"Batch URL: {batch_url()}")
    print(f"Batch key: ...{key[-8:]}")
    print(f"Filter style: {style}")

    single_range_mode = bool(args.start or args.end)
    if single_range_mode and (not args.start or not args.end):
        print("ERROR: single-range mode requires both --start and --end", file=sys.stderr)
        raise SystemExit(1)

    try:
        pupil_lookup = fetch_pupil_lookup()
        if single_range_mode:
            print(f"Date range: {args.start} → {args.end}")
            school_days = school_days_between(args.start, args.end)
            if not school_days:
                print("ERROR: no weekdays in selected date range", file=sys.stderr)
                raise SystemExit(1)
            data = fetch_batch_data(args.start, args.end, http_method=args.method)
            rm_keys = registration_manager_keys(data)
            if not data.get("iSAMS", {}).get("RegistrationManager", {}).get("RegistrationPeriods"):
                print(
                    "WARN: no registration periods in iSAMS for this range yet — "
                    "generating an empty grid.",
                    file=sys.stderr,
                )
                page_data = {
                    "periods": PERIOD_ORDER,
                    "students": [],
                    "registrationCodes": [],
                    "generatedAt": datetime.now().strftime("%d %b %Y, %H:%M"),
                    "years": [
                        {
                            "id": f"{args.start}_{args.end}",
                            "label": f"{args.start} – {args.end}",
                            "start": args.start,
                            "end": args.end,
                            "days": school_days,
                            "attendance": {},
                        }
                    ],
                    "defaultYearId": f"{args.start}_{args.end}",
                }
                period_rows = []
            else:
                code_rows, code_by_id = parse_registration_codes(data)
                code_names = registration_code_lookup(code_rows)
                year_model = build_attendance_model(
                    data, pupil_lookup, code_names, code_by_id, code_rows, school_days
                )
                year_id = f"{args.start}_{args.end}"
                page_data = {
                    "periods": PERIOD_ORDER,
                    "students": year_model["students"],
                    "registrationCodes": code_rows,
                    "generatedAt": datetime.now().strftime("%d %b %Y, %H:%M"),
                    "years": [
                        {
                            "id": year_id,
                            "label": f"{args.start} – {args.end}",
                            "start": args.start,
                            "end": args.end,
                            "days": year_model["days"],
                            "attendance": year_model["attendance"],
                        }
                    ],
                    "defaultYearId": year_id,
                }
                _, slot_to_id = parse_periods(data)
                period_rows = [
                    {
                        "id": period_id,
                        "friendly_name": slot.split("|", 1)[1],
                        "date": slot.split("|", 1)[0],
                        "academic_year": year_id,
                    }
                    for slot, period_id in sorted(slot_to_id.items())
                ]
        else:
            if args.years and args.years > 0:
                start_years = []
                current = academic_year_start_year_on(date.today())
                for offset in range(args.years - 1, -1, -1):
                    start_years.append(current - offset)
            else:
                start_years = default_academic_start_years()
            print(f"Academic years: {', '.join(academic_year_meta(y)['label'] for y in start_years)}")
            page_data, period_rows = build_multi_year_page_data(
                start_years,
                http_method=args.method,
            )
            page_data["generatedAt"] = datetime.now().strftime("%d %b %Y, %H:%M")
            rm_keys = ["multi-year"]
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    total_days = sum(len(year["days"]) for year in page_data["years"])
    print(f"RegistrationManager keys: {rm_keys}")
    print(f"School days (all years): {total_days}")
    print(f"Students: {len(page_data['students'])}")
    if not pupil_lookup:
        print(
            "WARN: pupil names unavailable — set ISAMS_BATCH_REFERENCE_API_KEY for "
            "PupilManager (timetables use the same key).",
            file=sys.stderr,
        )

    html_path = None if args.no_html else Path(args.html or HTML_PATH)
    if html_path:
        STATIC_DIR.mkdir(parents=True, exist_ok=True)
        generated_at = page_data.get("generatedAt") or datetime.now().strftime("%d %b %Y, %H:%M")
        year_labels = ", ".join(year["label"] for year in page_data["years"])
        range_label = year_labels
        html_content = build_html(
            page_data,
            title="Registration attendance",
            generated_at=generated_at,
            range_label=range_label,
        )
        tmp_path = html_path.with_suffix(".html.tmp")
        tmp_path.write_text(html_content, encoding="utf-8")
        tmp_path.replace(html_path)
        print(f"Saved HTML: {html_path}")
        upload_to_supabase_storage(html_path, "registration_wholeyear_attendance.html")
        print("View in app: http://127.0.0.1:8080/registration-wholeyear")

    if args.csv:
        write_periods_csv(period_rows, Path(args.csv))
        print(f"Wrote {args.csv}")

    if args.json:
        print(json.dumps(page_data, indent=2, ensure_ascii=False))

    if args.open and html_path:
        webbrowser.open(html_path.resolve().as_uri())


if __name__ == "__main__":
    main()
