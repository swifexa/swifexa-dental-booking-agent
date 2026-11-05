"""Calendar-write stub: interface + log, no real calendar call.

Production rule: a confirmed booking must land in a real calendar/CRM
(Google Calendar for the pilot business), not only in the local store.
This stub defines that interface so the server can call it today
without any credentials, and so the real implementation replaces
exactly one function later.

Behaviour:
- With no CALENDAR_ENABLED=1 and no calendar credentials set, it
  returns {"written": False, "reason": "no_calendar_credentials"}
  and does NOT call Google, and does NOT invent a calendar event.
- With CALENDAR_ENABLED=1, a real implementation would use the
  configured calendar credentials (via env / secure vault only, never
  in code or chat) to create an event with: service, address,
  time_window, caller, urgency — then return {"written": True,
  "event_id": "<real id>"}.
- The caller (app.py) records the result in the booking's
  calendar_status column, so an unwritten booking is visible in
  GET /bookings instead of silently lost.
"""

import os


def write_booking_to_calendar(booking):
    """Attempt a calendar write. Safe with no credentials: no-op."""
    if os.environ.get("CALENDAR_ENABLED") != "1":
        return {"written": False,
                "reason": "no_calendar_credentials"}
    missing = [k for k in ("CALENDAR_ID",) if not os.environ.get(k)]
    if missing:
        return {"written": False,
                "reason": "missing_env:" + ",".join(missing)}
    # Real Google Calendar API call goes here (service-account /
    # OAuth credentials from env / secure vault only).
    # Deliberately not implemented in this build: no credential is
    # ever read, sent, or charged here.
    return {"written": False,
            "reason": "calendar_not_implemented_in_proper_setup"}
