"""Shared iSAMS batch TimetableManager / TeachingManager lookups."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple


def as_list(node: Any) -> list:
    if node is None:
        return []
    if isinstance(node, list):
        return node
    return [node]


def text_value(value: Any) -> str:
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


def teacher_salutation_surname(staff: dict | None) -> str:
    """Display line: Title + Surname (e.g. Mr Afonso), else Salutation or full name."""
    if not staff:
        return ""
    title = text_value(staff.get("Title"))
    surname = text_value(staff.get("Surname"))
    if title and surname:
        return f"{title} {surname}".strip()
    salutation = text_value(staff.get("Salutation"))
    if salutation:
        return salutation
    return staff_name(staff)


def flatten_staff(root: dict) -> list[dict]:
    staff: list[dict] = []
    hr = root.get("HRManager") or {}
    for block in as_list(hr.get("CurrentStaff")):
        if not isinstance(block, dict):
            continue
        staff.extend(as_list(block.get("StaffMember")))
    return staff


def build_staff_lookups(root: dict) -> Tuple[Dict[str, dict], Dict[str, dict]]:
    staff_members = flatten_staff(root)
    staff_by_id = {str(item["@Id"]): item for item in staff_members if item.get("@Id")}
    staff_by_usercode = {
        str(item["UserCode"]): item for item in staff_members if item.get("UserCode")
    }
    return staff_by_id, staff_by_usercode


def teacher_staff_member(
    teacher_code: str,
    *,
    staff_by_id: Dict[str, dict],
    staff_by_usercode: Dict[str, dict],
) -> Optional[dict]:
    code = text_value(teacher_code)
    if not code:
        return None
    return staff_by_usercode.get(code) or staff_by_id.get(code)


def teacher_display_name(
    teacher_code: str,
    *,
    staff_by_id: Dict[str, dict],
    staff_by_usercode: Dict[str, dict],
) -> str:
    staff = teacher_staff_member(
        teacher_code, staff_by_id=staff_by_id, staff_by_usercode=staff_by_usercode
    )
    if staff:
        line = teacher_salutation_surname(staff)
        if line:
            return line
    return text_value(teacher_code)


def build_set_lookups(root: dict) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Map set @Id and set code (casefold) → display name."""
    sets = as_list((root.get("TeachingManager") or {}).get("Sets", {}).get("Set"))
    by_id: Dict[str, str] = {}
    code_to_id: Dict[str, str] = {}
    for item in sets:
        sid = str(item.get("@Id") or "")
        if not sid:
            continue
        label = text_value(item.get("Name") or item.get("SetCode") or item.get("Code"))
        by_id[sid] = label
        for key in (item.get("SetCode"), item.get("Name"), item.get("Code")):
            code = text_value(key)
            if code:
                code_to_id[code.casefold()] = sid
    return by_id, code_to_id


def build_pupil_counts_by_set_id(root: dict) -> Dict[str, int]:
    """Distinct pupils per teaching set (@SetId) from TimetableManager pupil periods."""
    tm = root.get("TimetableManager") or {}
    pupils = as_list((tm.get("Timetable") or {}).get("Pupils", {}).get("Pupil"))
    members: Dict[str, set[str]] = defaultdict(set)
    for pupil in pupils:
        pid = str(pupil.get("@Id") or "")
        if not pid:
            continue
        periods = as_list((pupil.get("Periods") or {}).get("Period"))
        for period in periods:
            sid = str(period.get("@SetId") or "")
            if sid:
                members[sid].add(pid)
    return {sid: len(ids) for sid, ids in members.items()}


def resolve_teaching_set_id(schedule: dict, code_to_id: Dict[str, str]) -> str:
    sid = text_value(schedule.get("SetId"))
    if sid and sid != "1":
        return sid
    code = text_value(schedule.get("Code"))
    if code:
        return code_to_id.get(code.casefold(), "")
    return sid


def lesson_event_from_schedule(
    schedule: dict,
    *,
    set_by_id: Dict[str, str],
    code_to_id: Dict[str, str],
    pupil_counts: Dict[str, int],
    staff_by_id: Dict[str, dict],
    staff_by_usercode: Dict[str, dict],
) -> dict:
    code = text_value(schedule.get("Code"))
    set_id = resolve_teaching_set_id(schedule, code_to_id)
    set_name = set_by_id.get(set_id, "")
    subject = code
    if set_name and set_name != code:
        subject = f"{code} ({set_name})" if code else set_name
    teacher = teacher_display_name(
        schedule.get("Teacher"),
        staff_by_id=staff_by_id,
        staff_by_usercode=staff_by_usercode,
    )
    count = pupil_counts.get(set_id) if set_id else None
    class_name = code or set_name or subject or "Lesson"
    return {
        "subject": subject or "Lesson",
        "className": class_name,
        "code": code,
        "teacher": teacher,
        "studentCount": count,
        "type": "lesson",
    }


def staff_meeting_event(
    meeting: dict,
    *,
    staff_by_id: Dict[str, dict],
    staff_by_usercode: Dict[str, dict],
) -> dict:
    subject = text_value(meeting.get("DisplayName") or "Staff meeting")
    teacher = teacher_display_name(
        meeting.get("TeacherId") or meeting.get("Author"),
        staff_by_id=staff_by_id,
        staff_by_usercode=staff_by_usercode,
    )
    return {
        "subject": subject,
        "className": subject,
        "code": "",
        "teacher": teacher,
        "studentCount": None,
        "type": "staff",
    }
