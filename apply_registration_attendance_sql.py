#!/usr/bin/env python3
"""Apply halcyon_registration_attendance_* DDL to Supabase Postgres."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / ".env", override=True)

SQL_PATH = _ROOT / "sql" / "008_registration_attendance_wholeyear.sql"


def main() -> None:
    db_url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not db_url:
        print(
            "Set SUPABASE_DB_URL to your Supabase Postgres connection string\n"
            "(Dashboard → Connect → URI, session mode or direct).\n"
            "Or paste the contents of sql/008_registration_attendance_wholeyear.sql\n"
            "into Supabase → SQL Editor and run it there.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    try:
        import psycopg2
    except ImportError as exc:
        raise SystemExit("pip install psycopg2-binary") from exc

    sql = SQL_PATH.read_text(encoding="utf-8")
    conn = psycopg2.connect(db_url)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql)
    finally:
        conn.close()
    print(f"Applied {SQL_PATH.name}")


if __name__ == "__main__":
    main()
