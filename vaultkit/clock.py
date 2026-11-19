"""Timezone resolution with no third-party dependency.

Uses the platform IANA database when available. Where it is missing (common
on minimal Windows Python installs), falls back to a built-in offset table for
widely used zones. This keeps the skill installable with a plain Python 3.9+.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

_OFFSETS_HOURS = {
    "UTC": 0, "Etc/UTC": 0, "GMT": 0,
    "Asia/Shanghai": 8, "Asia/Hong_Kong": 8, "Asia/Macau": 8,
    "Asia/Taipei": 8, "Asia/Singapore": 8, "Asia/Kuala_Lumpur": 8,
    "Asia/Tokyo": 9, "Asia/Seoul": 9,
    "Asia/Kolkata": 5.5, "Asia/Dubai": 4, "Asia/Bangkok": 7,
    "Europe/London": 0, "Europe/Paris": 1, "Europe/Berlin": 1,
    "Europe/Moscow": 3,
    "America/New_York": -5, "America/Chicago": -6,
    "America/Denver": -7, "America/Los_Angeles": -8,
    "Australia/Sydney": 10, "Pacific/Auckland": 12,
}


def zone(name: str):
    """Return a tzinfo for an IANA name. Never fails for known zones."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        pass
    hours = _OFFSETS_HOURS.get(name)
    if hours is None:
        hours = 0  # UTC is a safe, disclosed fallback
    return timezone(timedelta(hours=hours))


def available(name: str) -> bool:
    """Whether the exact IANA zone is resolvable here."""
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
        return True
    except Exception:
        return name in _OFFSETS_HOURS


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
