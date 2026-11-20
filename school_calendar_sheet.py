"""Parse Halcyon school calendar Google Sheet into dated events for the academic-year list."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

_REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_SHEET_ID = "1bzBjTADwueeaHEaYdGU7yZHcKh2IrUaNLXdAcKF0tV4"
DEFAULT_WORKSHEET_GID = 1541006323
DEFAULT_RANGE = "A1:AM38"

_CACHE: Dict[str, Any] = {"at": 0.0, "events": None}
_CACHE_TTL_SECONDS = 300

_MONTH_TITLE_RE = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})$"
)
_MONTH_NUM = {
    "January": 1,
    "February": 2,
    "March": 3,
    "April": 4,
    "May": 5,
    "June": 6,
    "July": 7,
    "August": 8,
    "September": 9,
    "October": 10,
    "November": 11,
    "December": 12,
}

_WEEKEND_FILL = (255, 204, 153)
_SKIP_DAY_FILLS = {_WEEKEND_FILL}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _access_token() -> str:
    from google.auth.transport.requests import Request

    from google_credentials import load_google_service_account_credentials

    creds = load_google_service_account_credentials(
        ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    creds.refresh(Request())
    return creds.token


def _sheet_id() -> str:
    return _env("SCHOOL_CALENDAR_SHEET_ID", DEFAULT_SHEET_ID)


def _worksheet_gid() -> int:
    raw = _env("SCHOOL_CALENDAR_WORKSHEET_GID", str(DEFAULT_WORKSHEET_GID))
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"SCHOOL_CALENDAR_WORKSHEET_GID must be an integer, got {raw!r}") from exc


def _sheet_range() -> str:
    return _env("SCHOOL_CALENDAR_RANGE", DEFAULT_RANGE)


def _rgb_tuple(color: Optional[Dict[str, float]]) -> Optional[Tuple[int, int, int]]:
    if not color:
        return None
    return (
        round((color.get("red") or 0) * 255),
        round((color.get("green") or 0) * 255),
        round((color.get("blue") or 0) * 255),
    )


def _worksheet_title(sheet_id: str, gid: int, token: str) -> str:
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
    params = {"fields": "sheets(properties(sheetId,title))"}
    resp = httpx.get(url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    resp.raise_for_status()
    for sheet in resp.json().get("sheets", []):
        props = sheet.get("properties") or {}
        if props.get("sheetId") == gid:
            title = str(props.get("title") or "").strip()
            if title:
                return title
    ws_name = _env("SCHOOL_CALENDAR_WORKSHEET")
    if ws_name:
        return ws_name
    raise RuntimeError(
        f"Worksheet gid {gid} not found in spreadsheet {sheet_id}. Set SCHOOL_CALENDAR_WORKSHEET."
    )


def _fetch_grid_rows() -> List[List[Dict[str, Any]]]:
    token = _access_token()
    sheet_id = _sheet_id()
    title = _worksheet_title(sheet_id, _worksheet_gid(), token)
    cell_range = _sheet_range()
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
    params = {
        "ranges": [f"'{title}'!{cell_range}"],
        "includeGridData": "true",
        "fields": "sheets(data(rowData(values(formattedValue,effectiveFormat(backgroundColor)))))",
    }
    resp = httpx.get(url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=120)
    resp.raise_for_status()
    row_data = resp.json()["sheets"][0]["data"][0].get("rowData") or []

    rows: List[List[Dict[str, Any]]] = []
    for row in row_data:
        cells: List[Dict[str, Any]] = []
        for cell in row.get("values") or []:
            fmt = cell.get("effectiveFormat") or {}
            cells.append(
                {
                    "v": cell.get("formattedValue") or "",
                    "bg": _rgb_tuple(fmt.get("backgroundColor")),
                }
            )
        rows.append(cells)

    col_count = max((len(r) for r in rows), default=0)
    for row in rows:
        while len(row) < col_count:
            row.append({"v": "", "bg": None})
    return rows


def _legend_labels(rows: List[List[Dict[str, Any]]]) -> Dict[Tuple[int, int, int], str]:
    """Map day-cell background colours to legend text (rows 31–37, cols T–AD)."""
    legend: Dict[Tuple[int, int, int], str] = {}
    for ri in range(30, min(37, len(rows))):
        left_bg = rows[ri][19]["bg"] if len(rows[ri]) > 19 else None
        left_label = (rows[ri][20]["v"] if len(rows[ri]) > 20 else "").strip()
        right_bg = rows[ri][28]["bg"] if len(rows[ri]) > 28 else None
        right_label = (rows[ri][29]["v"] if len(rows[ri]) > 29 else "").strip()
        for bg, label in ((left_bg, left_label), (right_bg, right_label)):
            if not bg or bg == (255, 255, 255) or not label:
                continue
            if bg == (230, 230, 230):
                continue
            if bg in legend and legend[bg] != label:
                legend[bg] = f"{legend[bg]} / {label}"
            else:
                legend[bg] = label
    return legend


def _calendar_day(value: str) -> Optional[int]:
    text = str(value or "").strip()
    if not text.isdigit():
        return None
    day = int(text)
    if 1 <= day <= 31:
        return day
    return None


def _include_instructional_days() -> bool:
    raw = _env("SCHOOL_CALENDAR_SHEET_INCLUDE_INSTRUCTIONAL", "0").lower()
    return raw in ("1", "true", "yes", "on")


def _events_from_rows(rows: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    legend = _legend_labels(rows)
    include_instructional = _include_instructional_days()
    if include_instructional:
        legend.setdefault((255, 255, 204), "Instructional day")
    events: List[Dict[str, Any]] = []

    for title_row, row in enumerate(rows):
        for start_col, cell in enumerate(row):
            match = _MONTH_TITLE_RE.match(str(cell.get("v") or "").strip())
            if not match:
                continue

            month = _MONTH_NUM[match.group(1)]
            year = int(match.group(2))
            header_row = title_row + 1
            if header_row >= len(rows):
                continue
            if str(rows[header_row][start_col].get("v") or "").strip().lower() != "days":
                continue

            mo_col = start_col + 1
            su_col = start_col + 7
            for data_row in range(title_row + 2, min(title_row + 8, len(rows))):
                if data_row >= len(rows):
                    break
                for col in range(mo_col, su_col + 1):
                    if col >= len(rows[data_row]):
                        continue
                    day_num = _calendar_day(rows[data_row][col].get("v", ""))
                    if day_num is None:
                        continue
                    bg = rows[data_row][col].get("bg")
                    if bg in _SKIP_DAY_FILLS:
                        continue

                    title = legend.get(bg) if bg else None
                    if not title:
                        if include_instructional and bg in ((255, 255, 255), (255, 255, 204)):
                            title = "Instructional day"
                        else:
                            continue

                    try:
                        start_date = date(year, month, day_num)
                    except ValueError:
                        continue

                    events.append(
                        {
                            "title": title,
                            "allDay": True,
                            "startDate": start_date.isoformat(),
                            "endDate": start_date.isoformat(),
                            "startTime": None,
                            "endTime": None,
                            "source": "sheet",
                        }
                    )

    events.sort(key=lambda e: (e.get("startDate") or "", e.get("title") or ""))
    return events


def fetch_sheet_calendar_events(
    range_start: date, range_end: date, *, force_refresh: bool = False
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Return all-day events from the published school calendar sheet within [range_start, range_end]."""
    now = time.time()
    cached = _CACHE.get("events")
    if not force_refresh and cached is not None and now - float(_CACHE.get("at") or 0) < _CACHE_TTL_SECONDS:
        all_events = cached
    else:
        try:
            rows = _fetch_grid_rows()
            all_events = _events_from_rows(rows)
            _CACHE["at"] = now
            _CACHE["events"] = all_events
        except Exception as exc:
            return [], str(exc)

    filtered = [
        ev
        for ev in all_events
        if range_start.isoformat() <= (ev.get("startDate") or "") <= range_end.isoformat()
    ]
    return filtered, None
