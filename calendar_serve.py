"""School calendar page — embed Google Sheet in Halcyon shell (no server-side Sheets API on page load)."""

from __future__ import annotations

import os
from pathlib import Path

from halcyon_tools_section import upgrade_tools_section_html

_ROOT = Path(__file__).resolve().parent
_CALENDAR_HTML = _ROOT / "CalendarView.html"

DEFAULT_SHEET_ID = "1bzBjTADwueeaHEaYdGU7yZHcKh2IrUaNLXdAcKF0tV4"
DEFAULT_WORKSHEET_GID = "1541006323"
DEFAULT_TITLE = "Halcyon School Calendar 2026/27"
DEFAULT_EMBED_ZOOM = "0.75"


def calendar_embed_zoom() -> str:
    raw = os.environ.get("SCHOOL_CALENDAR_EMBED_ZOOM", DEFAULT_EMBED_ZOOM).strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_EMBED_ZOOM
    if value <= 0 or value > 1:
        return DEFAULT_EMBED_ZOOM
    return format(value, "g")


def calendar_embed_url() -> str:
    override = os.environ.get("SCHOOL_CALENDAR_EMBED_URL", "").strip()
    if override:
        return override
    sheet_id = os.environ.get("SCHOOL_CALENDAR_SHEET_ID", DEFAULT_SHEET_ID).strip()
    gid = os.environ.get("SCHOOL_CALENDAR_WORKSHEET_GID", DEFAULT_WORKSHEET_GID).strip()
    return (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
        f"?gid={gid}&rm=embedded&single=true&widget=false"
    )


def calendar_title() -> str:
    return os.environ.get("SCHOOL_CALENDAR_TITLE", DEFAULT_TITLE).strip() or DEFAULT_TITLE


def load_calendar_html() -> bytes:
    if not _CALENDAR_HTML.is_file():
        raise FileNotFoundError("CalendarView.html missing")
    html = _CALENDAR_HTML.read_text(encoding="utf-8")
    html = html.replace("__CALENDAR_EMBED_URL__", calendar_embed_url())
    html = html.replace("__CALENDAR_TITLE__", calendar_title())
    html = html.replace("__CALENDAR_EMBED_ZOOM__", calendar_embed_zoom())
    html = upgrade_tools_section_html(
        html,
        section_page="calendar",
        page_class="page-calendar",
        content_class="cal-content",
    )
    return html.encode("utf-8")
