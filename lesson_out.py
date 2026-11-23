"""Out-of-lesson log: lessons from the teaching timetable, class lists from iSAMS."""

from __future__ import annotations

import json
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

from isams_timetable_batch import as_list, text_value

_ROOT = Path(__file__).resolve().parent
_ROSTER_PATH = _ROOT / "tmp" / "lesson_out_roster.json"
_lock = threading.Lock()
_building = False
_build_error = ""


def _minutes(value: str) -> int:
    parts = str(value or "").split(":")
    try:
        return int(parts[0]) * 60 + int(parts[1] or 0)
    except ValueError:
        return -1


def _current_period_order(periods: List[Dict[str, Any]], now: datetime) -> Optional[int]:
    minutes = now.hour * 60 + now.minute
    for period in periods:
        start = _minutes(str(period.get("start") or ""))
        end = _minutes(str(period.get("end") or ""))
        if start >= 0 and start <= minutes < end:
            return int(period.get("order") or 0) or None
    return None


def _lessons_from_timetable(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    names = {str(row.get("id")): str(row.get("name") or "") for row in payload.get("staff") or []}
    grouped: Dict[str, Dict[str, Any]] = {}
    slots = payload.get("slots") or {}
    for staff_id, staff_slots in slots.items():
        if not isinstance(staff_slots, dict):
            continue
        teacher = names.get(str(staff_id), "")
        for slot, events in staff_slots.items():
            if not isinstance(events, list):
                continue
            for event in events:
                if not isinstance(event, dict) or event.get("type") != "lesson":
                    continue
                # Teaching staff timetable events expose set code as "set"; setId is optional.
                set_id = str(event.get("setId") or "").strip()
                code = str(event.get("code") or event.get("set") or "").strip()
                if not set_id and not code:
                    continue
                key = f"{slot}|{set_id or code}"
                row = grouped.get(key)
                if row is None:
                    row = {
                        "id": key,
                        "slot": str(slot),
                        "setId": set_id,
                        "subject": str(event.get("subject") or "Lesson"),
                        "code": code,
                        "room": str(event.get("room") or ""),
                        "teachers": [],
                    }
                    grouped[key] = row
                if teacher and teacher not in row["teachers"]:
                    row["teachers"].append(teacher)
                if not row["room"] and event.get("room"):
                    row["room"] = str(event.get("room"))
                if not row["setId"] and set_id:
                    row["setId"] = set_id
                if not row["code"] and code:
                    row["code"] = code
    lessons = list(grouped.values())
    for row in lessons:
        row["teachers"].sort(key=str.casefold)
    lessons.sort(key=lambda row: (row["subject"].casefold(), row["code"].casefold(), row["room"].casefold()))
    return lessons


def lesson_out_index(*, rebuild: bool = False) -> Dict[str, Any]:
    from teaching_staff_timetable import teaching_staff_timetable_payload

    payload = teaching_staff_timetable_payload()
    lessons = _lessons_from_timetable(payload)
    if payload.get("ok") and not lessons:
        payload = teaching_staff_timetable_payload(force=True)
        lessons = _lessons_from_timetable(payload)
    tz = ZoneInfo("Europe/London")
    now = datetime.now(tz)
    weekday = now.weekday()
    periods = list(payload.get("periods") or [])
    roster = _read_roster()
    if roster is None or rebuild:
        _start_roster_build(force=rebuild)
        if rebuild:
            roster = None
    return {
        "ok": True,
        "generatedAt": payload.get("generatedAt") or "",
        "todayDayOrder": weekday + 1 if weekday < 5 else None,
        "currentPeriodOrder": _current_period_order(periods, now) if weekday < 5 else None,
        "days": payload.get("days") or [],
        "periods": periods,
        "lessons": lessons,
        "rosterReady": roster is not None,
        "rosterError": _build_error if roster is None else "",
    }


def lesson_out_class(set_id: str, code: str) -> Dict[str, Any]:
    roster = _read_roster()
    if roster is None:
        _start_roster_build()
        return {"ok": True, "ready": False, "students": [], "error": _build_error}
    students = _students_for(roster, set_id, code)
    return {"ok": True, "ready": True, "students": students, "count": len(students)}


def _students_for(roster: Dict[str, Any], set_id: str, code: str) -> List[Dict[str, str]]:
    by_id = roster.get("byId") or {}
    by_code = roster.get("byCode") or {}
    students = by_id.get(str(set_id or "").strip())
    if not students and code:
        students = by_code.get(str(code).strip().casefold())
    rows = [row for row in (students or []) if isinstance(row, dict)]
    rows.sort(key=lambda row: (str(row.get("surname") or "").casefold(), str(row.get("name") or "").casefold()))
    return [{"id": str(row.get("id") or ""), "name": str(row.get("name") or "")} for row in rows if row.get("name")]


def _read_roster() -> Optional[Dict[str, Any]]:
    if not _ROSTER_PATH.is_file():
        return None
    try:
        data = json.loads(_ROSTER_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or "byId" not in data:
        return None
    return data


def _start_roster_build(*, force: bool = False) -> None:
    global _building, _build_error
    with _lock:
        if _building:
            return
        if not force and (_read_roster() is not None or _build_error):
            return
        if force and _ROSTER_PATH.is_file():
            try:
                _ROSTER_PATH.unlink()
            except OSError:
                pass
        _build_error = ""
        _building = True
    threading.Thread(target=_build_roster_safe, name="lesson-out-roster", daemon=True).start()


def _build_roster_safe() -> None:
    global _building, _build_error
    try:
        roster = _build_roster()
        _ROSTER_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ROSTER_PATH.write_text(json.dumps(roster, ensure_ascii=False), encoding="utf-8")
        with _lock:
            _build_error = ""
    except Exception as exc:
        with _lock:
            _build_error = str(exc)
    finally:
        with _lock:
            _building = False


def _select_timetable(timetables: List[dict]) -> dict:
    if not timetables:
        raise RuntimeError("No published timetables returned from iSAMS")
    if len(timetables) == 1:
        return timetables[0]
    today = date.today()
    boundary = date(today.year, 8, 25)
    start_year = today.year if today >= boundary else today.year - 1
    for item in timetables:
        try:
            if int(text_value(item.get("StartYear"))) == start_year:
                return item
        except ValueError:
            continue
    return max(timetables, key=lambda item: int(text_value(item.get("StartYear")) or 0))


def _pupil_name(pupil: dict) -> tuple[str, str]:
    preferred = text_value(pupil.get("PreferredName") or pupil.get("Preferredname") or pupil.get("Forename"))
    surname = text_value(pupil.get("Surname"))
    full = text_value(pupil.get("Fullname") or pupil.get("FullName"))
    if preferred and surname:
        return f"{preferred} {surname}".strip(), surname
    if surname and full:
        return full, surname
    return full or preferred or surname, surname


def _build_roster() -> Dict[str, Any]:
    from isams_batch_filters import batch_reference_key, batch_url

    response = requests.post(batch_url(), params={"apiKey": batch_reference_key()}, timeout=180)
    response.raise_for_status()
    root = response.json()["iSAMS"]
    timetable_mgr = root["TimetableManager"]
    published = as_list((timetable_mgr.get("PublishedTimetables") or {}).get("Timetable"))
    _select_timetable([item for item in published if isinstance(item, dict)])

    pupils = as_list(((root.get("PupilManager") or {}).get("CurrentPupils") or {}).get("Pupil"))
    names: Dict[str, tuple[str, str]] = {}
    for pupil in pupils:
        if not isinstance(pupil, dict):
            continue
        pid = str(pupil.get("@Id") or "")
        if not pid:
            continue
        names[pid] = _pupil_name(pupil)

    code_by_id: Dict[str, str] = {}
    for item in as_list(((root.get("TeachingManager") or {}).get("Sets") or {}).get("Set")):
        if not isinstance(item, dict):
            continue
        sid = str(item.get("@Id") or "")
        code = text_value(item.get("SetCode") or item.get("Code") or item.get("Name"))
        if sid and code:
            code_by_id[sid] = code

    members: Dict[str, Dict[str, tuple[str, str]]] = {}
    timetable_pupils = as_list(((timetable_mgr.get("Timetable") or {}).get("Pupils") or {}).get("Pupil"))
    for entry in timetable_pupils:
        if not isinstance(entry, dict):
            continue
        pid = str(entry.get("@Id") or "")
        if not pid:
            continue
        person = names.get(pid) or (f"Student {pid}", "")
        for period in as_list((entry.get("Periods") or {}).get("Period")):
            if not isinstance(period, dict):
                continue
            sid = str(period.get("@SetId") or "").strip()
            if not sid:
                continue
            members.setdefault(sid, {})[pid] = person

    by_id: Dict[str, List[Dict[str, str]]] = {}
    by_code: Dict[str, List[Dict[str, str]]] = {}
    for sid, people in members.items():
        rows = [
            {"id": pid, "name": person[0], "surname": person[1]}
            for pid, person in people.items()
            if person[0]
        ]
        rows.sort(key=lambda row: (row["surname"].casefold(), row["name"].casefold()))
        by_id[sid] = rows
        code = code_by_id.get(sid, "")
        if code:
            by_code[code.casefold()] = rows

    return {
        "generatedAt": datetime.now(ZoneInfo("Europe/London")).strftime("%d %b %Y, %H:%M"),
        "setCount": len(by_id),
        "byId": by_id,
        "byCode": by_code,
    }
