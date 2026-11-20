"""Generate timetabled rooms viewer from iSAMS TimetableManager batch data."""

from __future__ import annotations

import html
import json
import os
import sys
from typing import Any
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
STATIC_DIR = SCRIPT_DIR / "static" / "timetabled_rooms"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

if load_dotenv:
    for env_path in (SCRIPT_DIR / ".env", REPO_ROOT / ".env"):
        if env_path.is_file():
            load_dotenv(env_path)
            break

URL = (
    os.environ.get("ISAMS_BATCH_API_URL", "").strip()
    or os.environ.get("ISAMS_BATCH_URL", "").strip()
    or "https://halcyonschool.isams.cloud/api/batch/1.0/json.ashx"
)


def _batch_api_key() -> str:
    return (
        os.environ.get("ISAMS_BATCH_REFERENCE_API_KEY", "").strip()
        or os.environ.get("ISAMS_BATCH_LEGACY_API_KEY", "").strip()
    )

CSV_PATH = STATIC_DIR / "timetabled_rooms.csv"
HTML_PATH = STATIC_DIR / "timetabled_rooms.html"
HTML_ALL_DAYS_PATH = STATIC_DIR / "timetabled_rooms_all_days.html"

from isams_timetable_batch import (
    lesson_event_from_schedule,
    staff_meeting_event,
    build_pupil_counts_by_set_id,
    build_set_lookups,
    build_staff_lookups,
)
from school_calendar import fetch_school_terms, non_school_day_message
from timetabled_rooms_config import EXCLUDED_ROOMS, EXTRA_GRID_ROOMS, RESTRICTED_ROOMS

ACADEMIC_YEAR_START_MONTH = 8
ACADEMIC_YEAR_START_DAY = 25


def as_list(node):
    if node is None:
        return []
    if isinstance(node, list):
        return node
    return [node]


def academic_year_start_year_on(day: date) -> int:
    boundary = date(day.year, ACADEMIC_YEAR_START_MONTH, ACADEMIC_YEAR_START_DAY)
    if day >= boundary:
        return day.year
    return day.year - 1


def select_published_timetable(timetables: list[dict]) -> dict:
    if not timetables:
        raise RuntimeError("No published timetables returned from iSAMS")
    if len(timetables) == 1:
        return timetables[0]
    current_start = academic_year_start_year_on(date.today())
    for item in timetables:
        try:
            if int(str(item.get("StartYear") or "")) == current_start:
                return item
        except ValueError:
            continue
    return max(timetables, key=lambda item: int(str(item.get("StartYear") or 0)))



def sync_timetabled_rooms() -> dict:
    api_key = _batch_api_key()
    if not api_key:
        raise RuntimeError(
            "ISAMS_BATCH_REFERENCE_API_KEY is not set on the server. "
            "Add it in DigitalOcean → App → signin-server → Environment."
        )

    resp = requests.post(URL, params={"apiKey": api_key}, timeout=180)
    resp.raise_for_status()

    root = resp.json()["iSAMS"]
    timetable_mgr = root["TimetableManager"]
    timetable = select_published_timetable(as_list(timetable_mgr["PublishedTimetables"]["Timetable"]))

    classrooms = root["EstateManager"]["Buildings"]["Building"]["Classrooms"]["Classroom"]
    if isinstance(classrooms, dict):
        classrooms = [classrooms]

    room_lookup = {
        room["@Id"]: {
            "Room": room["Name"].strip(),
            "Description": room.get("Description") or "",
        }
        for room in classrooms
    }

    period_lookup = {}
    period_rows = []
    days = timetable_mgr["Structure"]["Week"]["Days"]["Day"]
    if isinstance(days, dict):
        days = [days]

    monday = next(day for day in days if day["Name"] == "Monday")
    monday_periods = monday["Periods"]["Period"]
    if isinstance(monday_periods, dict):
        monday_periods = [monday_periods]
    for period in sorted(monday_periods, key=lambda item: int(item["Ordinal"])):
        start = period.get("StartTime", "")
        end = period.get("EndTime", "")
        period_rows.append({
            "order": int(period["Ordinal"]),
            "name": period["Name"],
            "start": start,
            "end": end,
            "time": f"{start}–{end}",
        })

    for day in days:
        periods = day["Periods"]["Period"]
        if isinstance(periods, dict):
            periods = [periods]
        for period in periods:
            period_lookup[period["@Id"]] = {
                "Day order": int(day["Ordinal"]),
                "Period order": int(period["Ordinal"]),
            }

    set_by_id, code_to_id = build_set_lookups(root)
    staff_by_id, staff_by_usercode = build_staff_lookups(root)
    pupil_counts = build_pupil_counts_by_set_id(root)
    school_terms = fetch_school_terms()
    school_calendar_note = (
        " · Weekdays filtered by iSAMS term dates"
        if school_terms
        else ""
    )

    weekly_slots = defaultdict(lambda: defaultdict(list))

    def add_weekly_slot(room_id, period_id, event: dict):
        period = period_lookup.get(period_id)
        room = room_lookup.get(room_id)
        if not period or not room:
            return
        key = (period["Day order"], period["Period order"])
        weekly_slots[key][room["Room"]].append(event)

    schedules = timetable["Schedules"]["Schedule"]
    if isinstance(schedules, dict):
        schedules = [schedules]
    for entry in schedules:
        event = lesson_event_from_schedule(
            entry,
            set_by_id=set_by_id,
            code_to_id=code_to_id,
            pupil_counts=pupil_counts,
            staff_by_id=staff_by_id,
            staff_by_usercode=staff_by_usercode,
        )
        add_weekly_slot(entry.get("RoomId"), entry.get("PeriodId"), event)

    staff_meetings = timetable.get("StaffMeetings", {}).get("StaffMeeting")
    if staff_meetings:
        if isinstance(staff_meetings, dict):
            staff_meetings = [staff_meetings]
        for meeting in staff_meetings:
            event = staff_meeting_event(
                meeting,
                staff_by_id=staff_by_id,
                staff_by_usercode=staff_by_usercode,
            )
            add_weekly_slot(meeting.get("RoomId"), meeting.get("PeriodId"), event)

    rooms = sorted(
        {room for slots in weekly_slots.values() for room in slots} | set(EXTRA_GRID_ROOMS),
        key=str.casefold,
    )
    all_rooms = sorted({room["Room"] for room in room_lookup.values()}, key=str.casefold)
    unused_rooms = [room for room in all_rooms if room not in set(rooms)]

    today = date.today()
    range_start = today.replace(day=1)
    next_month = range_start.month + 1
    next_year = range_start.year
    if next_month == 13:
        next_month = 1
        next_year += 1
    range_end = date(next_year, next_month, monthrange(next_year, next_month)[1])

    csv_rows = []
    dates_to_show = []
    closed_dates: list[tuple[date, str]] = []
    current = range_start
    while current <= range_end:
        if current.weekday() < 5:
            closed_msg = non_school_day_message(current, school_terms)
            if closed_msg:
                closed_dates.append((current, closed_msg))
            else:
                day_order = current.weekday() + 1
                dates_to_show.append((current, day_order))
                for period in period_rows:
                    for room in rooms:
                        for event in weekly_slots[(day_order, period["order"])][room]:
                            student_count = event.get("studentCount")
                            csv_rows.append({
                                "Date": current.strftime("%Y-%m-%d"),
                                "Day": current.strftime("%A"),
                                "Period": period["name"],
                                "Time": period["time"],
                                "Room": room,
                                "Teacher": event.get("teacher") or "",
                                "Students": student_count if student_count is not None else "",
                                "Subject": event["subject"],
                                "Type": "Staff meeting" if event["type"] == "staff" else "Lesson",
                            })
        current += timedelta(days=1)

    pd.DataFrame(csv_rows).to_csv(CSV_PATH, index=False)

    timetable_label = f"{timetable['Name']} ({timetable['StartYear']}-{timetable['EndYear']})"
    generated_at = datetime.now().strftime("%d %b %Y, %H:%M")
    range_label = f"{range_start.strftime('%B %Y')} – {range_end.strftime('%B %Y')}"

    from timetabled_rooms_occupancy import write_weekly_occupancy_json

    occupancy_path = STATIC_DIR / "timetabled_rooms_weekly.json"
    try:
        occupancy_path = write_weekly_occupancy_json(
            weekly_slots=weekly_slots,
            period_rows=period_rows,
            timetable_label=timetable_label,
            generated_at=generated_at,
        )
    except Exception as exc:
        print(f"WARN: could not write weekly occupancy JSON: {exc}", file=sys.stderr)

    ROW_BASE_HEIGHT = 92
    CELL_PADDING = 14
    CELL_BLOCK_GAP = 5
    MULTI_BLOCK_MIN = 64  # matches .cell-content.multi .block { min-height }

    def event_block_height(event: dict) -> int:
        """Pixel height for one stacked lesson/meeting block (matches .block min-heights in CSS)."""
        lines = 1
        if (event.get("teacher") or "").strip():
            lines += 1
        if event.get("studentCount") is not None and event.get("type") == "lesson":
            lines += 1
        return {1: 44, 2: 54, 3: 64}.get(lines, 64)

    def cell_stack_height(events: list) -> int:
        if not events:
            return ROW_BASE_HEIGHT
        if len(events) == 1:
            return max(ROW_BASE_HEIGHT, event_block_height(events[0]))
        blocks = sum(event_block_height(event) for event in events)
        gaps = (len(events) - 1) * CELL_BLOCK_GAP
        # Floor: every stacked block keeps MULTI_BLOCK_MIN (2, 3, 4+ classes all scale).
        floor = len(events) * MULTI_BLOCK_MIN + gaps + CELL_PADDING
        return max(blocks + gaps + CELL_PADDING, floor)

    def row_height_for_period(day_order, period_order):
        heights = [
            cell_stack_height(weekly_slots[(day_order, period_order)][room])
            for room in rooms
        ]
        return max(heights, default=ROW_BASE_HEIGHT)


    def render_block(event):
        css_class = "block staff" if event["type"] == "staff" else "block lesson"
        parts: list[str] = []
        class_name = (event.get("className") or event.get("code") or event.get("subject") or "").strip()
        if class_name:
            parts.append(f'<span class="block-class">{html.escape(class_name)}</span>')
        teacher = (event.get("teacher") or "").strip()
        if teacher:
            parts.append(f'<span class="block-teacher">{html.escape(teacher)}</span>')
        count = event.get("studentCount")
        if count is not None and event["type"] == "lesson":
            label = "1 student" if count == 1 else f"{count} students"
            parts.append(f'<span class="block-students">{html.escape(label)}</span>')
        if not parts:
            parts.append(f'<span class="block-class">{html.escape("Lesson")}</span>')
        return f'<div class="{css_class}">{"".join(parts)}</div>'


    def render_cell(events, row_height, room_name):
        room_attr = html.escape(room_name, quote=True)
        if not events:
            return (
                f'<td class="empty" data-room="{room_attr}" style="height:{row_height}px">'
                f'<div class="empty-slot"></div></td>'
            )
        if len(events) == 1:
            count_class = "single"
        elif len(events) >= 4:
            count_class = "multi multi-many"
        else:
            count_class = "multi"
        stack_h = cell_stack_height(events) if events else row_height
        cell_h = max(row_height, stack_h)
        blocks = "".join(render_block(event) for event in events)
        return (
            f'<td class="filled" data-room="{room_attr}" data-lesson-count="{len(events)}"'
            f' style="height:{cell_h}px;min-height:{cell_h}px">'
            f'<div class="cell-content {count_class}">{blocks}</div></td>'
        )


    def render_closed_day_grid(day_date: date, message: str) -> str:
        date_label = day_date.strftime("%a %d %b %Y")
        return (
            f'<section class="day-grid day-grid-closed" id="{day_date.isoformat()}">'
            f'<p class="closed-day-banner">{html.escape(date_label)} — {html.escape(message)}</p>'
            f'<p class="closed-day-hint">No lesson timetable shown (school closed).</p>'
            f"</section>"
        )

    def render_day_grid(day_date, day_order):
        date_label = day_date.strftime("%a %d %b %Y")
        corner = (
            f'<th class="corner"><span class="corner-date">{html.escape(date_label)}</span></th>'
        )
        room_headers = "".join(
            f'<th class="room-head" data-room="{html.escape(room, quote=True)}">'
            f"<span>{html.escape(room)}</span></th>"
            for room in rooms
        )
        body_rows = []
        for period in period_rows:
            row_height = row_height_for_period(day_order, period["order"])
            cells = []
            for room in rooms:
                events = weekly_slots[(day_order, period["order"])][room]
                cells.append(render_cell(events, row_height, room))
            body_rows.append(
                f'<tr style="height:{row_height}px" data-period-order="{period["order"]}"'
                f' data-period-start="{html.escape(period["start"])}" data-period-end="{html.escape(period["end"])}">'
                f'<th class="period-head" style="height:{row_height}px">'
                f'<span class="period-name">{html.escape(period["name"])}</span>'
                f'<span class="period-time">{html.escape(period["time"])}</span>'
                f"</th>"
                + "".join(cells)
                + "</tr>"
            )

        return (
            f'<section class="day-grid" id="{day_date.isoformat()}">'
            f'<div class="grid-scroll">'
            f'<table class="timetable-grid">'
            f'<thead><tr>{corner}{room_headers}</tr></thead>'
            f'<tbody>{"".join(body_rows)}</tbody>'
            f'</table></div></section>'
        )


    day_grids = "".join(
        render_day_grid(day_date, day_order)
        for day_date, day_order in dates_to_show
    )
    closed_day_grids = "".join(
        render_closed_day_grid(day_date, message) for day_date, message in closed_dates
    )

    calendar_days: list[tuple[date, bool, Any]] = [
        (day_date, False, day_order) for day_date, day_order in dates_to_show
    ]
    calendar_days.extend((day_date, True, message) for day_date, message in closed_dates)
    calendar_days.sort(key=lambda item: item[0])

    date_options = "".join(
        (
            f'<option value="{day_date.isoformat()}" data-closed="1">'
            f'{html.escape(day_date.strftime("%a %d %b %Y"))} (closed)</option>'
            if is_closed
            else f'<option value="{day_date.isoformat()}">{html.escape(day_date.strftime("%a %d %b %Y"))}</option>'
        )
        for day_date, is_closed, _ in calendar_days
    )

    period_options = "".join(
        f'<option value="{period["order"]}">{html.escape(period["name"])} ({html.escape(period["time"])})</option>'
        for period in period_rows
    )

    months: dict[str, list[tuple[date, bool, Any]]] = defaultdict(list)
    for day_date, day_order in dates_to_show:
        months[day_date.strftime("%Y-%m")].append((day_date, False, day_order))
    for day_date, message in closed_dates:
        months[day_date.strftime("%Y-%m")].append((day_date, True, message))

    month_sections = []
    for month_key in sorted(months):
        month_title = datetime.strptime(month_key, "%Y-%m").strftime("%B %Y")
        day_sections_parts = []
        for day_date, is_closed, payload in sorted(months[month_key], key=lambda row: row[0]):
            if is_closed:
                day_sections_parts.append(render_closed_day_grid(day_date, str(payload)))
            else:
                day_sections_parts.append(render_day_grid(day_date, int(payload)))
        day_sections = "".join(day_sections_parts)
        month_sections.append(
            f'<section class="month"><h2>{html.escape(month_title)}</h2>{day_sections}</section>'
        )
    all_days_content = "".join(month_sections)

    room_usage = {}
    for day_order in range(1, 6):
        room_usage[str(day_order)] = {}
        for period in period_rows:
            used_rooms = sorted(
                weekly_slots[(day_order, period["order"])].keys(),
                key=str.casefold,
            )
            room_usage[str(day_order)][str(period["order"])] = used_rooms

    from school_calendar_store import calendar_payload, write_school_calendar_file

    school_calendar_obj = calendar_payload(school_terms, generated_at=generated_at)
    write_school_calendar_file(school_terms, generated_at=generated_at)

    timetable_data_json = json.dumps({
        "periods": [
            {
                "order": period["order"],
                "name": period["name"],
                "start": period["start"],
                "end": period["end"],
            }
            for period in period_rows
        ],
        "roomUsage": room_usage,
        "allRooms": all_rooms,
        "gridRooms": rooms,
        "excludedRooms": EXCLUDED_ROOMS,
        "restrictedRooms": RESTRICTED_ROOMS,
        "schoolCalendar": school_calendar_obj,
    })

    unused_count = len(unused_rooms)

    def build_legend_html() -> str:
        return """
              <div class="legend legend-stacked">
                <div class="legend-item"><span class="legend-swatch lesson"></span> Lesson</div>
                <div class="legend-item"><span class="legend-swatch staff"></span> Staff meeting</div>
                <div class="legend-item"><span class="legend-swatch available"></span> Available now</div>
                <div class="legend-item"><span class="legend-swatch booking"></span> Ad-hoc booking</div>
                <div class="legend-item"><span class="legend-swatch closed"></span> Holiday / out of term</div>
              </div>
        """


    def build_header_sidebar(alt_view_link: str = "") -> str:
        unused_block = ""
        if unused_rooms:
            items = "".join(f"<li>{html.escape(room)}</li>" for room in unused_rooms)
            unused_block = f"""
              <div class="sidebar-unused">
                <h2 class="sidebar-title">Not on timetable ({unused_count})</h2>
                <ul class="sidebar-list">{items}</ul>
              </div>
            """
        alt_link_block = ""
        if alt_view_link:
            alt_link_block = f"""
                  <div class="legend-item legend-link">{alt_view_link}</div>
            """
        return f"""
            <div class="header-sidebar-cluster">
              <button type="button" class="nav-btn nav-btn-primary" id="refresh-isams-btn">Refresh from iSAMS</button>
              <aside class="header-sidebar">
                <div class="sidebar-inner">
                  <div class="sidebar-legend">
                    {build_legend_html()}
                    {alt_link_block}
                  </div>
                  {unused_block}
                </div>
              </aside>
            </div>
        """

    def build_period_panels(include_selected_day: bool = False) -> str:
        selected_day_panel = ""
        if include_selected_day:
            selected_day_panel = f"""
        <section class="selected-day-panel" id="selected-day-panel" hidden>
          <div class="selected-period-row">
            <div class="period-select-wrap">
              <label for="period-picker">Period</label>
              <select id="period-picker" aria-label="Select period">{period_options}</select>
            </div>
            <div class="selected-period-rooms-wrap">
              <h3 class="panel-subtitle">Available rooms</h3>
              <ul id="selected-period-rooms" class="available-rooms"><li class="muted">—</li></ul>
            </div>
          </div>
        </section>
            """
        return f"""
        {selected_day_panel}
        <section class="period-panels" id="period-panels">
          <div class="period-panel">
            <h2 class="panel-title">Current period</h2>
            <p id="current-period-detail" class="panel-detail">Checking…</p>
            <h3 class="panel-subtitle">Available rooms</h3>
            <ul id="available-rooms-list" class="available-rooms"><li class="muted">—</li></ul>
          </div>
          <div class="period-panel">
            <h2 class="panel-title">Next period</h2>
            <p id="next-period-detail" class="panel-detail">Checking…</p>
            <h3 class="panel-subtitle">Available rooms</h3>
            <ul id="next-available-rooms-list" class="available-rooms"><li class="muted">—</li></ul>
          </div>
        </section>
        """

    base_styles = """
        :root {
          color-scheme: light;
          --bg: #eef2f7;
          --card: #ffffff;
          --text: #1f2937;
          --muted: #6b7280;
          --border: #d1d5db;
          --lesson: #dbeafe;
          --lesson-border: #93c5fd;
          --staff: #ede9fe;
          --staff-border: #c4b5fd;
          --empty: #f9fafb;
          --tr-col-width: 132px;
        }
        * { box-sizing: border-box; }
        .page-timetabled-rooms .tr-content {
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          color: var(--text);
          line-height: 1.35;
          padding: 0.5rem 0.65rem 1rem;
        }
        .page-timetabled-rooms .tr-content header,
        .page-timetabled-rooms .tr-content .schedule-panel,
        .page-timetabled-rooms .tr-content .month,
        .page-timetabled-rooms .tr-content .period-panels,
        .page-timetabled-rooms .tr-content .period-panel,
        .page-timetabled-rooms .tr-content .selected-day-panel {
          background: var(--card);
          border: 1px solid var(--border);
          border-radius: 12px;
          padding: 20px;
          margin-bottom: 24px;
        }
        .period-panels[hidden],
        .selected-day-panel[hidden] {
          display: none !important;
        }
        h1 { margin: 0 0 8px; font-size: 1.7rem; }
        h2 { margin: 0 0 16px; font-size: 1.25rem; }
        .header-layout {
          display: flex;
          gap: 24px;
          align-items: flex-start;
          justify-content: space-between;
          flex-wrap: wrap;
        }
        .header-main { flex: 1 1 420px; min-width: 0; }
        .meta-group { margin-top: 4px; }
        .meta {
          color: var(--muted);
          margin: 0;
          font-size: 0.9rem;
          line-height: 1.5;
        }
        .legend-link {
          margin-top: auto;
        }
        .legend-link a { color: #1d4ed8; text-decoration: none; }
        .legend-link a:hover { text-decoration: underline; }
        .header-sidebar {
          flex: 0 0 360px;
          max-width: 480px;
          min-width: 280px;
          padding: 14px 16px;
          background: #f9fafb;
          border: 1px solid var(--border);
          border-radius: 10px;
        }
        .sidebar-inner {
          display: flex;
          gap: 20px;
          align-items: stretch;
        }
        .sidebar-legend {
          display: flex;
          flex-direction: column;
          flex: 0 0 auto;
        }
        .sidebar-unused {
          flex: 1 1 auto;
          min-width: 0;
        }
        .sidebar-title {
          margin: 0 0 8px;
          font-size: 0.95rem;
          font-weight: 700;
        }
        .sidebar-list {
          margin: 0;
          padding: 0;
          list-style: none;
          display: grid;
          grid-template-columns: 1fr;
          gap: 4px 0;
          font-size: 0.9rem;
          line-height: 1.5;
          color: var(--muted);
        }
        .period-panels {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 24px;
          padding: 0;
          background: transparent;
          border: none;
        }
        .period-panel {
          margin-bottom: 0;
          padding: 16px 18px;
        }
        .selected-day-panel {
          padding: 16px 18px;
        }
        .selected-period-row {
          display: flex;
          align-items: flex-start;
          gap: 24px;
          flex-wrap: wrap;
        }
        .period-select-wrap {
          flex: 0 0 220px;
          min-width: 200px;
        }
        .period-select-wrap label {
          display: block;
          margin: 0 0 8px;
          font-size: 0.95rem;
          font-weight: 700;
        }
        #period-picker {
          width: 100%;
          padding: 8px 10px;
          border: 1px solid var(--border);
          border-radius: 8px;
          font: inherit;
          font-size: 0.9rem;
          background: #fff;
          color: var(--text);
        }
        .selected-period-rooms-wrap {
          flex: 1 1 360px;
          min-width: 0;
        }
        .selected-period-rooms-wrap .panel-subtitle {
          margin-top: 0;
        }
        .panel-title {
          margin: 0 0 6px;
          font-size: 0.95rem;
          font-weight: 700;
        }
        .panel-detail {
          margin: 0 0 14px;
          font-size: 0.9rem;
          line-height: 1.5;
          color: #111827;
        }
        .panel-subtitle {
          margin: 0 0 8px;
          font-size: 0.95rem;
          font-weight: 700;
        }
        .available-rooms {
          margin: 0;
          padding: 0;
          list-style: none;
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 4px 12px;
          font-size: 0.9rem;
          line-height: 1.5;
        }
        .available-rooms .muted {
          color: var(--muted);
          grid-column: 1 / -1;
        }
        .available-rooms .room-available {
          color: #111827;
          font-size: 0.9rem;
          line-height: 1.5;
          font-weight: 400;
        }
        .legend {
          display: flex;
          gap: 16px;
          margin-top: 0;
          flex-wrap: wrap;
        }
        .legend-stacked {
          flex-direction: column;
          align-items: flex-start;
          gap: 8px;
        }
        .legend-item {
          display: flex;
          align-items: center;
          gap: 8px;
          font-size: 0.9rem;
        }
        .legend-swatch {
          width: 18px;
          height: 18px;
          border-radius: 4px;
          border: 1px solid var(--border);
        }
        .legend-swatch.lesson { background: var(--lesson); border-color: var(--lesson-border); }
        .legend-swatch.staff { background: var(--staff); border-color: var(--staff-border); }
        .legend-swatch.available { background: #dcfce7; border-color: #86efac; }
        .legend-swatch.closed { background: #e5e7eb; border-color: #9ca3af; }
        .legend-swatch.booking { background: #fef9c3; border-color: #fde047; }
        tr.current-period-row .period-head {
          background: #fef9c3;
        }
        td.available-now {
          background: var(--empty) !important;
        }
        .empty-slot {
          height: 100%;
          min-height: 100%;
          box-sizing: border-box;
        }
        td.available-now .empty-slot:not(.has-booking-content) {
          position: absolute;
          inset: 5%;
          display: flex;
          align-items: center;
          justify-content: center;
          box-sizing: border-box;
          width: auto;
          height: auto;
          min-height: 0;
          border-radius: 6px;
          border: 1px solid #86efac;
          background: #dcfce7;
          color: #166534;
          font-weight: 600;
          font-size: 0.68rem;
          text-transform: uppercase;
          letter-spacing: 0.04em;
        }
        td.available-now .empty-slot:not(.has-booking-content)::before {
          content: "Available";
        }
        .room-head.available-now-header {
          background: #ecfdf5;
          box-shadow: inset 0 -3px 0 #22c55e;
        }
        .grid-scroll {
          overflow: auto;
          border: 1px solid var(--border);
          border-radius: 8px;
          max-height: 70vh;
        }
        .timetable-grid {
          border-collapse: separate;
          border-spacing: 0;
          table-layout: fixed;
          width: max-content;
          min-width: 100%;
          font-size: 0.72rem;
        }
        .timetable-grid th,
        .timetable-grid td {
          border-right: 1px solid var(--border);
          border-bottom: 1px solid var(--border);
          vertical-align: middle;
          padding: 0;
        }
        .corner {
          position: sticky;
          top: 0;
          left: 0;
          z-index: 4;
          background: #f3f4f6;
          width: var(--tr-col-width);
          min-width: var(--tr-col-width);
          max-width: var(--tr-col-width);
          text-align: center;
          padding: 10px 8px;
          box-sizing: border-box;
        }
        .corner-date {
          display: block;
          font-weight: 700;
          font-size: 0.78rem;
          line-height: 1.3;
          color: #111827;
        }
        .room-head {
          position: sticky;
          top: 0;
          z-index: 3;
          background: #f3f4f6;
          width: var(--tr-col-width);
          min-width: var(--tr-col-width);
          max-width: var(--tr-col-width);
          min-height: 52px;
          padding: 8px 6px;
          text-align: center;
          box-sizing: border-box;
        }
        .room-head span {
          display: block;
          font-weight: 600;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          line-height: 1.25;
          font-size: 0.72rem;
        }
        .period-head {
          position: sticky;
          left: 0;
          z-index: 2;
          background: #f9fafb;
          width: var(--tr-col-width);
          min-width: var(--tr-col-width);
          max-width: var(--tr-col-width);
          padding: 8px 10px;
          font-weight: 600;
          vertical-align: middle;
          box-sizing: border-box;
        }
        .period-name {
          display: block;
          line-height: 1.25;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .period-time {
          display: block;
          color: var(--muted);
          font-weight: 400;
          font-size: 0.68rem;
          margin-top: 2px;
          line-height: 1.25;
          white-space: nowrap;
        }
        .timetable-grid td {
          width: var(--tr-col-width);
          min-width: var(--tr-col-width);
          max-width: var(--tr-col-width);
          min-height: 92px;
          background: var(--empty);
          padding: 0;
          vertical-align: stretch;
          box-sizing: border-box;
        }
        .timetable-grid td.empty {
          position: relative;
        }
        .cell-content {
          display: flex;
          flex-direction: column;
          height: 100%;
          min-height: 100%;
          padding: 5%;
          box-sizing: border-box;
          gap: 4px;
          justify-content: stretch;
        }
        .cell-content.multi {
          gap: 5px;
          justify-content: flex-start;
        }
        .block {
          flex: 1 1 auto;
          width: 100%;
          min-height: 62px;
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          line-height: 1.22;
          margin: 0;
          border-radius: 6px;
          padding: 6px 5px;
          border: 1px solid transparent;
          text-align: center;
          box-sizing: border-box;
          gap: 3px;
          overflow: hidden;
        }
        .cell-content.multi .block {
          flex: 0 0 auto;
          min-height: 64px;
        }
        .cell-content.multi-many {
          justify-content: flex-start;
        }
        .cell-content.single .block {
          min-height: 62px;
        }
        .block-class,
        .block-teacher,
        .block-students {
          display: block;
          width: 100%;
          max-width: 100%;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .block-class {
          font-weight: 700;
          font-size: 0.82rem;
          line-height: 1.15;
        }
        .block-teacher {
          font-weight: 400;
          font-size: 0.58rem;
          line-height: 1.15;
        }
        .block-students {
          font-weight: 400;
          font-size: 0.58rem;
          line-height: 1.15;
          opacity: 0.88;
        }
        .day-grid-closed {
          padding: 16px 20px;
          margin-bottom: 16px;
          background: #f3f4f6;
          border: 1px dashed var(--border);
          border-radius: 10px;
        }
        .closed-day-banner {
          margin: 0 0 6px;
          font-weight: 700;
          color: #374151;
        }
        .closed-day-hint {
          margin: 0;
          color: var(--muted);
          font-size: 0.9rem;
        }
        .block.lesson {
          background: var(--lesson);
          border-color: var(--lesson-border);
          color: #1e3a8a;
        }
        .block.staff {
          background: var(--staff);
          border-color: var(--staff-border);
          color: #5b21b6;
        }
        .unused ul { margin: 0; padding-left: 1.2rem; }
        @media (max-width: 900px) {
          .period-panels { grid-template-columns: 1fr; }
        }
    """

    date_picker_styles = """
        .date-nav {
          display: flex;
          flex-direction: column;
          align-items: flex-start;
          gap: 8px;
          margin-top: 20px;
        }
        .date-nav label {
          font-weight: 600;
          font-size: 0.95rem;
        }
        .date-nav-controls {
          display: flex;
          align-items: center;
          gap: 10px;
          flex-wrap: wrap;
        }
        #date-picker {
          min-width: 220px;
          padding: 8px 10px;
          border: 1px solid var(--border);
          border-radius: 8px;
          font: inherit;
          background: #fff;
          color: var(--text);
        }
        .nav-btn {
          padding: 8px 12px;
          border: 1px solid var(--border);
          border-radius: 8px;
          background: #f9fafb;
          color: var(--text);
          font: inherit;
          cursor: pointer;
        }
        .nav-btn:hover:not(:disabled) {
          background: #eff6ff;
          border-color: #93c5fd;
        }
        .nav-btn:disabled {
          opacity: 0.45;
          cursor: not-allowed;
        }
        .nav-btn-primary {
          background: #2563eb;
          color: #fff;
          border-color: #2563eb;
          font-weight: 600;
        }
        .nav-btn-primary:hover:not(:disabled) {
          background: #1d4ed8;
          border-color: #1d4ed8;
        }
        .header-sidebar-cluster {
          display: flex;
          flex-direction: row;
          align-items: flex-start;
          gap: 16px;
          flex: 0 0 auto;
        }
        .header-sidebar-cluster .nav-btn {
          flex-shrink: 0;
        }
        .schedule-panel { padding: 16px; }
        .day-grid { display: none; }
        .day-grid.active { display: block; }
    """

    all_days_styles = """
        .day-grid { margin-bottom: 28px; }
        .day-grid:last-child { margin-bottom: 0; }
    """


    def build_header(extra_controls: str = "", alt_view_link: str = "") -> str:
        return f"""
        <header>
          <div class="header-layout">
            <div class="header-main">
              <h1>Timetabled Rooms</h1>
              <div class="meta-group">
                <p class="meta">Generated {generated_at} · {html.escape(timetable_label)}{html.escape(school_calendar_note)}</p>
              </div>
              {extra_controls}
            </div>
            {build_header_sidebar(alt_view_link)}
          </div>
        </header>
        """


    live_status_script = f"""
      <script>
        const TIMETABLE_DATA = {timetable_data_json};

        function formatDate(dateValue) {{
          return [
            dateValue.getFullYear(),
            String(dateValue.getMonth() + 1).padStart(2, "0"),
            String(dateValue.getDate()).padStart(2, "0"),
          ].join("-");
        }}

        function parseTime(value) {{
          const parts = value.split(":");
          return Number(parts[0]) * 60 + Number(parts[1]);
        }}

        function getDayOrder(dateValue) {{
          const weekday = dateValue.getDay();
          return weekday >= 1 && weekday <= 5 ? weekday : null;
        }}

        function getCurrentPeriod(dateValue) {{
          const minutes = dateValue.getHours() * 60 + dateValue.getMinutes();
          for (const period of TIMETABLE_DATA.periods) {{
            const start = parseTime(period.start);
            const end = parseTime(period.end);
            if (minutes >= start && minutes < end) {{
              return period;
            }}
          }}
          return null;
        }}

        function isExcludedRoom(room) {{
          return TIMETABLE_DATA.excludedRooms.includes(room);
        }}

        function clearHighlights() {{
          document.querySelectorAll(".current-period-row").forEach((row) => {{
            row.classList.remove("current-period-row");
          }});
          document.querySelectorAll(".available-now").forEach((cell) => {{
            cell.classList.remove("available-now");
          }});
          document.querySelectorAll(".available-now-header").forEach((cell) => {{
            cell.classList.remove("available-now-header");
          }});
        }}

        function getGridForToday(todayStr) {{
          const active = document.querySelector(".day-grid.active");
          if (active && active.id === todayStr) {{
            return active;
          }}
          return document.getElementById(todayStr);
        }}

        function getNextPeriod(dateValue) {{
          const minutes = dateValue.getHours() * 60 + dateValue.getMinutes();
          const current = getCurrentPeriod(dateValue);
          if (current) {{
            const index = TIMETABLE_DATA.periods.findIndex(
              (period) => period.order === current.order
            );
            if (index >= 0 && index < TIMETABLE_DATA.periods.length - 1) {{
              return TIMETABLE_DATA.periods[index + 1];
            }}
            return null;
          }}
          for (const period of TIMETABLE_DATA.periods) {{
            if (minutes < parseTime(period.start)) {{
              return period;
            }}
          }}
          return null;
        }}

        function renderAvailableList(rooms) {{
          return rooms.length
            ? rooms.map((room) => `<li class="room-available">${{room}}</li>`).join("")
            : '<li class="muted">None free</li>';
        }}

        function isRoomUnavailableForBooking(cell, grid, period) {{
          if (cell.classList.contains("room-blocked")) {{
            return true;
          }}
          const room = cell.dataset.room;
          if (
            room &&
            period &&
            typeof window.roomBookingsIsCellBlocked === "function" &&
            window.roomBookingsIsCellBlocked(grid.id, room, period.start, period.end)
          ) {{
            return true;
          }}
          return false;
        }}

        function getAvailableRoomsForPeriod(grid, periodOrder) {{
          if (!grid) {{
            return [];
          }}
          const row = grid.querySelector(`tr[data-period-order="${{periodOrder}}"]`);
          if (!row) {{
            return [];
          }}
          const period = TIMETABLE_DATA.periods.find((item) => item.order === periodOrder);
          const availableInGrid = [];
          row.querySelectorAll("td.empty").forEach((cell) => {{
            const room = cell.dataset.room;
            if (!room || isExcludedRoom(room)) {{
              return;
            }}
            if (isRoomUnavailableForBooking(cell, grid, period)) {{
              return;
            }}
            availableInGrid.push(room);
          }});
          return availableInGrid.sort((a, b) =>
            a.localeCompare(b, undefined, {{ sensitivity: "base" }})
          );
        }}

        function highlightAvailableRooms(grid, periodOrder) {{
          if (!grid) {{
            return [];
          }}
          const row = grid.querySelector(`tr[data-period-order="${{periodOrder}}"]`);
          if (!row) {{
            return [];
          }}
          row.classList.add("current-period-row");
          const availableInGrid = getAvailableRoomsForPeriod(grid, periodOrder);
          availableInGrid.forEach((room) => {{
            const cell = row.querySelector(`td[data-room="${{CSS.escape(room)}}"]`);
            if (cell) {{
              cell.classList.add("available-now");
            }}
            const header = grid.querySelector(
              `.room-head[data-room="${{CSS.escape(room)}}"]`
            );
            if (header) {{
              header.classList.add("available-now-header");
            }}
          }});
          return availableInGrid;
        }}

        function getTodayStr() {{
          return formatDate(new Date());
        }}

        function getActiveGrid() {{
          return document.querySelector(".day-grid.active");
        }}

        function setPanelMode(isToday) {{
          const livePanels = document.getElementById("period-panels");
          const selectedPanel = document.getElementById("selected-day-panel");
          if (livePanels) {{
            livePanels.hidden = !isToday;
          }}
          if (selectedPanel) {{
            selectedPanel.hidden = isToday;
          }}
        }}

        function updateSelectedDayPanel() {{
          const grid = getActiveGrid();
          const picker = document.getElementById("period-picker");
          const listEl = document.getElementById("selected-period-rooms");
          if (!picker || !listEl) {{
            return;
          }}
          clearHighlights();
          if (!grid) {{
            listEl.innerHTML = '<li class="muted">—</li>';
            return;
          }}
          const periodOrder = Number(picker.value);
          const available = highlightAvailableRooms(grid, periodOrder);
          listEl.innerHTML = renderAvailableList(available);
        }}

        function updateLiveStatus() {{
          const now = new Date();
          const todayStr = getTodayStr();
          const activeGrid = getActiveGrid();
          const selectedDate = activeGrid ? activeGrid.id : todayStr;
          const isToday = selectedDate === todayStr;
          const hasSelectedDayPanel = Boolean(document.getElementById("selected-day-panel"));

          if (hasSelectedDayPanel) {{
            setPanelMode(isToday);
          }}

          clearHighlights();

          if (hasSelectedDayPanel && !isToday) {{
            updateSelectedDayPanel();
            return;
          }}

          const currentDetailEl = document.getElementById("current-period-detail");
          const currentListEl = document.getElementById("available-rooms-list");
          const nextDetailEl = document.getElementById("next-period-detail");
          const nextListEl = document.getElementById("next-available-rooms-list");
          const grid = getGridForToday(todayStr);

          const dayOrder = getDayOrder(now);
          if (!dayOrder) {{
            currentDetailEl.textContent = "No school day today";
            currentListEl.innerHTML = '<li class="muted">—</li>';
            nextDetailEl.textContent = "—";
            nextListEl.innerHTML = '<li class="muted">—</li>';
            return;
          }}

          const period = getCurrentPeriod(now);
          const nextPeriod = getNextPeriod(now);

          if (!period) {{
            currentDetailEl.textContent = "Between periods";
            currentListEl.innerHTML = '<li class="muted">—</li>';
          }} else {{
            currentDetailEl.textContent = `${{period.name}} (${{period.start}}–${{period.end}})`;
            const available = highlightAvailableRooms(grid, period.order);
            currentListEl.innerHTML = renderAvailableList(available);
          }}

          if (!nextPeriod) {{
            nextDetailEl.textContent = "No further periods today";
            nextListEl.innerHTML = '<li class="muted">—</li>';
          }} else {{
            nextDetailEl.textContent = `${{nextPeriod.name}} (${{nextPeriod.start}}–${{nextPeriod.end}})`;
            const nextAvailable = getAvailableRoomsForPeriod(grid, nextPeriod.order);
            nextListEl.innerHTML = renderAvailableList(nextAvailable);
          }}
        }}

        window.updateLiveStatus = updateLiveStatus;
        const periodPicker = document.getElementById("period-picker");
        if (periodPicker) {{
          periodPicker.addEventListener("change", updateSelectedDayPanel);
        }}
        updateLiveStatus();
        setInterval(updateLiveStatus, 60000);

        const refreshBtn = document.getElementById("refresh-isams-btn");
        if (refreshBtn) {{
          refreshBtn.addEventListener("click", () => {{
            if (!confirm("Reload timetabled rooms from iSAMS? This may take a few minutes.")) {{
              return;
            }}
            refreshBtn.disabled = true;
            refreshBtn.textContent = "Refreshing…";
            fetch("/api/timetabled-rooms/resync", {{
              method: "POST",
              credentials: "same-origin",
              headers: {{ "Content-Type": "application/json" }},
              body: "{{}}",
            }})
              .then((response) => response.text().then((text) => {{
                let data = {{}};
                try {{
                  data = text ? JSON.parse(text) : {{}};
                }} catch (err) {{
                  throw new Error(response.ok ? "Invalid JSON" : text.slice(0, 200) || response.statusText);
                }}
                if (!response.ok) {{
                  throw new Error((data && data.error) || text.slice(0, 300) || response.statusText);
                }}
                return data;
              }}))
              .then((data) => {{
                const removed = data && data.prunedBookings && data.prunedBookings.removed;
                if (removed) {{
                  alert(
                    "iSAMS timetable reloaded. "
                    + removed
                    + " ad-hoc booking(s) were removed because they now clash with timetabled lessons."
                  );
                }}
                window.location.reload();
              }})
              .catch((err) => {{
                alert(err.message || "Refresh failed");
                refreshBtn.disabled = false;
                refreshBtn.textContent = "Refresh from iSAMS";
              }});
          }});
        }}
      </script>
    """

    date_nav_controls = f"""
          <div class="date-nav">
            <label for="date-picker">View date</label>
            <div class="date-nav-controls">
              <button type="button" class="nav-btn" id="prev-day" aria-label="Previous day">←</button>
              <select id="date-picker" aria-label="Select date">{date_options}</select>
              <button type="button" class="nav-btn" id="next-day" aria-label="Next day">→</button>
            </div>
          </div>
    """


    from halcyon_tools_section import wrap_tools_section_page

    def wrap_halcyon_page(title: str, inner_styles: str, body_inner: str, tail_scripts: str) -> str:
        return wrap_tools_section_page(
            title=title,
            section_page="timetabledrooms",
            page_class="page-timetabled-rooms",
            content_class="tr-content",
            inner_styles=inner_styles,
            body_inner=body_inner,
            tail_scripts=tail_scripts,
        )


    single_day_body = f"""
        {build_header(date_nav_controls, '<a href="/timetabled-rooms/all-days">All days at once</a>')}
        <section class="schedule-panel">
          {day_grids}
          {closed_day_grids}
        </section>
        {build_period_panels(include_selected_day=True)}
    """

    single_day_scripts = f"""
      {live_status_script}
      <script>
        (function () {{
          const picker = document.getElementById("date-picker");
          const prevBtn = document.getElementById("prev-day");
          const nextBtn = document.getElementById("next-day");
          const grids = Array.from(document.querySelectorAll(".day-grid"));
          const dates = grids.map((grid) => grid.id);

          function pickInitialDate() {{
            const today = new Date();
            const todayStr = [
              today.getFullYear(),
              String(today.getMonth() + 1).padStart(2, "0"),
              String(today.getDate()).padStart(2, "0"),
            ].join("-");

            if (dates.includes(todayStr)) {{
              return todayStr;
            }}

            const todayTime = new Date(todayStr + "T00:00:00").getTime();
            const nextDate = dates.find((value) => new Date(value + "T00:00:00").getTime() >= todayTime);
            return nextDate || dates[dates.length - 1];
          }}

          function showDate(dateValue) {{
            grids.forEach((grid) => {{
              grid.classList.toggle("active", grid.id === dateValue);
            }});
            picker.value = dateValue;

            const index = dates.indexOf(dateValue);
            prevBtn.disabled = index <= 0;
            nextBtn.disabled = index >= dates.length - 1;

            if (window.updateLiveStatus) {{
              window.updateLiveStatus();
            }}
          }}

          picker.addEventListener("change", () => showDate(picker.value));
          prevBtn.addEventListener("click", () => {{
            const index = dates.indexOf(picker.value);
            if (index > 0) showDate(dates[index - 1]);
          }});
          nextBtn.addEventListener("click", () => {{
            const index = dates.indexOf(picker.value);
            if (index < dates.length - 1) showDate(dates[index + 1]);
          }});

          showDate(pickInitialDate());
        }})();
      </script>
    """

    all_days_body = f"""
        {build_header(alt_view_link='<a href="/timetabled-rooms">Single day with date picker</a>')}
        {all_days_content}
        {build_period_panels()}
    """

    html_doc = wrap_halcyon_page(
        "Timetabled Rooms",
        base_styles + date_picker_styles,
        single_day_body,
        single_day_scripts,
    )

    html_all_days_doc = wrap_halcyon_page(
        "Timetabled Rooms – All Days",
        base_styles + all_days_styles,
        all_days_body,
        live_status_script,
    )

    HTML_PATH.write_text(html_doc, encoding="utf-8")
    HTML_ALL_DAYS_PATH.write_text(html_all_days_doc, encoding="utf-8")

    uploaded_html = upload_to_supabase_storage(HTML_PATH, "timetabled_rooms.html")
    uploaded_all_days = upload_to_supabase_storage(HTML_ALL_DAYS_PATH, "timetabled_rooms_all_days.html")
    uploaded_csv = False
    if CSV_PATH.is_file():
        uploaded_csv = upload_to_supabase_storage(CSV_PATH, "timetabled_rooms.csv", "text/csv")
    uploaded_weekly = False
    if occupancy_path.is_file():
        uploaded_weekly = upload_to_supabase_storage(
            occupancy_path, "timetabled_rooms_weekly.json", "application/json"
        )
    from school_calendar_store import CALENDAR_FILENAME, REMOTE_NAME as CALENDAR_REMOTE

    uploaded_calendar = False
    calendar_path = STATIC_DIR / CALENDAR_FILENAME
    if calendar_path.is_file():
        uploaded_calendar = upload_to_supabase_storage(
            calendar_path, CALENDAR_REMOTE, "application/json"
        )
        from school_calendar_store import invalidate_school_calendar_cache

        invalidate_school_calendar_cache()

    pruned_bookings = {"removed": 0, "items": []}
    try:
        from room_bookings import prune_after_timetable_sync

        pruned_bookings = prune_after_timetable_sync()
    except Exception as exc:
        print(f"WARN: could not prune conflicting bookings: {exc}", file=sys.stderr)

    return {
        "timetableLabel": timetable_label,
        "rangeLabel": range_label,
        "weekdays": len(dates_to_show),
        "closedWeekdays": len(closed_dates),
        "schoolTermsLoaded": bool(school_terms),
        "rooms": len(rooms),
        "slotEntries": len(csv_rows),
        "generatedAt": generated_at,
        "uploadedHtml": uploaded_html,
        "uploadedAllDays": uploaded_all_days,
        "uploadedCsv": uploaded_csv,
        "uploadedWeekly": uploaded_weekly,
        "uploadedSchoolCalendar": uploaded_calendar,
        "prunedBookings": pruned_bookings,
        "htmlPath": str(HTML_PATH),
        "htmlAllDaysPath": str(HTML_ALL_DAYS_PATH),
        "csvPath": str(CSV_PATH),
    }


def upload_to_supabase_storage(
    local_path: Path, remote_name: str, content_type: str = "text/html; charset=utf-8"
) -> bool:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    bucket = os.environ.get("SUPABASE_TIMETABLED_ROOMS_BUCKET", "timetabled-rooms").strip()
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


def main() -> int:
    if not _batch_api_key():
        print(
            "ERROR: set ISAMS_BATCH_REFERENCE_API_KEY in signInApp_server/.env",
            file=sys.stderr,
        )
        return 1
    result = sync_timetabled_rooms()
    print(f"\nPublished timetable: {result['timetableLabel']}")
    print(f"Showing: {result['rangeLabel']}")
    print(
        f"{result['weekdays']} weekdays, {result['rooms']} rooms, "
        f"{result['slotEntries']} slot entries"
    )
    print(f"\nSaved CSV:       {result['csvPath']}")
    print(f"Saved HTML:      {result['htmlPath']}")
    print(f"Saved HTML (all): {result['htmlAllDaysPath']}")
    if result.get("uploadedHtml"):
        print("Uploaded to Supabase Storage")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
