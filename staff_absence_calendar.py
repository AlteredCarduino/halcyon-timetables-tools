"""Staff absence Google Calendar — ICS feed and Calendar API fallback."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from google_credentials import google_credentials_available, load_google_service_account_credentials

DEFAULT_CALENDAR_ID = (
    "halcyonschool.com_u0hh4k04k0hjd9asltr9aklbko@group.calendar.google.com"
)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def staff_absence_calendar_id() -> str:
    return _env("STAFF_ABSENCE_GOOGLE_CALENDAR_ID", DEFAULT_CALENDAR_ID)


def _access_token() -> str:
    from google.auth.transport.requests import Request

    creds = load_google_service_account_credentials(
        ["https://www.googleapis.com/auth/calendar.readonly"]
    )
    creds.refresh(Request())
    return creds.token


def _parse_api_datetime(raw: str, tz: ZoneInfo) -> datetime:
    if raw.endswith("Z"):
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt


def _api_item_to_event(item: Dict[str, Any], tz: ZoneInfo) -> Optional[Dict[str, Any]]:
    title = (item.get("summary") or "").strip()
    if not title:
        return None
    start = item.get("start") or {}
    end = item.get("end") or {}
    if start.get("date"):
        d0 = datetime.strptime(start["date"], "%Y-%m-%d").date()
        end_raw = end.get("date")
        if end_raw:
            end_exclusive = datetime.strptime(end_raw, "%Y-%m-%d").date()
            last = end_exclusive - timedelta(days=1)
        else:
            last = d0
        return {
            "title": title,
            "allDay": True,
            "startDate": d0.isoformat(),
            "endDate": last.isoformat(),
            "startTime": None,
            "endTime": None,
            "source": "staff",
        }
    if start.get("dateTime"):
        t0 = _parse_api_datetime(start["dateTime"], tz).astimezone(tz)
        d0 = t0.date()
        end_label = None
        if end.get("dateTime"):
            te = _parse_api_datetime(end["dateTime"], tz).astimezone(tz)
            end_label = te.strftime("%H:%M")
        return {
            "title": title,
            "allDay": False,
            "startDate": d0.isoformat(),
            "endDate": d0.isoformat(),
            "startTime": t0.strftime("%H:%M"),
            "endTime": end_label,
            "source": "staff",
        }
    return None


def fetch_staff_absence_events_api(
    range_start: date, range_end: date, tz: ZoneInfo
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    cal_id = staff_absence_calendar_id()
    if not cal_id:
        return [], "STAFF_ABSENCE_GOOGLE_CALENDAR_ID not set"

    token = _access_token()
    time_min = datetime.combine(range_start, datetime.min.time(), tzinfo=tz).isoformat()
    time_max = datetime.combine(
        range_end + timedelta(days=1), datetime.min.time(), tzinfo=tz
    ).isoformat()
    encoded_id = quote(cal_id, safe="@")
    url = f"https://www.googleapis.com/calendar/v3/calendars/{encoded_id}/events"

    events: List[Dict[str, Any]] = []
    page_token: Optional[str] = None
    try:
        with httpx.Client(timeout=60.0) as client:
            while True:
                params: Dict[str, Any] = {
                    "timeMin": time_min,
                    "timeMax": time_max,
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "maxResults": 2500,
                }
                if page_token:
                    params["pageToken"] = page_token
                resp = client.get(
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
                if resp.status_code == 404:
                    return [], (
                        "Staff absence calendar not found for the service account. "
                        "In Google Calendar, share that calendar with “Make changes to events” "
                        "or at least “See all event details” for the service account email in "
                        "credentials/google/service-account.json (client_email)."
                    )
                if resp.status_code == 403:
                    return [], (
                        "Google Calendar API denied access to the staff absence calendar. "
                        "Share the calendar with the service account and enable the Calendar API "
                        "for your Google Cloud project."
                    )
                resp.raise_for_status()
                payload = resp.json()
                for item in payload.get("items") or []:
                    if item.get("status") == "cancelled":
                        continue
                    ev = _api_item_to_event(item, tz)
                    if ev:
                        events.append(ev)
                page_token = payload.get("nextPageToken")
                if not page_token:
                    break
    except Exception as exc:
        return [], f"Staff absence calendar API: {exc}"

    events.sort(
        key=lambda e: (
            e.get("startDate") or "",
            "" if e.get("allDay") else (e.get("startTime") or ""),
            e.get("title") or "",
        )
    )
    return events, None
