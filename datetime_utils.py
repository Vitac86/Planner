from datetime import datetime, timezone
from typing import Optional, Union


def parse_rfc3339(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if "." in s:
        head, tail = s.split(".", 1)
        if "+" in tail or "-" in tail:
            if "+" in tail:
                frac, tz = tail.split("+", 1)
                sign = "+"
            else:
                frac, tz = tail.split("-", 1)
                sign = "-"
            s = f"{head}.{(frac + '000000')[:6]}{sign}{tz}"
        else:
            s = f"{head}.{(tail + '000000')[:6]}+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def to_rfc3339(dt: Optional[Union[datetime, str]]) -> Optional[str]:
    if dt is None:
        return None
    if isinstance(dt, str):
        dt = parse_rfc3339(dt)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
