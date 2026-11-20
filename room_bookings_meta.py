"""Room list helpers for booking admin + APIs."""

from __future__ import annotations

import csv
from pathlib import Path

from timetabled_rooms_config import EXTRA_GRID_ROOMS, RESTRICTED_ROOMS

_STATIC = Path(__file__).resolve().parent / "static" / "timetabled_rooms"
_CSV = _STATIC / "timetabled_rooms.csv"


def list_bookable_rooms() -> list[str]:
    rooms: set[str] = set(EXTRA_GRID_ROOMS) | set(RESTRICTED_ROOMS)
    if _CSV.is_file():
        with _CSV.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                name = (row.get("Room") or "").strip()
                if name:
                    rooms.add(name)
    return sorted(rooms, key=str.casefold)
