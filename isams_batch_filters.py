"""
iSAMS Batch API filtered requests (CoverManager + Registration blocks).

Halcyon uses two batch apiKeys:

- ``ISAMS_BATCH_API_KEY`` — OAuth client id; CoverManager + RegistrationManager
  (requires filtered XML body below).
- ``ISAMS_BATCH_REFERENCE_API_KEY`` — legacy key; TimetableManager, PupilManager,
  timetabled rooms, personal timetables (unfiltered POST).

Filtered body for the registration key:

  <Filters>
    <CoverManager>
      <AllLessonSuspensions StartDate="..." EndDate="..." />
      <AllRoomClosuresAndCover StartDate="..." EndDate="..." />
    </CoverManager>
    <Registration>
      <OutOfSchoolPupils StartDate="..." EndDate="..." />
      <RegistrationStatus StartDate="..." EndDate="..." />
      <RegistrationPeriods StartDate="..." EndDate="..." />
    </Registration>
  </Filters>

That key requires all of the above filters. Older batch keys may only return
reference datasets unless Registration Periods is enabled separately.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Literal, Optional
from xml.sax.saxutils import escape

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    _ENV_PATH = Path(__file__).resolve().parent / ".env"
    if _ENV_PATH.is_file():
        load_dotenv(_ENV_PATH, override=True)

FilterStyle = Literal["auto", "full", "minimal"]


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def batch_url() -> str:
    return (
        _env("ISAMS_BATCH_API_URL")
        or _env("ISAMS_BATCH_URL")
        or "https://halcyonschool.isams.cloud/api/batch/1.0/json.ashx"
    )


def batch_key() -> str:
    """OAuth batch key for CoverManager + RegistrationManager (filtered XML body)."""
    key = _env("ISAMS_BATCH_API_KEY")
    if not key:
        raise RuntimeError("set ISAMS_BATCH_API_KEY in signInApp_server/.env")
    return key


def batch_reference_key() -> str:
    """Legacy batch key for TimetableManager, PupilManager, and other unfiltered datasets."""
    key = _env("ISAMS_BATCH_REFERENCE_API_KEY") or _env("ISAMS_BATCH_LEGACY_API_KEY")
    if not key:
        raise RuntimeError("set ISAMS_BATCH_REFERENCE_API_KEY in signInApp_server/.env")
    return key


_requires_full_filters: Optional[bool] = None


def batch_key_requires_full_filters() -> bool:
    """
    Some batch keys (e.g. OAuth client id UUID) reject unfiltered POST with 400.
    Detect once per process so auto mode picks the full Cover+Registration body.
    """
    global _requires_full_filters
    if _requires_full_filters is not None:
        return _requires_full_filters

    explicit = _env("ISAMS_BATCH_FILTER_STYLE").lower()
    if explicit == "full":
        _requires_full_filters = True
        return True
    if explicit == "minimal":
        _requires_full_filters = False
        return False

    oauth_client_id = _env("ISAMS_OAUTH_CLIENT_ID")
    key = batch_key()
    if oauth_client_id and key == oauth_client_id:
        _requires_full_filters = True
        return True

    client_id = _env("ISAMS_CLIENT_ID")
    if client_id and key == client_id:
        _requires_full_filters = True
        return True

    try:
        resp = requests.post(batch_url(), params={"apiKey": key}, timeout=60)
        _requires_full_filters = resp.status_code == 400
    except requests.RequestException:
        _requires_full_filters = False
    return _requires_full_filters


def resolve_filter_style(style: FilterStyle = "auto") -> Literal["full", "minimal"]:
    if style == "full":
        return "full"
    if style == "minimal":
        return "minimal"
    configured = _env("ISAMS_BATCH_FILTER_STYLE").lower()
    if configured == "full":
        return "full"
    if configured == "minimal":
        return "minimal"
    if batch_key_requires_full_filters():
        return "full"
    return "minimal"


def build_filters_xml(
    start_date: str,
    end_date: str,
    *,
    style: FilterStyle = "auto",
    include_registration_periods: bool = True,
    include_registration_status: bool = False,
    include_out_of_school: bool = False,
) -> str:
    """
    Build batch filter XML for a date range.

    ``full`` style sends CoverManager + all Registration filters (required when
    ISAMS_BATCH_API_KEY is the OAuth client id). ``minimal`` sends only the
    Registration elements requested.
    """
    start_date = escape(start_date.strip())
    end_date = escape(end_date.strip())
    resolved = resolve_filter_style(style)

    if resolved == "full":
        return f"""<?xml version="1.0" encoding="utf-8" ?>
<Filters>
    <CoverManager>
        <AllLessonSuspensions StartDate="{start_date}" EndDate="{end_date}" />
        <AllRoomClosuresAndCover StartDate="{start_date}" EndDate="{end_date}" />
    </CoverManager>
    <Registration>
        <OutOfSchoolPupils StartDate="{start_date}" EndDate="{end_date}" />
        <RegistrationStatus StartDate="{start_date}" EndDate="{end_date}" />
        <RegistrationPeriods StartDate="{start_date}" EndDate="{end_date}" />
    </Registration>
</Filters>"""

    registration_lines = []
    if include_out_of_school:
        registration_lines.append(
            f'        <OutOfSchoolPupils StartDate="{start_date}" EndDate="{end_date}" />'
        )
    if include_registration_status:
        registration_lines.append(
            f'        <RegistrationStatus StartDate="{start_date}" EndDate="{end_date}" />'
        )
    if include_registration_periods:
        registration_lines.append(
            f'        <RegistrationPeriods StartDate="{start_date}" EndDate="{end_date}" />'
        )
    if not registration_lines:
        raise ValueError("At least one Registration filter must be requested")

    registration_body = "\n".join(registration_lines)
    return f"""<?xml version="1.0" encoding="utf-8" ?>
<Filters>
    <Registration>
{registration_body}
    </Registration>
</Filters>"""


def batch_post_filtered(
    xml_body: str,
    *,
    method: Literal["post", "get"] = "post",
    timeout: int = 180,
) -> Dict[str, Any]:
    """POST (default) or GET with XML filter body and apiKey query param."""
    url = batch_url()
    params = {"apiKey": batch_key()}
    headers = {"Content-Type": "application/xml"}
    request_method = requests.post if method == "post" else requests.get
    resp = request_method(
        url,
        params=params,
        headers=headers,
        data=xml_body,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def registration_manager_keys(data: Dict[str, Any]) -> list[str]:
    rm = data.get("iSAMS", {}).get("RegistrationManager")
    if isinstance(rm, dict):
        return sorted(rm.keys())
    return []
