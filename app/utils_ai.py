import os, json, re
from datetime import datetime, timedelta
from django.utils import timezone

# Optional: Google Generative AI
try:
    import google.generativeai as genai
    GEMINI_KEY = os.getenv("AIzaSyDAv4UEGoc2BuZjff7H1GdKsbxILPgIZnY")
    if GEMINI_KEY:
        genai.configure(api_key=GEMINI_KEY)
    _HAS_GEMINI = bool(GEMINI_KEY)
except Exception:
    _HAS_GEMINI = False

PROMPT = (
    "You are a structured extractor. Given a donor's free text about donated food, "
    "return strict JSON with keys: expired_at_iso (ISO datetime). "
   "Use donor's locale IST if helpful."
)

IST_OFFSET = timedelta(hours=5, minutes=30)

def _regex_fallback(note: str):
    # Very naive heuristics
    desc = note.strip()
    prepared = timezone.now()
    # Find times like 6:30 PM, 18:30, etc.
    m = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)?", note)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2))
        ap = m.group(3)
        if ap:
            ap = ap.lower()
            if ap == "pm" and hh != 12:
                hh += 12
            if ap == "am" and hh == 12:
                hh = 0
        prepared = timezone.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
    return {
        "description": desc,
        "prepared_at_iso": prepared.isoformat()
    }

def parse_food_note(note: str, shelf_hours: int = 4):
    """Parse donor free text. Return (description, prepared_at, expires_at)."""
    data = None
    if _HAS_GEMINI:
        try:
            model = genai.GenerativeModel("gemini-1.5-flash")
            resp = model.generate_content(f"{PROMPT}\n\nNOTE:\n{note}")
            text = resp.text
            # Try to locate JSON in response
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1:
                data = json.loads(text[start:end+1])
        except Exception:
            data = None
    if not data:
        data = _regex_fallback(note)

    description = (data.get("description") or note or "").strip()
    try:
        prepared = datetime.fromisoformat(data.get("prepared_at_iso"))
        if prepared.tzinfo is None:
            # assume IST per project context
            prepared = prepared - IST_OFFSET
            prepared = prepared.replace(tzinfo=timezone.utc)
        prepared = prepared.astimezone(timezone.get_current_timezone())
    except Exception:
        prepared = timezone.now()

    expires_at = prepared + timedelta(hours=shelf_hours)
    return description, prepared, expires_at