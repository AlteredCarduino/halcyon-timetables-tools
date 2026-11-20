"""Teaching-staff timetable: one row per iSAMS teaching employee, periods in columns."""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

import httpx

from isams_registration import isams_get_token, load_isams_config_from_env

_MT_ROLE = re.compile(r"mt[\s-]*teacher", re.IGNORECASE)
_EXCLUDED_ROLE = re.compile(r"peripatetic|consultant|\badmin", re.IGNORECASE)
_CACHE_TTL_SECONDS = 10 * 60
_cache_lock = threading.Lock()
_cache: Dict[str, Any] = {"at": 0.0, "payload": None}


def _get_json(client: httpx.Client, base: str, path: str) -> Dict[str, Any]:
    response = client.get(base + path)
    if response.status_code >= 400:
        raise RuntimeError(f"REST GET {path} failed ({response.status_code})")
    body = response.json() if response.text.strip() else {}
    return body if isinstance(body, dict) else {}


def _display_name(emp: Dict[str, Any]) -> str:
    preferred = str(emp.get("preferredName") or emp.get("forename") or "").strip()
    surname = str(emp.get("surname") or "").strip()
    if preferred and surname:
        return f"{preferred} {surname}"
    full = str(emp.get("fullName") or "").strip()
    return full or preferred or surname or f"Staff {emp.get('id')}"


def _still_employed(emp: Dict[str, Any], today) -> bool:
    raw = str(emp.get("leavingDate") or "").strip()
    if not raw:
        return True
    try:
        left = datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return True
    return left > today


def _load_employees(client: httpx.Client, base: str) -> List[Dict[str, Any]]:
    page = 1
    employees: List[Dict[str, Any]] = []
    while True:
        body = _get_json(
            client, base, f"/api/humanresources/employees?page={page}&pageSize=200"
        )
        chunk = [row for row in (body.get("employees") or []) if isinstance(row, dict)]
        employees.extend(chunk)
        total_pages = int(body.get("totalPages") or 1)
        if page >= total_pages or not chunk:
            break
        page += 1
    return employees


def _timetable_structure(
    client: httpx.Client, base: str
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[int, Tuple[int, int]]]:
    body = _get_json(client, base, "/api/timetables/structure")
    weeks = [week for week in (body.get("timetableWeeks") or []) if isinstance(week, dict)]
    if not weeks:
        raise RuntimeError("iSAMS timetable structure has no weeks")
    days: List[Dict[str, Any]] = []
    columns: List[Dict[str, Any]] = []
    period_by_id: Dict[int, Tuple[int, int]] = {}
    for day in weeks[0].get("timetableDays") or []:
        if not isinstance(day, dict):
            continue
        day_order = int(day.get("ordinal") or 0)
        days.append(
            {
                "order": day_order,
                "name": str(day.get("name") or "").strip(),
                "shortName": str(day.get("shortName") or "").strip(),
            }
        )
        periods = [p for p in (day.get("periods") or []) if isinstance(p, dict)]
        periods.sort(key=lambda period: int(period.get("ordinal") or 0))
        for period in periods:
            order = int(period.get("ordinal") or 0)
            start = str(period.get("startTime") or "")[:5]
            end = str(period.get("endTime") or "")[:5]
            name = str(period.get("shortName") or period.get("name") or "").strip()
            period_by_id[int(period["id"])] = (day_order, order)
            if day_order == 1:
                columns.append(
                    {
                        "order": order,
                        "name": name,
                        "start": start,
                        "end": end,
                        "time": f"{start}–{end}" if start and end else "",
                    }
                )
    days.sort(key=lambda item: item["order"])
    if not columns:
        raise RuntimeError("iSAMS timetable structure has no periods")
    return days, columns, period_by_id


def _events_for(
    body: Dict[str, Any], period_by_id: Dict[int, Tuple[int, int]]
) -> Dict[str, List[Dict[str, str]]]:
    slots: Dict[str, List[Dict[str, str]]] = {}

    def add(period_id: Any, event: Dict[str, str]) -> None:
        if period_id is None:
            return
        try:
            mapped = period_by_id.get(int(period_id))
        except (TypeError, ValueError):
            return
        if not mapped:
            return
        key = f"{mapped[0]}-{mapped[1]}"
        slots.setdefault(key, []).append(event)

    for item in body.get("sets") or []:
        if not isinstance(item, dict):
            continue
        subject = str(item.get("subjectCode") or "").strip()
        code = str(item.get("code") or "").strip()
        add(
            item.get("periodId"),
            {
                "subject": subject or code or "Lesson",
                "set": code if code and code != subject else "",
                "code": code,
                "setId": str(item.get("id") or "").strip(),
                "room": str(item.get("roomCode") or "").strip(),
                "type": "lesson",
            },
        )
    for item in body.get("meetings") or []:
        if not isinstance(item, dict):
            continue
        add(
            item.get("periodId"),
            {
                "subject": str(item.get("displayName") or "Staff meeting").strip() or "Staff meeting",
                "set": "",
                "room": str(item.get("roomCode") or "").strip(),
                "type": "meeting",
            },
        )
    for events in slots.values():
        events.sort(key=lambda event: (0 if event["type"] == "lesson" else 1, event["subject"].casefold()))
    return slots


def _load_employee(
    client: httpx.Client,
    base: str,
    emp: Dict[str, Any],
    period_by_id: Dict[int, Tuple[int, int]],
) -> Dict[str, Any]:
    eid = emp.get("id")
    mt = False
    excluded = False
    failed = False
    slots: Dict[str, List[Dict[str, str]]] = {}
    try:
        roles = _get_json(client, base, f"/api/humanresources/employees/{eid}/roles")
        for role in roles.get("roles") or []:
            if not isinstance(role, dict):
                continue
            name = str(role.get("name") or "")
            if _EXCLUDED_ROLE.search(name):
                excluded = True
            elif _MT_ROLE.search(name):
                mt = True
    except Exception:
        failed = True
    try:
        timetable = _get_json(client, base, f"/api/timetables/employees/{eid}")
        slots = _events_for(timetable, period_by_id)
    except Exception:
        failed = True
    return {
        "id": str(eid),
        "name": _display_name(emp),
        "mtTeacher": mt,
        "excluded": excluded,
        "failed": failed,
        "slots": slots,
    }


def _build_payload() -> Dict[str, Any]:
    cfg = load_isams_config_from_env()
    token = isams_get_token(cfg)
    base = cfg["base_url"]
    tz = ZoneInfo(cfg.get("tz") or "Europe/London")
    now = datetime.now(tz)
    today = now.date()
    headers = {"Authorization": f"Bearer {token}"}
    limits = httpx.Limits(max_connections=12, max_keepalive_connections=12)
    with httpx.Client(timeout=60.0, headers=headers, limits=limits) as client:
        employees = _load_employees(client, base)
        teaching = [
            emp
            for emp in employees
            if emp.get("isTeachingStaff") is True and _still_employed(emp, today)
        ]
        days, periods, period_by_id = _timetable_structure(client, base)
        rows: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [
                pool.submit(_load_employee, client, base, emp, period_by_id) for emp in teaching
            ]
            for future in as_completed(futures):
                row = future.result()
                if row.get("excluded"):
                    continue
                rows.append(row)
    rows.sort(key=lambda row: (1 if row["mtTeacher"] else 0, row["name"].casefold()))
    slots = {row["id"]: row.pop("slots") for row in rows}
    failed = sum(1 for row in rows if row.pop("failed", False))
    for row in rows:
        row.pop("excluded", None)
    weekday = today.weekday()
    return {
        "ok": True,
        "generatedAt": now.strftime("%d %b %Y, %H:%M"),
        "todayDayOrder": weekday + 1 if weekday < 5 else None,
        "days": days,
        "periods": periods,
        "staff": rows,
        "slots": slots,
        "staffCount": len(rows),
        "mtCount": sum(1 for row in rows if row["mtTeacher"]),
        "failedCount": failed,
    }


def teaching_staff_timetable_payload(*, force: bool = False) -> Dict[str, Any]:
    now = time.time()
    with _cache_lock:
        cached = _cache.get("payload")
        if (
            not force
            and isinstance(cached, dict)
            and cached.get("ok")
            and now - float(_cache.get("at") or 0) < _CACHE_TTL_SECONDS
        ):
            return cached
    payload = _build_payload()
    with _cache_lock:
        _cache["at"] = time.time()
        _cache["payload"] = payload
    return payload
