"""Serve registration attendance HTML (Supabase cache, local static, or Storage)."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent
_STATIC_DIR = _ROOT / "static" / "registration_wholeyear"
_REMOTE_NAME = "registration_wholeyear_attendance.html"
_LOCAL_NAME = "attendance.html"

_log = logging.getLogger(__name__)


def _bucket() -> str:
    return os.environ.get(
        "SUPABASE_REGISTRATION_WHOLEYEAR_BUCKET",
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
        from supabase_storage_http import download_storage_object

        return download_storage_object(_bucket(), _REMOTE_NAME)
    except Exception as exc:
        _log.debug("registration wholeyear storage load failed: %s", exc)
        return None


def _load_cached_html() -> Optional[bytes]:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        return None
    try:
        from registration_attendance_db import load_html_cache, require_supabase, school_key

        sb = require_supabase()
        return load_html_cache(sb, school_key())
    except Exception as exc:
        _log.warning("registration wholeyear html cache load failed: %s", exc)
        return None


def load_registration_wholeyear_html() -> Optional[bytes]:
    """Prefer pre-built Supabase cache; never build ~50MB HTML during a web request."""
    for loader in (_load_cached_html, _load_storage, _load_local):
        content = loader()
        if content:
            return content
    return None


def load_registration_wholeyear_html_diagnostics() -> tuple[Optional[bytes], str]:
    checks = [
        ("supabase_html_cache", _load_cached_html),
        ("supabase_storage", _load_storage),
        ("local_static", _load_local),
    ]
    notes: list[str] = []
    for name, loader in checks:
        try:
            content = loader()
        except Exception as exc:
            notes.append(f"{name}: error ({exc})")
            continue
        if content:
            notes.append(f"{name}: ok ({len(content)} bytes)")
            return content, "; ".join(notes)
        notes.append(f"{name}: missing")
    return None, "; ".join(notes)
