"""Swifexa DENTAL booking agent — separate variant, do NOT overwrite HVAC demo.
Test-data demo only. No real patient calls until a BAA is signed.
Hard rules: never quote a price, never promise candidacy, never diagnose,
never promise same-day treatment, never collect insurance policy numbers.
Implant = CONSULTATION only, office confirms plan/cost in person.
SMS only after verbal yes, STOP/HELP in every SMS. No audio recording by default.
Flow: greeting (new/returning) -> reason -> urgency -> patient details
(name, insurance carrier name only) -> time -> confirm -> done.
"""
import os, re
from datetime import datetime, timezone
SYSTEM_PROMPT = "You are the booking assistant for a dental practice. Talk naturally, ask one question at a time. Never quote a price, never diagnose, never promise candidacy or treatment. Implant bookings are consultations only — the office confirms the plan and cost in person. If price or availability is unknown, say you will check with the office."
NO_GUESS_PRICE = "I don't have a confirmed price for that yet, so I won't guess — I'll check with the office and let you know as soon as they confirm."
NO_GUESS_TIME = "I can't confirm an exact time yet — I'll check with the office and send you a confirmation message once it's set."
REASON_KEYWORDS = [
    (["implant"], "implant consultation"),
    (["cleaning", "checkup", "check-up", "hygiene"], "cleaning / checkup"),
    (["tooth pain", "toothache", "pain", "hurts", "aching"], "tooth pain"),
    (["broken tooth", "broken filling", "chipped", "cracked tooth"], "broken tooth / filling"),
    (["crown"], "crown"),
    (["whitening", "whiten"], "whitening"),
    (["braces", "invisalign", "aligner"], "braces / Invisalign"),
    (["reschedule", "cancel", "move my appointment"], "reschedule / cancel"),
]
FALLBACK_PATTERNS = [
    (r"\b(human|person|real person|manager|owner|receptionist)\b", "caller asked for a human"),
    (r"\b(angry|refund|complaint|terrible|awful|scam|lawyer)\b", "caller angry / refund / complaint"),
    (r"\b(diagnos|is it infected|do i need a root canal|what is wrong with my tooth)\b", "question the agent cannot answer (diagnosis)"),
]
PRICE_PATTERNS = r"\b(price|cost|how much|quote|discount|charge)\b"
TIME_PATTERNS = r"\b(exact.*time|what time will|when will.*arrive|guarantee)\b"
def classify_reason(text):
    low = text.lower()
    for keywords, label in REASON_KEYWORDS:
        if any(k in low for k in keywords):
            return label
    return "general dental visit"
def detect_fallback(text):
    low = text.lower()
    for pattern, reason in FALLBACK_PATTERNS:
        if re.search(pattern, low):
            return reason
    return None
def is_yes(text):
    return bool(re.search(r"\b(yes|yeah|yep|correct|that's right|right)\b", text.lower()))
def is_no(text):
    return bool(re.search(r"\b(no|not right|wrong|incorrect)\b", text.lower()))
def detect_urgency(text):
    low = text.lower()
    if re.search(r"\b(pain|swelling|bleeding|broken|trauma|emergency|urgent|swollen)\b", low):
        return "urgent"
    if re.search(r"\b(routine|not urgent|can wait|next week|whenever)\b", low):
        return "routine"
    return None
class Session:
    def __init__(self, caller, business):
        self.caller = caller; self.business = business; self.stage = "greeting"
        self.service = None; self.urgency = None; self.name = None
        self.insurance_carrier = None; self.time_window = None
        self.status = "in_progress"; self.fallback_reason = None; self.transcript = []
        self.address = None
    def to_booking(self):
        return {"caller": self.caller, "name": self.name, "service": self.service, "urgency": self.urgency, "insurance_carrier": self.insurance_carrier, "time_window": self.time_window, "status": self.status, "fallback_reason": self.fallback_reason, "logged_at": datetime.now(timezone.utc).isoformat()}
def llm_reply(system_prompt, transcript, user_text):
    if not os.environ.get("LLM_API_KEY"):
        return None
    return None
def _stage_prompt(session):
    prompts = {"greeting": "are you a new or returning patient, and what do you need help with today?", "reason": f"so that's {session.service or 'the visit'}. Did I get that right?", "urgency": "is there pain, swelling, bleeding, a broken tooth, or trauma right now, or can this wait for a scheduled visit?", "details": "may I have your name, and your insurance carrier name only, if you have one?", "time": "what day and time window works best?", "confirm": "is that correct?"}
    return prompts.get(session.stage, "how can I help?")
def reply(session, user_text):
    text = (user_text or "").strip()
    session.transcript.append({"from": "caller", "text": text})
    if re.search(PRICE_PATTERNS, text.lower()) and session.stage not in ("confirm",):
        agent_text = NO_GUESS_PRICE + " Now, " + _stage_prompt(session)
        session.transcript.append({"from": "agent", "text": agent_text}); return agent_text, False
    if re.search(TIME_PATTERNS, text.lower()):
        agent_text = NO_GUESS_TIME + " " + _stage_prompt(session)
        session.transcript.append({"from": "agent", "text": agent_text}); return agent_text, False
    fallback = detect_fallback(text) if text else None
    if fallback and session.stage != "done":
        session.status = "fallback_to_owner"; session.fallback_reason = fallback
        agent_text = "That's one for the office — I'll pass your details on and they'll get back to you. Can I take your name and number?"
        session.transcript.append({"from": "agent", "text": agent_text}); return agent_text, False
    llm_text = llm_reply(SYSTEM_PROMPT, session.transcript, text)
    if llm_text:
        session.transcript.append({"from": "agent", "text": llm_text}); return llm_text, False
    if session.stage == "greeting":
        if not text:
            agent_text = f"Thanks for calling {session.business}, this is the booking assistant. Are you a new or returning patient, and what do you need help with today?"
        else:
            session.service = classify_reason(text); session.stage = "reason"
            agent_text = f"Got it — so that's {session.service}. Did I get that right?"
            if session.service == "implant consultation":
                agent_text += " I can book an implant consultation — the office will confirm the plan and cost in person."
        session.transcript.append({"from": "agent", "text": agent_text}); return agent_text, False
    if session.stage == "reason":
        if is_yes(text):
            session.stage = "urgency"; agent_text = "Quick check — is there pain, swelling, bleeding, a broken tooth, or trauma right now, or can this wait for a scheduled visit?"
        elif is_no(text):
            session.stage = "greeting"; agent_text = "Sorry about that — what do you need help with today?"
        else:
            session.service = classify_reason(text); agent_text = f"Got it — so that's {session.service}. Did I get that right?"
        session.transcript.append({"from": "agent", "text": agent_text}); return agent_text, False
    if session.stage == "urgency":
        urgency = detect_urgency(text)
        if urgency: session.urgency = urgency
        elif is_yes(text): session.urgency = "urgent"
        elif is_no(text): session.urgency = "routine"
        else: session.urgency = "urgent" if re.search(r"\b(pain|swelling|bleeding|broken)\b", text.lower()) else "routine"
        session.stage = "details"; agent_text = "Thanks. May I have your name, and your insurance carrier name only, if you have one? Please don't share a policy number on this call."
        session.transcript.append({"from": "agent", "text": agent_text}); return agent_text, False
    if session.stage == "details":
        parts = [p.strip() for p in text.split(",") if p.strip()]
        session.name = parts[0] if parts else text
        session.insurance_carrier = parts[1] if len(parts) > 1 else None
        session.stage = "time"; agent_text = "And what day and time window works best — for example tomorrow morning, tomorrow afternoon, or a specific day and time?"
        session.transcript.append({"from": "agent", "text": agent_text}); return agent_text, False
    if session.stage == "time":
        session.time_window = text; session.stage = "confirm"
        agent_text = f"To confirm: {session.service} for {session.name}, {session.time_window}, urgency {session.urgency}. Is that correct?"
        session.transcript.append({"from": "agent", "text": agent_text}); return agent_text, False
    if session.stage == "confirm":
        if is_yes(text):
            session.stage = "done"; session.status = "confirmed"
            agent_text = f"Confirmed. I've logged that booking and I'll send you a confirmation message if you said yes to texts. Thanks for calling {session.business}."
            session.transcript.append({"from": "agent", "text": agent_text}); return agent_text, True
        session.stage = "details"; agent_text = "No problem — let's fix it. May I have your name and insurance carrier name again?"
        session.transcript.append({"from": "agent", "text": agent_text}); return agent_text, False
    agent_text = f"Thanks for calling {session.business}."
    session.transcript.append({"from": "agent", "text": agent_text}); return agent_text, True
def synthesize_speech(text):
    if os.environ.get("GOOGLE_TTS_ENABLED") != "1":
        return None
    return None
from store import load_bookings as _load, save_booking as _save  # noqa: E402
def load_bookings(path=None):
    return _load(path)
def save_booking(path, booking):
    return _save(path, booking)
