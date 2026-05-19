"""Pure, deterministic time helpers for ReminderAgent.

No LLM, no I/O. Shared by SetReminderSkill, RescheduleReminderSkill and the
pending_reminder gate. Part-of-day defaults (product spec):
    morning → 09:00, afternoon → 15:00, night → 19:00  (user local tz)
"""
import re
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_PERIOD_CLOCK = {"morning": (9, 0), "afternoon": (15, 0), "night": (19, 0)}

# Phrase → period. "mañana" alone means *tomorrow*, so it only counts as
# "morning" when bound by an article ("la/por la/en la/de la mañana").
_PERIOD_PHRASES = [
    ("night", ("in the evening", "this evening", "at night", "tonight",
               "la noche", "de la noche", "por la noche", "en la noche",
               "esta noche", " evening", " night")),
    ("afternoon", ("in the afternoon", "this afternoon", "la tarde",
                    "de la tarde", "por la tarde", "en la tarde",
                    "esta tarde", " afternoon")),
    ("morning", ("in the morning", "this morning", "la mañana",
                 "de la mañana", "por la mañana", "en la mañana",
                 "la manana", "de la manana", "por la manana",
                 "en la manana", " morning")),
]

_CLOCK_RES = [
    re.compile(r"\b(\d{1,2}):(\d{2})\s*(am|pm|a\.m\.|p\.m\.)?\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,2})\s*(am|pm|a\.m\.|p\.m\.)\b", re.IGNORECASE),
    re.compile(r"\b(?:a\s+las|at|las)\s+(\d{1,2})(?::(\d{2}))?\b", re.IGNORECASE),
]


def resolve_tz(tz_name):
    try:
        return ZoneInfo(tz_name) if tz_name else ZoneInfo("UTC")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def fold(text):
    """Accent/case fold for matching (NFKD, strip combining marks, lower)."""
    if not text:
        return ""
    n = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in n if not unicodedata.combining(c)).lower().strip()


def _parse_iso(raw):
    try:
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


def detect_period(text):
    """Return 'morning'|'afternoon'|'night' if the text names a part of day."""
    t = " " + fold(text) + " "
    for period, phrases in _PERIOD_PHRASES:
        for p in phrases:
            if fold(p) in t:
                return period
    return None


def detect_clock(text):
    """Return (hour, minute) for an explicit clock time in the text, else None."""
    for rx in _CLOCK_RES:
        m = rx.search(text or "")
        if not m:
            continue
        groups = m.groups()
        hour = int(groups[0])
        minute = 0
        meridiem = None
        if rx is _CLOCK_RES[0]:
            minute = int(groups[1])
            meridiem = (groups[2] or "").lower().replace(".", "")
        elif rx is _CLOCK_RES[1]:
            meridiem = (groups[1] or "").lower().replace(".", "")
        else:  # "a las 7" / "at 7" / "las 7:30"
            if groups[1]:
                minute = int(groups[1])
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return (hour, minute)
    return None


def resolve_fire_at(*, reminder_time, reminder_period, tz_name, now_utc):
    """Resolve a fire time.

    Returns (fire_at_iso | None, status) where status is
    "resolved" or "needs_time_of_day". A concrete clock time wins. A bare
    part-of-day maps to the spec defaults on the given date (or today, rolling
    to tomorrow if already past). A date with no time-of-day → needs_time_of_day
    (the date is preserved by the caller's stash and passed back later).
    """
    tz = resolve_tz(tz_name)
    now_local = now_utc.astimezone(tz)
    period = reminder_period if reminder_period in _PERIOD_CLOCK else None

    dt_rt = _parse_iso(reminder_time) if reminder_time else None
    if dt_rt is not None and dt_rt.tzinfo is None:
        dt_rt = dt_rt.replace(tzinfo=tz)
    if dt_rt is not None:
        dt_rt = dt_rt.astimezone(tz)

    has_concrete_time = dt_rt is not None and (dt_rt.hour, dt_rt.minute, dt_rt.second) != (0, 0, 0)
    date_part = dt_rt.date() if dt_rt is not None else None

    # Concrete clock time wins outright (period, if any, is redundant).
    if has_concrete_time:
        fire = dt_rt
        if fire <= now_local:
            fire = fire + timedelta(days=1)
        return (fire.isoformat(), "resolved")

    if period is not None:
        h, m = _PERIOD_CLOCK[period]
        base = date_part or now_local.date()
        fire = datetime(base.year, base.month, base.day, h, m, tzinfo=tz)
        if fire <= now_local and date_part is None:
            fire = fire + timedelta(days=1)
        return (fire.isoformat(), "resolved")

    # Only a date (or nothing): we still need a time of day.
    return (None, "needs_time_of_day")


def parse_reply_time(reply_text, *, base_date_iso, tz_name, now_utc):
    """Classify a clarify reply into a concrete time or a part-of-day.

    Returns (reminder_time_iso | None, reminder_period | None). A part-of-day
    word yields a period (resolved later against base_date). An explicit clock
    is combined with base_date (or today, rolling to tomorrow if already past).
    """
    period = detect_period(reply_text)
    clock = detect_clock(reply_text)
    if clock is None and period is not None:
        return (None, period)
    if clock is not None:
        tz = resolve_tz(tz_name)
        now_local = now_utc.astimezone(tz)
        base_dt = _parse_iso(base_date_iso) if base_date_iso else None
        if base_dt is not None and base_dt.tzinfo is None:
            base_dt = base_dt.replace(tzinfo=tz)
        base = base_dt.astimezone(tz).date() if base_dt is not None else now_local.date()
        h, m = clock
        fire = datetime(base.year, base.month, base.day, h, m, tzinfo=tz)
        if fire <= now_local and (base_dt is None):
            fire = fire + timedelta(days=1)
        return (fire.isoformat(), None)
    return (None, None)
