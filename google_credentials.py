"""Shared Google service account credentials (Sheets, Calendar, etc.)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

_APP_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _APP_ROOT.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def default_service_account_paths() -> List[Path]:
    return [
        _REPO_ROOT / "credentials" / "google" / "service-account.json",
        _APP_ROOT / "credentials" / "google" / "service-account.json",
    ]


def resolve_service_account_path(path: str) -> Path:
    p = Path(path).expanduser()
    if p.is_absolute():
        return p.resolve()
    for base in (_REPO_ROOT, _APP_ROOT):
        candidate = (base / p).resolve()
        if candidate.is_file():
            return candidate
    return (_REPO_ROOT / p).resolve()


def google_credentials_available() -> bool:
    sa_file = _env("GOOGLE_APPLICATION_CREDENTIALS") or _env("GOOGLE_SERVICE_ACCOUNT_FILE")
    if sa_file and resolve_service_account_path(sa_file).is_file():
        return True
    if _env("GOOGLE_SERVICE_ACCOUNT_JSON"):
        return True
    return any(p.is_file() for p in default_service_account_paths())


def load_google_service_account_credentials(scopes: List[str]):
    from google.oauth2.service_account import Credentials as ServiceAccountCredentials

    sa_file = _env("GOOGLE_APPLICATION_CREDENTIALS") or _env("GOOGLE_SERVICE_ACCOUNT_FILE")
    sa_json = _env("GOOGLE_SERVICE_ACCOUNT_JSON")

    if sa_file:
        sa_path = resolve_service_account_path(sa_file)
        if not sa_path.is_file():
            raise RuntimeError(f"Google service account file not found: {sa_path}")
        return ServiceAccountCredentials.from_service_account_file(str(sa_path), scopes=scopes)
    if sa_json:
        return ServiceAccountCredentials.from_service_account_info(json.loads(sa_json), scopes=scopes)

    for default_path in default_service_account_paths():
        if default_path.is_file():
            return ServiceAccountCredentials.from_service_account_file(str(default_path), scopes=scopes)

    paths = ", ".join(str(p) for p in default_service_account_paths())
    raise RuntimeError(
        "Google credentials not configured. Set GOOGLE_APPLICATION_CREDENTIALS, "
        "GOOGLE_SERVICE_ACCOUNT_FILE, or GOOGLE_SERVICE_ACCOUNT_JSON, or place "
        f"service-account.json at one of: {paths}"
    )
