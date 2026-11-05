"""Twilio webhook signature validation (stdlib only, no twilio lib).

Twilio signs each webhook POST with HMAC-SHA1 over
    url + sorted POST parameters
using the account's Auth Token as the key, base64-encoded, sent as
the X-Twilio-Signature header. This module implements exactly that
check with hashlib/hmac — no new dependency.

Production rule:
- If TWILIO_AUTH_TOKEN is set in the environment, a POST to /voice
  or /sms with a missing or mismatched X-Twilio-Signature is
  rejected with 403 and nothing is logged as a booking. This is
  what stops anyone on the internet from injecting fake bookings
  into a public Render URL.
- If TWILIO_AUTH_TOKEN is NOT set (test mode, no credentials), the
  check passes through with a warning — so the flow still runs
  locally and in tests without any key. Pass-through is test-only
  and must never be relied on in production: the checklist
  (proper-setup-checklist.md) blocks go-live until the token is set.
"""

import base64
import hashlib
import hmac
import logging
import os

log = logging.getLogger("swifexa.security")


def compute_signature(auth_token, url, params):
    """Compute Twilio's expected signature for a URL + POST params."""
    data = url
    for key in sorted(params):
        data += key + params[key]
    digest = hmac.new(auth_token.encode("utf-8"),
                      data.encode("utf-8"),
                      hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


def verify_twilio_signature(url, params, signature):
    """Return (ok, mode).

    mode is "verified" when a token is set and the signature matches,
    "test_mode_passthrough" when no token is set, or "rejected" when
    a token is set and the signature is missing/mismatched.
    """
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    if not auth_token:
        log.warning("TWILIO_AUTH_TOKEN not set: test-mode passthrough, "
                    "do NOT use in production")
        return True, "test_mode_passthrough"
    if not signature:
        return False, "rejected"
    expected = compute_signature(auth_token, url, params)
    ok = hmac.compare_digest(expected, signature)
    return ok, ("verified" if ok else "rejected")
