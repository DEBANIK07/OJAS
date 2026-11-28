"""
OJAS Database — vendor quote persistence via psycopg2 (Postgres / Neon / Supabase).

Connection is established lazily on first use and reused across requests.
Set DATABASE_URL in the environment (or .env):
  DATABASE_URL=postgresql://user:password@host:5432/dbname

If DATABASE_URL is not set the module degrades gracefully: quote submissions
still return a tracking ID but are not persisted.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("ojas.database")

_conn = None   # module-level connection (re-opened if closed)


def _get_conn():
    """Return the shared connection, opening it if necessary."""
    global _conn
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        return None
    try:
        import psycopg2
        import psycopg2.extras
        if _conn is None or _conn.closed:
            _conn = psycopg2.connect(database_url, connect_timeout=10)
            _conn.autocommit = True
            logger.info("Connected to Postgres")
        return _conn
    except Exception as exc:
        logger.warning("Postgres connection failed: %s", exc)
        return None


def ensure_schema() -> None:
    """Create the quote_requests table if it does not exist yet."""
    conn = _get_conn()
    if conn is None:
        logger.info("DATABASE_URL not set — quote persistence disabled")
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS quote_requests (
                    id              SERIAL PRIMARY KEY,
                    tracking_id     TEXT NOT NULL UNIQUE,
                    vendor_name     TEXT NOT NULL,
                    customer_name   TEXT NOT NULL,
                    phone           TEXT NOT NULL,
                    latitude        DOUBLE PRECISION,
                    longitude       DOUBLE PRECISION,
                    system_capacity_kw DOUBLE PRECISION,
                    notes           TEXT,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
        logger.info("Schema verified / created")
    except Exception as exc:
        logger.warning("Schema creation failed: %s", exc)


def save_quote(tracking_id: str, vendor_name: str, customer_name: str,
               phone: str, latitude: Optional[float], longitude: Optional[float],
               system_capacity_kw: Optional[float], notes: Optional[str]) -> bool:
    """
    Insert a quote request row.
    Returns True on success, False if the DB is unavailable (caller should still
    return a tracking ID to the user so the experience is not broken).
    """
    conn = _get_conn()
    if conn is None:
        logger.warning("Quote %s not persisted — no DB connection", tracking_id)
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO quote_requests
                    (tracking_id, vendor_name, customer_name, phone,
                     latitude, longitude, system_capacity_kw, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (tracking_id, vendor_name, customer_name, phone,
                 latitude, longitude, system_capacity_kw, notes),
            )
        logger.info("Saved quote %s", tracking_id)
        return True
    except Exception as exc:
        logger.warning("Failed to save quote %s: %s", tracking_id, exc)
        return False
