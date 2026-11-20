"""Shared Google Sheets helpers (e.g. local student logs). Visitor In uses Supabase only."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import gspread

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _default_service_account_path() -> Path:
    return _REPO_ROOT / "credentials" / "google" / "service-account.json"


def _resolve_service_account_path(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = _REPO_ROOT / p
    return p.resolve()


def _google_credentials_available() -> bool:
    sa_file = (
        _env("GOOGLE_APPLICATION_CREDENTIALS")
        or _env("GOOGLE_SERVICE_ACCOUNT_FILE")
    )
    if sa_file and _resolve_service_account_path(sa_file).is_file():
        return True
    if _env("GOOGLE_SERVICE_ACCOUNT_JSON"):
        return True
    return _default_service_account_path().is_file()


def _open_spreadsheet():
    sheet_id = _env("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise RuntimeError("GOOGLE_SHEET_ID is not set.")

    if not _google_credentials_available():
        raise RuntimeError(
            "Google credentials not configured. Set GOOGLE_SERVICE_ACCOUNT_JSON or place "
            f"service-account.json at {_default_service_account_path()}"
        )

    from google.oauth2.service_account import Credentials as ServiceAccountCredentials

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    sa_file = (
        _env("GOOGLE_APPLICATION_CREDENTIALS")
        or _env("GOOGLE_SERVICE_ACCOUNT_FILE")
    )
    sa_path: Optional[Path] = None
    if sa_file:
        sa_path = _resolve_service_account_path(sa_file)
    else:
        default_path = _default_service_account_path()
        if default_path.is_file():
            sa_path = default_path
    sa_json = _env("GOOGLE_SERVICE_ACCOUNT_JSON")

    if sa_path is not None:
        if not sa_path.is_file():
            raise RuntimeError(f"Google service account file not found: {sa_path}")
        creds = ServiceAccountCredentials.from_service_account_file(str(sa_path), scopes=scopes)
    elif sa_json:
        creds = ServiceAccountCredentials.from_service_account_info(json.loads(sa_json), scopes=scopes)
    else:
        raise RuntimeError("Google credentials not configured.")

    gc = gspread.authorize(creds)
    return gc.open_by_key(sheet_id)


def _find_worksheet(spreadsheet, worksheet_name: str):
    try:
        return spreadsheet.worksheet(worksheet_name)
    except gspread.exceptions.WorksheetNotFound:
        pass
    want = worksheet_name.strip().lower()
    for ws in spreadsheet.worksheets():
        if ws.title.strip().lower() == want:
            return ws
    titles = [w.title for w in spreadsheet.worksheets()]
    raise RuntimeError(f"Worksheet {worksheet_name!r} not found. Available tabs: {titles}")
