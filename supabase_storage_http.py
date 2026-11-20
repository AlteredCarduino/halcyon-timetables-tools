"""Supabase Storage upload/download compatible with sb_secret_ and legacy JWT keys."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import requests


def _supabase_base_url() -> str:
    return os.environ.get("SUPABASE_URL", "").strip().rstrip("/")


def _service_key() -> str:
    return os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()


def _storage_headers(key: str, *, content_type: Optional[str] = None, upsert: bool = False) -> dict[str, str]:
    headers = {"apikey": key}
    # sb_secret_* keys are not JWTs — Bearer makes Storage return 404 "Bucket not found".
    if key.startswith("eyJ") and key.count(".") >= 2:
        headers["Authorization"] = f"Bearer {key}"
    if content_type:
        headers["Content-Type"] = content_type
    if upsert:
        headers["x-upsert"] = "true"
    return headers


def upload_storage_object(
    bucket: str,
    remote_name: str,
    data: bytes,
    *,
    content_type: str = "application/octet-stream",
) -> None:
    url = _supabase_base_url()
    key = _service_key()
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY required")

    resp = requests.post(
        f"{url}/storage/v1/object/{bucket}/{remote_name}",
        headers=_storage_headers(key, content_type=content_type, upsert=True),
        data=data,
        timeout=600,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(resp.text or f"upload failed ({resp.status_code})")


def download_storage_object(bucket: str, remote_name: str) -> bytes:
    url = _supabase_base_url()
    key = _service_key()
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY required")

    resp = requests.get(
        f"{url}/storage/v1/object/{bucket}/{remote_name}",
        headers=_storage_headers(key),
        timeout=600,
    )
    if resp.status_code != 200:
        raise RuntimeError(resp.text or f"download failed ({resp.status_code})")
    return resp.content


def upload_storage_file(
    bucket: str,
    remote_name: str,
    local_path: Path,
    *,
    content_type: str = "application/octet-stream",
) -> None:
    upload_storage_object(bucket, remote_name, local_path.read_bytes(), content_type=content_type)
