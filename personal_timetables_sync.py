"""Generate personal timetable viewer from iSAMS TimetableManager batch data."""

from __future__ import annotations

import html
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

SCRIPT_DIR = Path(__file__).resolve().parent
STATIC_DIR = SCRIPT_DIR / "static" / "personal_timetables"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

if load_dotenv:
    for env_path in (SCRIPT_DIR / ".env", SCRIPT_DIR.parent / ".env"):
        if env_path.is_file():
            load_dotenv(env_path)
            break

URL = (
    os.environ.get("ISAMS_BATCH_API_URL", "").strip()
    or os.environ.get("ISAMS_BATCH_URL", "").strip()
    or "https://halcyonschool.isams.cloud/api/batch/1.0/json.ashx"
)
API_KEY = (
    os.environ.get("ISAMS_BATCH_REFERENCE_API_KEY", "").strip()
    or os.environ.get("ISAMS_BATCH_LEGACY_API_KEY", "").strip()
)
if not API_KEY:
    print(
        "ERROR: set ISAMS_BATCH_REFERENCE_API_KEY in signInApp_server/.env",
        file=sys.stderr,
    )
    raise SystemExit(1)

HTML_PATH = STATIC_DIR / "personal_timetables.html"


def as_list(node):
    if node is None:
        return []
    if isinstance(node, list):
        return node
    return [node]


def text_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get("#text") or value.get("Name") or "").strip()
    return str(value).strip()


def staff_name(staff: dict | None) -> str:
    if not staff:
        return ""
    preferred = text_value(staff.get("PreferredName") or staff.get("Preferredname"))
    forename = text_value(staff.get("Forename"))
    surname = text_value(staff.get("Surname"))
    full = text_value(staff.get("FullName") or staff.get("Fullname"))
    if preferred and surname:
        return f"{preferred} {surname}".strip()
    if forename and surname:
        return f"{forename} {surname}".strip()
    return full


def flatten_staff(root) -> list[dict]:
    staff: list[dict] = []
    for block in as_list(root["HRManager"]["CurrentStaff"]):
        if not isinstance(block, dict):
            continue
        staff.extend(as_list(block.get("StaffMember")))
    return staff


def slot_key(day_order: int, period_order: int) -> str:
    return f"{day_order}-{period_order}"


ACADEMIC_YEAR_START_MONTH = 8
ACADEMIC_YEAR_START_DAY = 25


def academic_year_start_year_on(day: date) -> int:
    boundary = date(day.year, ACADEMIC_YEAR_START_MONTH, ACADEMIC_YEAR_START_DAY)
    if day >= boundary:
        return day.year
    return day.year - 1


def select_published_timetable(timetables: list[dict]) -> dict:
    """Pick the published timetable for the current academic year when several exist."""
    if not timetables:
        raise RuntimeError("No published timetables returned from iSAMS")
    if len(timetables) == 1:
        return timetables[0]
    current_start = academic_year_start_year_on(date.today())
    for item in timetables:
        try:
            if int(text_value(item.get("StartYear"))) == current_start:
                return item
        except ValueError:
            continue
    return max(timetables, key=lambda item: int(text_value(item.get("StartYear")) or 0))


def sync_personal_timetables() -> dict:
    resp = requests.post(URL, params={"apiKey": API_KEY}, timeout=180)
    resp.raise_for_status()
    root = resp.json()["iSAMS"]

    timetable_mgr = root["TimetableManager"]
    timetable = select_published_timetable(as_list(timetable_mgr["PublishedTimetables"]["Timetable"]))

    classrooms = as_list(
        root["EstateManager"]["Buildings"]["Building"]["Classrooms"]["Classroom"]
    )
    room_lookup = {
        str(room["@Id"]): text_value(room.get("Name"))
        for room in classrooms
    }

    sets = as_list(root["TeachingManager"]["Sets"]["Set"])
    set_lookup = {
        str(item["@Id"]): text_value(item.get("Name") or item.get("SetCode") or item.get("Code"))
        for item in sets
    }

    staff_members = flatten_staff(root)
    staff_by_id = {str(item["@Id"]): item for item in staff_members if item.get("@Id")}
    staff_by_usercode = {
        str(item["UserCode"]): item for item in staff_members if item.get("UserCode")
    }

    period_lookup: dict[str, dict] = {}
    period_rows: list[dict] = []
    day_rows: list[dict] = []

    days = as_list(timetable_mgr["Structure"]["Week"]["Days"]["Day"])
    for day in days:
        day_order = int(day["Ordinal"])
        day_rows.append({"order": day_order, "name": text_value(day.get("Name"))})
        periods = as_list(day["Periods"]["Period"])
        for period in periods:
            period_lookup[str(period["@Id"])] = {
                "dayOrder": day_order,
                "periodOrder": int(period["Ordinal"]),
                "name": text_value(period.get("Name")),
                "start": text_value(period.get("StartTime")),
                "end": text_value(period.get("EndTime")),
            }

    monday = next(day for day in days if text_value(day.get("Name")) == "Monday")
    monday_periods = as_list(monday["Periods"]["Period"])
    for period in sorted(monday_periods, key=lambda item: int(item["Ordinal"])):
        start = text_value(period.get("StartTime"))
        end = text_value(period.get("EndTime"))
        period_rows.append(
            {
                "order": int(period["Ordinal"]),
                "name": text_value(period.get("Name")),
                "start": start,
                "end": end,
                "time": f"{start}–{end}" if start and end else "",
            }
        )

    pupils = as_list(root["PupilManager"]["CurrentPupils"]["Pupil"])
    pupil_lookup = {
        str(pupil["@Id"]): pupil for pupil in pupils if pupil.get("@Id")
    }
    for pupil in pupils:
        person_id = pupil.get("PersonId") or pupil.get("@PersonId")
        if person_id:
            pupil_lookup.setdefault(str(person_id), pupil)

    timetable_pupils = as_list(timetable_mgr["Timetable"]["Pupils"]["Pupil"])
    student_slots: dict[str, dict[str, list[dict]]] = {}
    students: list[dict] = []

    for entry in timetable_pupils:
        pupil_id = str(entry.get("@Id") or "")
        if not pupil_id:
            continue
        pupil = pupil_lookup.get(pupil_id, {})
        name = text_value(pupil.get("Fullname") or pupil.get("FullName"))
        if not name:
            name = f"Pupil {pupil_id}"
        nc_year = text_value(pupil.get("NCYear"))
        form = text_value(pupil.get("Form") or pupil.get("DivisionName"))

        slots: dict[str, list[dict]] = {}
        for period in as_list(entry.get("Periods", {}).get("Period")):
            meta = period_lookup.get(str(period.get("@Id")))
            if not meta:
                continue
            teacher = staff_name(staff_by_id.get(str(period.get("@TeacherId"))))
            subject = set_lookup.get(str(period.get("@SetId")), "")
            room = room_lookup.get(str(period.get("@RoomId")), "")
            cell = {
                "subject": subject,
                "room": room,
                "teacher": teacher,
                "type": "lesson",
            }
            key = slot_key(meta["dayOrder"], meta["periodOrder"])
            slots.setdefault(key, []).append(cell)

        student_slots[pupil_id] = slots
        students.append(
            {
                "id": pupil_id,
                "name": name,
                "ncYear": nc_year,
                "form": form,
                "label": " — ".join(part for part in [name, f"Y{nc_year}" if nc_year else "", form] if part),
            }
        )

    students.sort(
        key=lambda item: (
            int(item["ncYear"]) if str(item.get("ncYear", "")).isdigit() else 999,
            item["name"].casefold(),
        )
    )

    teacher_slots: dict[str, dict[str, list[dict]]] = {}
    teacher_names: dict[str, str] = {}


    def teacher_label(code: str) -> str:
        if code in teacher_names:
            return teacher_names[code]
        staff = staff_by_usercode.get(code) or staff_by_id.get(code)
        name = staff_name(staff) or code
        teacher_names[code] = name
        return name


    def add_teacher_event(code: str, period_id: str, room_id: str, subject: str, event_type: str) -> None:
        if not code:
            return
        meta = period_lookup.get(str(period_id))
        if not meta:
            return
        room = room_lookup.get(str(room_id), "")
        key = slot_key(meta["dayOrder"], meta["periodOrder"])
        teacher_slots.setdefault(code, {}).setdefault(key, []).append(
            {
                "subject": subject,
                "room": room,
                "type": event_type,
            }
        )


    for schedule in as_list(timetable["Schedules"]["Schedule"]):
        code = text_value(schedule.get("Teacher"))
        subject = text_value(schedule.get("Code"))
        set_name = set_lookup.get(str(schedule.get("SetId")), "")
        if set_name and set_name != subject:
            subject = set_name if not subject else f"{subject} ({set_name})"
        add_teacher_event(
            code,
            schedule.get("PeriodId"),
            schedule.get("RoomId"),
            subject or "Lesson",
            "lesson",
        )

    for meeting in as_list(timetable.get("StaffMeetings", {}).get("StaffMeeting")):
        code = text_value(meeting.get("TeacherId") or meeting.get("Author"))
        subject = text_value(meeting.get("DisplayName") or "Staff meeting")
        add_teacher_event(
            code,
            meeting.get("PeriodId"),
            meeting.get("RoomId"),
            subject,
            "meeting",
        )

    teachers = [
        {
            "id": code,
            "name": teacher_label(code),
            "label": teacher_label(code),
            "lessons": sum(len(items) for items in slots.values()),
        }
        for code, slots in teacher_slots.items()
    ]
    teachers.sort(key=lambda item: item["name"].casefold())

    timetable_label = (
        f"{text_value(timetable.get('Name'))} "
        f"({text_value(timetable.get('StartYear'))}-{text_value(timetable.get('EndYear'))})"
    ).strip()
    generated_at = datetime.now().strftime("%d %b %Y, %H:%M")

    page_data = {
        "periods": period_rows,
        "days": day_rows,
        "students": students,
        "teachers": teachers,
        "studentSlots": student_slots,
        "teacherSlots": teacher_slots,
        "timetableLabel": timetable_label,
        "generatedAt": generated_at,
    }

    page_data_json = json.dumps(page_data, ensure_ascii=False, separators=(",", ":"))

    styles = """
        :root {
          color-scheme: light;
          --bg: #eef2f7;
          --card: #ffffff;
          --text: #1f2937;
          --muted: #6b7280;
          --border: #d1d5db;
          --lesson: #dbeafe;
          --lesson-border: #93c5fd;
          --meeting: #ede9fe;
          --meeting-border: #c4b5fd;
          --free: #f9fafb;
        }
        * { box-sizing: border-box; }
        .page-personal-timetables .pt-content {
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          color: var(--text);
          line-height: 1.35;
          padding: 0.5rem 0.65rem 1rem;
        }
        .page-personal-timetables .pt-content .header-panel,
        .page-personal-timetables .pt-content .grid-panel {
          background: var(--card);
          border: 1px solid var(--border);
          border-radius: 12px;
          padding: 20px;
          margin-bottom: 24px;
        }
        h1 { margin: 0 0 8px; font-size: 1.7rem; }
        .meta { color: var(--muted); margin: 0; font-size: 0.9rem; }
        .header-top {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 16px;
          flex-wrap: wrap;
          margin-bottom: 16px;
        }
        .header-main { min-width: 0; }
        .header-actions {
          display: flex;
          align-items: center;
          gap: 8px;
          flex-wrap: wrap;
          margin-left: auto;
        }
        .mode-tab,
        #refresh-btn,
        .grid-toolbar .btn {
          padding: 8px 14px;
          border: 1px solid var(--border);
          border-radius: 8px;
          background: #f9fafb;
          font: inherit;
          font-size: 0.9rem;
          line-height: 1.35;
          cursor: pointer;
          box-sizing: border-box;
        }
        #refresh-btn {
          background: #2563eb;
          color: #fff;
          border-color: #2563eb;
          font-weight: 600;
        }
        #refresh-btn:hover:not(:disabled) {
          background: #1d4ed8;
          border-color: #1d4ed8;
        }
        #refresh-btn:disabled {
          opacity: 0.65;
          cursor: wait;
        }
        .mode-tabs {
          display: inline-flex;
          gap: 8px;
          flex-wrap: wrap;
        }
        .grid-toolbar .btn {
          min-height: 0;
          min-width: 0;
          height: auto;
          font-weight: 600;
        }
        .mode-tab.is-active {
          background: #dbeafe;
          border-color: #93c5fd;
          font-weight: 600;
        }
        .grid-toolbar .btn:hover {
          background: #eff6ff;
          border-color: #93c5fd;
        }
        .control-row {
          display: flex;
          gap: 16px;
          flex-wrap: wrap;
          align-items: end;
        }
        .field {
          display: flex;
          flex-direction: column;
          gap: 6px;
          min-width: 200px;
          flex: 1 1 0;
        }
        .field label {
          font-weight: 700;
          font-size: 0.95rem;
        }
        .field input,
        .field select {
          padding: 8px 10px;
          border: 1px solid var(--border);
          border-radius: 8px;
          font: inherit;
          font-size: 0.9rem;
          background: #fff;
          width: 100%;
          height: 38px;
          min-height: 38px;
          box-sizing: border-box;
        }
        .grid-toolbar {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 16px;
          flex-wrap: wrap;
          margin-bottom: 12px;
        }
        .grid-title-wrap {
          display: flex;
          align-items: baseline;
          gap: 12px;
          flex-wrap: wrap;
          min-width: 0;
        }
        .grid-person-name {
          font-size: 1rem;
          font-weight: 600;
          color: var(--text);
        }
        .grid-timetable-meta {
          font-size: 0.9rem;
          color: var(--muted);
        }
        .grid-scroll { overflow: auto; }
        .timetable-grid {
          width: 100%;
          border-collapse: collapse;
          min-width: 760px;
        }
        .timetable-grid th,
        .timetable-grid td {
          border: 1px solid var(--border);
          vertical-align: top;
          padding: 8px;
        }
        .corner,
        .period-head {
          background: #f3f4f6;
          font-weight: 600;
          white-space: nowrap;
        }
        .period-head .period-time {
          display: block;
          font-size: 0.82rem;
          color: var(--muted);
          font-weight: 500;
        }
        .day-head {
          background: #f3f4f6;
          text-align: center;
          min-width: 130px;
        }
        .timetable-grid td.now-marker-cell {
          position: relative;
          overflow: visible;
        }
        .now-line {
          position: absolute;
          left: 0;
          right: 0;
          height: 2px;
          background: #dc2626;
          pointer-events: none;
          z-index: 2;
          transform: translateY(-1px);
        }
        .cell-free {
          color: var(--muted);
          font-size: 0.88rem;
          text-align: center;
          padding: 12px 8px;
        }
        .block {
          border-radius: 8px;
          padding: 6px 8px;
          margin-bottom: 6px;
          font-size: 0.88rem;
          border: 1px solid transparent;
        }
        .block:last-child { margin-bottom: 0; }
        .block.lesson {
          background: var(--lesson);
          border-color: var(--lesson-border);
        }
        .block.meeting {
          background: var(--meeting);
          border-color: var(--meeting-border);
        }
        .block .subject { font-weight: 600; }
        .block .meta-line {
          color: var(--muted);
          font-size: 0.82rem;
          margin-top: 2px;
        }
        .empty-state {
          color: var(--muted);
          padding: 24px 12px;
          text-align: center;
        }
        @media print {
          .header-panel, .halcyon-app > header, .drawer-overlay { display: none !important; }
          .page-personal-timetables .pt-content { padding: 0; }
          .grid-toolbar .btn, #refresh-btn { display: none !important; }
        }
    """

    body_inner = f"""
        <section class="header-panel">
          <div class="header-top">
            <div class="header-main">
              <h1>Personal Timetables</h1>
              <p class="meta">Generated {html.escape(generated_at)} · {len(students)} students · {len(teachers)} teachers</p>
            </div>
            <div class="header-actions">
              <button type="button" class="mode-tab" id="refresh-btn">Refresh from iSAMS</button>
              <div class="mode-tabs" role="tablist" aria-label="Timetable type">
                <button type="button" class="mode-tab is-active" data-mode="student" role="tab" aria-selected="true">Students</button>
                <button type="button" class="mode-tab" data-mode="teacher" role="tab" aria-selected="false">Teachers</button>
              </div>
            </div>
          </div>
          <div class="control-row">
            <div class="field" id="year-filter-wrap">
              <label for="year-filter">Year group</label>
              <select id="year-filter" aria-label="Filter by year group">
                <option value="">All years</option>
              </select>
            </div>
            <div class="field">
              <label for="person-search">Search</label>
              <input id="person-search" type="search" placeholder="Type a name…" autocomplete="off" />
            </div>
            <div class="field">
              <label for="person-picker" id="person-picker-label">Student</label>
              <select id="person-picker" aria-label="Select person"></select>
            </div>
          </div>
        </section>
        <section class="grid-panel">
          <div class="grid-toolbar">
            <div class="grid-title-wrap">
              <span id="grid-title" class="grid-person-name"></span>
              <span id="grid-timetable-label" class="grid-timetable-meta">{html.escape(timetable_label)}</span>
            </div>
            <button type="button" class="btn" id="print-btn">Print</button>
          </div>
          <div class="grid-scroll">
            <table class="timetable-grid" id="timetable-grid">
              <thead id="grid-head"></thead>
              <tbody id="grid-body"></tbody>
            </table>
          </div>
          <div id="empty-state" class="empty-state" hidden>Select a person to view their timetable.</div>
        </section>
    """

    scripts = f"""
      <script id="timetable-data" type="application/json">{page_data_json}</script>
      <script>
        const DATA = JSON.parse(document.getElementById("timetable-data").textContent);
        const modeTabs = Array.from(document.querySelectorAll(".mode-tab"));
        const yearFilter = document.getElementById("year-filter");
        const yearFilterWrap = document.getElementById("year-filter-wrap");
        const personSearch = document.getElementById("person-search");
        const personPicker = document.getElementById("person-picker");
        const personPickerLabel = document.getElementById("person-picker-label");
        const gridHead = document.getElementById("grid-head");
        const gridBody = document.getElementById("grid-body");
        const gridTitle = document.getElementById("grid-title");
        const gridTimetableLabel = document.getElementById("grid-timetable-label");
        const emptyState = document.getElementById("empty-state");
        const printBtn = document.getElementById("print-btn");
        const refreshBtn = document.getElementById("refresh-btn");

        let mode = "student";

        function escapeHtml(value) {{
          return String(value ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
        }}

        function peopleForMode() {{
          return mode === "student" ? DATA.students : DATA.teachers;
        }}

        function slotsForPerson(id) {{
          if (!id) return {{}};
          return mode === "student"
            ? (DATA.studentSlots[id] || {{}})
            : (DATA.teacherSlots[id] || {{}});
        }}

        function populateYearFilter() {{
          const years = [...new Set(DATA.students.map((item) => item.ncYear).filter(Boolean))]
            .sort((a, b) => (Number(a) || 999) - (Number(b) || 999));
          yearFilter.innerHTML = '<option value="">All years</option>' +
            years.map((year) => `<option value="${{escapeHtml(year)}}">Year ${{escapeHtml(year)}}</option>`).join("");
        }}

        function sortStudents(list) {{
          return list.slice().sort((a, b) => {{
            const ya = Number(a.ncYear) || 999;
            const yb = Number(b.ncYear) || 999;
            if (ya !== yb) return ya - yb;
            return a.name.localeCompare(b.name, undefined, {{ sensitivity: "base" }});
          }});
        }}

        function filteredPeople() {{
          const query = personSearch.value.trim().toLowerCase();
          const year = yearFilter.value;
          const filtered = peopleForMode().filter((person) => {{
            if (mode === "student" && year && person.ncYear !== year) return false;
            if (!query) return true;
            return person.label.toLowerCase().includes(query);
          }});
          return mode === "student" ? sortStudents(filtered) : filtered.sort((a, b) => a.name.localeCompare(b.name, undefined, {{ sensitivity: "base" }}));
        }}

        function renderPicker() {{
          const people = filteredPeople();
          const previous = personPicker.value;
          personPicker.innerHTML = people
            .map((person) => `<option value="${{escapeHtml(person.id)}}">${{escapeHtml(person.label)}}</option>`)
            .join("");
          if (people.some((person) => person.id === previous)) {{
            personPicker.value = previous;
          }} else if (people.length) {{
            personPicker.selectedIndex = 0;
          }} else {{
            personPicker.innerHTML = '<option value="">No matches</option>';
          }}
          renderGrid();
        }}

        function renderBlock(event) {{
          const cssClass = event.type === "meeting" ? "block meeting" : "block lesson";
          const lines = [];
          if (event.subject) lines.push(`<div class="subject">${{escapeHtml(event.subject)}}</div>`);
          if (event.room) lines.push(`<div class="meta-line">${{escapeHtml(event.room)}}</div>`);
          if (event.teacher) lines.push(`<div class="meta-line">${{escapeHtml(event.teacher)}}</div>`);
          return `<div class="${{cssClass}}">${{lines.join("")}}</div>`;
        }}

        function parseTime(value) {{
          const parts = String(value || "").split(":");
          return Number(parts[0]) * 60 + Number(parts[1] || 0);
        }}

        function getTodayDayOrder() {{
          const weekday = new Date().getDay();
          return weekday >= 1 && weekday <= 5 ? weekday : null;
        }}

        function getCurrentPeriodOrder() {{
          const now = new Date();
          const minutes = now.getHours() * 60 + now.getMinutes();
          for (const period of DATA.periods) {{
            const start = parseTime(period.start);
            const end = parseTime(period.end);
            if (minutes >= start && minutes < end) {{
              return period.order;
            }}
          }}
          return null;
        }}

        function progressInCurrentPeriod(period) {{
          const now = new Date();
          const minutes = now.getHours() * 60 + now.getMinutes();
          const start = parseTime(period.start);
          const end = parseTime(period.end);
          if (minutes < start || minutes >= end || end <= start) {{
            return null;
          }}
          return (minutes - start) / (end - start);
        }}

        function nowLineHtml(period) {{
          const progress = progressInCurrentPeriod(period);
          if (progress === null) {{
            return "";
          }}
          const topPct = Math.min(100, Math.max(0, progress * 100));
          return `<div class="now-line" style="top:${{topPct}}%"></div>`;
        }}

        function renderGrid() {{
          const personId = personPicker.value;
          const person = peopleForMode().find((item) => item.id === personId);
          const slots = slotsForPerson(personId);
          const todayDayOrder = getTodayDayOrder();
          const currentPeriodOrder = getCurrentPeriodOrder();

          if (!person) {{
            gridHead.innerHTML = "";
            gridBody.innerHTML = "";
            gridTitle.textContent = "";
            gridTimetableLabel.hidden = true;
            emptyState.hidden = false;
            return;
          }}

          emptyState.hidden = true;
          gridTitle.textContent = person.label;
          gridTimetableLabel.hidden = false;

          const dayHeaders = DATA.days
            .map((day) => `<th class="day-head">${{escapeHtml(day.name)}}</th>`)
            .join("");
          gridHead.innerHTML =
            `<tr><th class="corner">Period</th>${{dayHeaders}}</tr>`;

          const rows = DATA.periods.map((period) => {{
            const cells = DATA.days.map((day) => {{
              const key = `${{day.order}}-${{period.order}}`;
              const events = slots[key] || [];
              const isNowCell = todayDayOrder !== null
                && day.order === todayDayOrder
                && currentPeriodOrder !== null
                && period.order === currentPeriodOrder;
              const nowLine = isNowCell ? nowLineHtml(period) : "";
              const cellClass = isNowCell ? "now-marker-cell" : "";
              if (!events.length) {{
                return `<td class="cell-free ${{cellClass}}">-${{nowLine}}</td>`;
              }}
              return `<td class="${{cellClass.trim()}}">${{events.map(renderBlock).join("")}}${{nowLine}}</td>`;
            }}).join("");
            return `<tr>
              <th class="period-head">
                <span>${{escapeHtml(period.name)}}</span>
                <span class="period-time">${{escapeHtml(period.time)}}</span>
              </th>${{cells}}
            </tr>`;
          }}).join("");
          gridBody.innerHTML = rows;
        }}

        function setMode(nextMode) {{
          mode = nextMode;
          modeTabs.forEach((tab) => {{
            const active = tab.dataset.mode === mode;
            tab.classList.toggle("is-active", active);
            tab.setAttribute("aria-selected", active ? "true" : "false");
          }});
          yearFilterWrap.hidden = mode !== "student";
          personPickerLabel.textContent = mode === "student" ? "Student" : "Teacher";
          personSearch.value = "";
          renderPicker();
        }}

        modeTabs.forEach((tab) => {{
          tab.addEventListener("click", () => setMode(tab.dataset.mode));
        }});
        yearFilter.addEventListener("change", renderPicker);
        personSearch.addEventListener("input", renderPicker);
        personPicker.addEventListener("change", renderGrid);
        printBtn.addEventListener("click", () => window.print());
        refreshBtn.addEventListener("click", () => {{
          if (!confirm("Reload personal timetables from iSAMS? This may take a few minutes.")) {{
            return;
          }}
          refreshBtn.disabled = true;
          refreshBtn.textContent = "Refreshing…";
          fetch("/api/personal-timetables/resync", {{
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
                throw new Error((data && data.error) || response.statusText);
              }}
              return data;
            }}))
            .then(() => window.location.reload())
            .catch((err) => {{
              alert(err.message || "Refresh failed");
              refreshBtn.disabled = false;
              refreshBtn.textContent = "Refresh from iSAMS";
            }});
        }});
        setInterval(() => {{
          if (personPicker.value) renderGrid();
        }}, 60000);

        populateYearFilter();
        setMode("student");
      </script>
    """


    from halcyon_tools_section import wrap_tools_section_page

    def wrap_halcyon_page(title: str, inner_styles: str, body: str, tail_scripts: str) -> str:
        return wrap_tools_section_page(
            title=title,
            section_page="personaltimetables",
            page_class="page-personal-timetables",
            content_class="pt-content",
            inner_styles=inner_styles,
            body_inner=body,
            tail_scripts=tail_scripts,
        )


    def upload_to_supabase_storage(
        local_path: Path, remote_name: str, content_type: str = "text/html; charset=utf-8"
    ) -> bool:
        url = os.environ.get("SUPABASE_URL", "").strip()
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        bucket = os.environ.get(
            "SUPABASE_PERSONAL_TIMETABLES_BUCKET",
            os.environ.get("SUPABASE_TIMETABLED_ROOMS_BUCKET", "timetabled-rooms"),
        ).strip()
        if not url or not key:
            print("WARN: Supabase not configured — HTML saved locally only.", file=sys.stderr)
            return False
        try:
            from supabase import create_client

            sb = create_client(url, key)
            data = local_path.read_bytes()
            sb.storage.from_(bucket).upload(
                remote_name,
                data,
                {"content-type": content_type, "upsert": "true"},
            )
            print(f"Uploaded to Supabase storage {bucket}/{remote_name}")
            return True
        except Exception as exc:
            print(f"WARN: could not upload {remote_name}: {exc}", file=sys.stderr)
            return False

    html_doc = wrap_halcyon_page("Personal Timetables", styles, body_inner, scripts)
    HTML_PATH.write_text(html_doc, encoding="utf-8")
    uploaded = upload_to_supabase_storage(HTML_PATH, "personal_timetables.html")
    return {
        "timetableLabel": timetable_label,
        "students": len(students),
        "teachers": len(teachers),
        "generatedAt": generated_at,
        "uploaded": uploaded,
        "htmlPath": str(HTML_PATH),
    }


def main() -> int:
    result = sync_personal_timetables()
    print(f"\nPublished timetable: {result['timetableLabel']}")
    print(f"Students: {result['students']}, Teachers: {result['teachers']}")
    print(f"Saved HTML: {result['htmlPath']}")
    if result.get("uploaded"):
        print("Uploaded to Supabase Storage")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
