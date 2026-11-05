"""Swifexa AI booking agent — webhook server (Python stdlib only, no deps).

Proper-setup copy of the prototype. No real calls, texts, or emails are sent from this server
in test mode. Outbound SMS via Twilio is an integration point that is
OFF unless TWILIO_SEND_ENABLED=1 and TWILIO_* credentials are set in the
environment — never paste secrets into this file.

Run:  python3 app.py          (listens on PORT, default 8000)
Test: python3 test_flow.py    (3 scenarios, no server needed)
      curl localhost:8000/bookings

Endpoints:
  GET  /           -> server status + endpoint list
  GET  /health     -> {"ok": true}
  GET  /bookings   -> SQLite booking list
  POST /voice      -> Twilio inbound-call webhook.
                      Accepts Twilio form fields (SpeechResult, CallSid,
                      From) or JSON {"text": ..., "caller": ...}.
                      Returns TwiML: <Say> (Twilio built-in voice) or,
                      when Google TTS is configured, an audio reply is
                      marked in the response — see agent.synthesize_speech.
                      On confirm, the booking is written to the SQLite booking store.
  POST /sms        -> SMS / chat webhook.
                      Accepts Twilio form fields (Body, From) or JSON.
                      Runs the same agent flow; on confirm, returns a
                      confirmation text in the response. In test mode
                      that text is returned, NOT sent to a real phone.
  POST /sms/missed -> Missed-call text-back.
                      JSON {"from": "+1...", "business": "..."}.
                      Returns the text-back message that would be sent
                      within 60 seconds in the live version, and logs a
                      missed_call event. Nothing is sent in test mode.

Twilio webhook pointing (later, live only):
  Voice webhook URL: https://<your-public-host>/voice   (POST)
  SMS webhook URL:   https://<your-public-host>/sms     (POST)
  Use a tunnel (e.g. ngrok) for local testing, or deploy to a host.
  Do NOT point a real Twilio number here until the 3 test scenarios
  pass and the $25/month trial budget is approved (already approved
  Nov 1, 2026 — see README).
"""

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape

import logging

from agent import Session, reply, synthesize_speech
from calendar_stub import write_booking_to_calendar
from security import verify_twilio_signature
from store import load_bookings, save_booking, update_calendar_status

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('swifexa.app')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(BASE_DIR, "bookings.db"))
BUSINESS_NAME = os.environ.get("BUSINESS_NAME", "Swifexa Dental Demo")
PORT = int(os.environ.get("PORT", "8000"))

SESSIONS = {}  # caller/call-id -> Session (in-memory, prototype only)


def get_session(key, caller):
    if key not in SESSIONS:
        SESSIONS[key] = Session(caller=caller, business=BUSINESS_NAME)
    return SESSIONS[key]


def twiml_say(text, gather=False):
    """Twilio built-in voice fallback.

    GOOGLE TTS INTEGRATION POINT: if agent.synthesize_speech(text)
    returns audio bytes (only when GOOGLE_TTS_ENABLED=1 + credentials),
    the live version would serve that audio via <Play> instead of <Say>.
    In test mode synthesize_speech() returns None, so <Say> is used —
    the flow works with no Google account or key.
    """
    audio = synthesize_speech(text)
    if audio is not None:
        # Live path (not active in prototype): store/serve audio, <Play> it.
        # Kept as a marker so the integration point is visible, not hidden.
        body = "<Play>https://example.invalid/tts-audio</Play>"
    else:
        body = f"<Say>{escape(text)}</Say>"
    if gather:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f"<Response><Gather input=\"speech\" action=\"/voice\" "
            f"method=\"POST\">{body}</Gather></Response>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<Response>{body}</Response>"
    )


def twiml_message(text):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<Response><Message>{escape(text)}</Message></Response>"
    )


def send_sms_via_twilio(to_number, text):
    """Send a real SMS via Twilio's Messages API.

    Only runs when TWILIO_SEND_ENABLED=1 and TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER are all set in the
    environment. Otherwise returns {"sent": False, ...} and never
    attempts a network call. A Twilio-side rejection (e.g. toll-free
    verification / A2P registration still pending) is returned as a
    failure, never raised — the booking itself stays confirmed.
    """
    if os.environ.get("TWILIO_SEND_ENABLED") != "1":
        return {"sent": False, "reason": "test_mode_no_credentials"}
    missing = [k for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
                           "TWILIO_PHONE_NUMBER") if not os.environ.get(k)]
    if missing:
        return {"sent": False, "reason": f"missing_env:{','.join(missing)}"}
    if not to_number or to_number == "unknown":
        return {"sent": False, "reason": "no_recipient"}
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    url = (f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json")
    data = urllib.parse.urlencode({
        "From": os.environ["TWILIO_PHONE_NUMBER"],
        "To": to_number,
        "Body": text,
    }).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    auth = base64.b64encode(
        f"{sid}:{os.environ['TWILIO_AUTH_TOKEN']}".encode()).decode()
    req.add_header("Authorization", f"Basic {auth}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:500]
        log.warning("Twilio SMS failed HTTP %s: %s", exc.code, body)
        return {"sent": False, "reason": f"twilio_http_{exc.code}"}
    except Exception as exc:  # network/timeout — booking must not crash
        log.warning("Twilio SMS failed: %s", exc)
        return {"sent": False, "reason": "twilio_request_failed"}
    message_sid = payload.get("sid")
    log.info("Twilio SMS sent sid=%s to=%s", message_sid, to_number)
    return {"sent": True, "sid": message_sid}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep test output clean
        pass

    def _send_json(self, obj, status=200):
        data = json.dumps(obj, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_xml(self, xml):
        data = xml.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        ctype = self.headers.get("Content-Type") or ""
        if "application/json" in ctype:
            try:
                return json.loads(raw.decode() or "{}")
            except json.JSONDecodeError:
                return {}
        # Twilio sends application/x-www-form-urlencoded
        # keep_blank_values=True is critical for signature validation:
        # Twilio signs ALL POST params, including ones with empty values.
        # parse_qs drops blank values by default, which made every real
        # Twilio webhook fail signature check (403, Nov 2) even with the
        # correct Auth Token, while unsigned local tests passed.
        parsed = urllib.parse.parse_qs(raw.decode(), keep_blank_values=True)
        return {k: v[0] for k, v in parsed.items()}

    def do_GET(self):
        if self.path in ("/", "/health"):
            self._send_json({
                "ok": True,
                "service": "swifexa-dental-booking-agent",
                "business": BUSINESS_NAME,
                "mode": os.environ.get("AGENT_MODE", "test_mode_no_credentials"),
                "endpoints": ["GET /bookings", "POST /voice",
                              "POST /sms", "POST /sms/missed"],
            })
        elif self.path == "/bookings":
            self._send_json({"bookings": load_bookings(DB_PATH)})
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        body = self._read_body()
        path = self.path.split("?")[0]

        # PROPER SETUP: Twilio webhook signature validation.
        # With TWILIO_AUTH_TOKEN set, a missing/mismatched signature
        # is rejected (403) before any booking is logged. Without a
        # token, security.verify_twilio_signature passes through in
        # test mode only — see security.py. Twilio sends form params,
        # so the signature is computed over those params.
        if path in ("/voice", "/sms", "/sms/missed"):
            host = self.headers.get("Host") or "localhost"
            scheme = "https" if os.environ.get("AGENT_MODE") == "production" else "http"
            full_url = f"{scheme}://{host}{self.path}"
            form_params = {k: v for k, v in body.items()
                           if isinstance(v, str)}
            ok, mode = verify_twilio_signature(
                full_url, form_params,
                self.headers.get("X-Twilio-Signature"))
            if not ok:
                log.warning("Rejected webhook: bad Twilio signature, path=%s", path)
                self._send_json({"error": "invalid Twilio signature"}, status=403)
                return
            if mode == "test_mode_passthrough":
                log.warning("Webhook accepted in test mode (no TWILIO_AUTH_TOKEN)")

        if path == "/sms/missed":
            caller = body.get("from") or body.get("From") or "unknown"
            text_back = (
                f"Hi, this is {BUSINESS_NAME} — sorry we missed your call. "
                "Reply with what you need (e.g. cleaning, tooth pain, implant consultation) "
                "and a time window, and we'll book it for you."
            )
            sms_result = send_sms_via_twilio(caller, text_back)
            save_booking(DB_PATH, {
                "caller": caller, "name": None, "service": None,
                "urgency": None, "address": None, "time_window": None,
                "status": ("missed_call_text_back_sent" if sms_result.get("sent")
                           else "missed_call_text_back_failed"),
                "fallback_reason": None,
                "logged_at": datetime.now(timezone.utc).isoformat(),
            })
            self._send_json({"text_back": text_back, **sms_result})
            return

        if path not in ("/voice", "/sms"):
            self._send_json({"error": "not found"}, status=404)
            return

        # Twilio form fields first, JSON fields second.
        user_text = (body.get("SpeechResult") or body.get("Body")
                     or body.get("text") or "")
        caller = body.get("From") or body.get("from") or "unknown"
        call_id = body.get("CallSid") or body.get("call_id") or caller
        session_key = f"{caller}:{call_id}" if self.path == "/voice" else caller
        session = get_session(session_key, caller)

        agent_text, finished = reply(session, user_text)

        if finished and session.status == "confirmed":
            saved = save_booking(DB_PATH, session.to_booking())
            cal = write_booking_to_calendar(saved)
            if saved.get("id") is not None:
                update_calendar_status(
                    saved["id"],
                    "written" if cal.get("written") else "not_written:" + str(cal.get("reason")),
                    DB_PATH)
            log.info("Booking confirmed id=%s calendar=%s", saved.get("id"), cal)
            # Confirmation response (SMS integration point, test mode).
            confirm_text = (
                f"Confirmed: {session.service} at {session.address}, "
                f"{session.time_window}. {BUSINESS_NAME} will confirm "
                "the exact arrival time with you."
            )
            sms_result = send_sms_via_twilio(caller, confirm_text)
            log.info("Booking id=%s confirmation SMS: %s",
                     saved.get("id"), sms_result)
        elif finished and session.status == "fallback_to_owner":
            save_booking(DB_PATH, session.to_booking())

        if path == "/voice":
            # Gather again unless the call is finished.
            self._send_xml(twiml_say(agent_text, gather=not finished))
        else:
            # SMS clients: JSON is easier to test than TwiML; also
            # accept ?format=twiml for a real Twilio SMS webhook.
            if "format=twiml" in self.path or body.get("format") == "twiml":
                self._send_xml(twiml_message(agent_text))
            else:
                self._send_json({"reply": agent_text,
                                 "stage": session.stage,
                                 "status": session.status,
                                 "finished": finished})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Swifexa booking agent listening on http://localhost:{PORT} "
          f"(business: {BUSINESS_NAME}, test mode, no credentials)")
    print("Endpoints: GET /bookings | POST /voice | POST /sms | "
          "POST /sms/missed")
    server.serve_forever()
