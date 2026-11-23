#!/usr/bin/env python3
"""Apply halcyon_room_bookings DDL to Supabase Postgres."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / ".env", override=True)

SQL_FILES = (
    _ROOT / "sql" / "010_halcyon_room_bookings.sql",
    _ROOT / "sql" / "011_halcyon_room_bookings_period_order.sql",
    _ROOT / "sql" / "012_halcyon_room_blocks.sql",
    _ROOT / "sql" / "013_halcyon_room_bookings_series.sql",
)


def main() -> None:
    db_url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not db_url:
        print(
            "Set SUPABASE_DB_URL to your Supabase Postgres connection string\n"
            "(Dashboard → Connect → URI, session mode or direct).\n"
            "Or paste sql/011_halcyon_room_bookings_period_order.sql into\n"
            "Supabase → SQL Editor and run it there.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    try:
        import psycopg2
    except ImportError as exc:
        raise SystemExit("pip install psycopg2-binary") from exc

    conn = psycopg2.connect(db_url)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            for sql_path in SQL_FILES:
                cur.execute(sql_path.read_text(encoding="utf-8"))
                print(f"Applied {sql_path.name}")
            cur.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'halcyon_room_bookings'
                ORDER BY ordinal_position
                """
            )
            cols = [row[0] for row in cur.fetchall()]
            print("Columns:", ", ".join(cols))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
