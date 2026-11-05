"""Proper booking store: SQLite, not a resettable JSON file.

Production change vs the test prototype (agent-flow/bookings.json):
- Bookings persist in a SQLite database file (default bookings.db).
- Same fields as the prototype, plus an auto-increment id and a
  calendar_status column so the calendar-write result is recorded
  next to the booking instead of being lost.
- SQLite is stdlib-only, so this still needs no pip install. For
  multi-process / multi-instance Render deployments, swap the
  DATABASE_URL for Postgres and replace the sqlite3 calls with the
  same table shape — the column names below are the contract.
  Do NOT put credentials in this file; the path comes from env only.
"""

import os
import sqlite3

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "bookings.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    caller TEXT,
    name TEXT,
    service TEXT,
    urgency TEXT,
    address TEXT,
    time_window TEXT,
    status TEXT,
    fallback_reason TEXT,
    calendar_status TEXT DEFAULT 'not_attempted',
    logged_at TEXT
);
"""


def _connect(path=None):
    db_path = path or os.environ.get("DATABASE_PATH", DEFAULT_DB)
    conn = sqlite3.connect(db_path)
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def save_booking(path, booking):
    """Save one booking dict. `path` is the DB path (or None for default).

    Returns the booking with its database id attached.
    """
    conn = _connect(path)
    try:
        cur = conn.execute(
            "INSERT INTO bookings (caller, name, service, urgency, address,"
            " time_window, status, fallback_reason, calendar_status,"
            " logged_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (booking.get("caller"), booking.get("name"),
             booking.get("service"), booking.get("urgency"),
             booking.get("address"), booking.get("time_window"),
             booking.get("status"), booking.get("fallback_reason"),
             booking.get("calendar_status", "not_attempted"),
             booking.get("logged_at")),
        )
        conn.commit()
        saved = dict(booking)
        saved["id"] = cur.lastrowid
        return saved
    finally:
        conn.close()


def load_bookings(path=None):
    """Return all bookings, oldest first, as dicts."""
    conn = _connect(path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM bookings ORDER BY id ASC").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def update_calendar_status(booking_id, calendar_status, path=None):
    conn = _connect(path)
    try:
        conn.execute(
            "UPDATE bookings SET calendar_status = ? WHERE id = ?",
            (calendar_status, booking_id))
        conn.commit()
    finally:
        conn.close()
