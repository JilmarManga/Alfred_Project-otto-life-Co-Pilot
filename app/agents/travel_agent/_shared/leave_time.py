from datetime import datetime
from typing import Tuple, Optional


def compute_leave_decision(
    leave_at_str: str,
    now: Optional[datetime] = None,
) -> Tuple[str, Optional[int]]:
    """Given a leave-at time string (e.g. '8:20 AM') and the current time,
    return (decision, minutes_until_leave).

    decision values:
    - 'not_yet'   — more than 10 minutes until leave time
    - 'leave_now' — 0–10 minutes until leave time
    - 'late'      — leave time already passed
    - 'unknown'   — could not parse leave_at_str
    """
    if now is None:
        now = datetime.now()

    try:
        leave_at_dt = datetime.strptime(leave_at_str, "%I:%M %p").replace(
            year=now.year, month=now.month, day=now.day
        )
        diff = (leave_at_dt - now).total_seconds() / 60
        minutes_until_leave = int(diff)

        if diff > 10:
            return "not_yet", minutes_until_leave
        elif diff >= 0:
            return "leave_now", minutes_until_leave
        else:
            return "late", minutes_until_leave
    except (ValueError, TypeError):
        return "unknown", None
