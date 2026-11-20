"""Serve timetabled rooms HTML (Supabase Storage, local static) + room booking assets."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from halcyon_tools_section import upgrade_tools_section_html

_ROOT = Path(__file__).resolve().parent
_STATIC_DIR = _ROOT / "static" / "timetabled_rooms"
_REMOTE_NAME = "timetabled_rooms.html"
_LOCAL_NAME = "timetabled_rooms.html"
_BOOKING_MARKER = "room-bookings.js"

_FILES = {
    "day": ("timetabled_rooms.html", "timetabled_rooms.html"),
    "all-days": ("timetabled_rooms_all_days.html", "timetabled_rooms_all_days.html"),
}


def _bucket() -> str:
    return os.environ.get("SUPABASE_TIMETABLED_ROOMS_BUCKET", "timetabled-rooms").strip()


def _load_local(name: str) -> Optional[bytes]:
    path = _STATIC_DIR / _LOCAL_NAME if name == _LOCAL_NAME else _STATIC_DIR / name
    if path.is_file():
        return path.read_bytes()
    return None


def _load_storage(name: str) -> Optional[bytes]:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        return None
    try:
        from supabase_storage_http import download_storage_object

        return download_storage_object(_bucket(), name)
    except Exception:
        return None


def inject_room_booking_assets(text: str) -> str:
    if _BOOKING_MARKER in text:
        return text
    injection = (
        '<link rel="stylesheet" href="/static/timetabled_rooms/room-bookings.css?v=17" />\n'
        '<script src="/static/timetabled_rooms/room-bookings.js?v=17"></script>\n'
    )
    if "</body>" in text:
        return text.replace("</body>", injection + "</body>", 1)
    return text + injection


def prepare_timetabled_rooms_html(html_bytes: bytes) -> bytes:
    text = html_bytes.decode("utf-8")
    text = upgrade_tools_section_html(
        text,
        section_page="timetabledrooms",
        page_class="page-timetabled-rooms",
        content_class="tr-content",
    )
    text = inject_room_booking_assets(text)
    return text.encode("utf-8")


def load_timetabled_rooms_html(view: str) -> Optional[bytes]:
    """view: 'day' | 'all-days'. Prefers Supabase Storage, then bundled static."""
    if view not in _FILES:
        return None
    local_name, remote_name = _FILES[view]
    content = _load_storage(remote_name)
    if not content:
        content = _load_local(local_name)
    if not content:
        return None
    return prepare_timetabled_rooms_html(content)
