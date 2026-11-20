"""Serve personal timetables HTML (local static or Supabase Storage after sync)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from halcyon_tools_section import upgrade_tools_section_html

_ROOT = Path(__file__).resolve().parent
_STATIC_DIR = _ROOT / "static" / "personal_timetables"
_REMOTE_NAME = "personal_timetables.html"
_LOCAL_NAME = "personal_timetables.html"


def _bucket() -> str:
    return os.environ.get(
        "SUPABASE_PERSONAL_TIMETABLES_BUCKET",
        os.environ.get("SUPABASE_TIMETABLED_ROOMS_BUCKET", "timetabled-rooms"),
    ).strip()


def _load_local() -> Optional[bytes]:
    path = _STATIC_DIR / _LOCAL_NAME
    if path.is_file():
        return path.read_bytes()
    return None


def _load_storage() -> Optional[bytes]:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        return None
    try:
        from supabase import create_client

        sb = create_client(url, key)
        return sb.storage.from_(_bucket()).download(_REMOTE_NAME)
    except Exception:
        return None


def prepare_personal_timetables_html(html_bytes: bytes) -> bytes:
    text = html_bytes.decode("utf-8")
    text = upgrade_tools_section_html(
        text,
        section_page="personaltimetables",
        page_class="page-personal-timetables",
        content_class="pt-content",
    )
    return text.encode("utf-8")


def load_personal_timetables_html() -> Optional[bytes]:
    content = _load_storage()
    if not content:
        content = _load_local()
    if not content:
        return None
    return prepare_personal_timetables_html(content)
