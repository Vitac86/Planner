import re
from typing import Any, Dict, Optional

from . import db
from .utils_text import org_name_canon


EMAIL_REGEX = re.compile(r"[a-z0-9_.+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+", re.IGNORECASE)
LISTIFIED_EMAIL_REGEX = re.compile(r"^\s*\[\s*'?.+@.+?'?\s*\]\s*$")


def _extract_email_from_string(value: str) -> Optional[str]:
    matches = EMAIL_REGEX.findall(value or "")
    if not matches:
        return None
    return matches[0]


def is_valid_email(value: Optional[str]) -> bool:
    if not value:
        return False
    return bool(EMAIL_REGEX.fullmatch(value.strip()))


def is_listified_email(value: Optional[str]) -> bool:
    if not value or not isinstance(value, str):
        return False
    return bool(LISTIFIED_EMAIL_REGEX.match(value.strip()))


def pick_email_any(value: Any) -> str:
    """
    Extract the first valid email from various shapes of input.
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        for item in value:
            email = pick_email_any(item)
            if email:
                return email
        return ""
    if isinstance(value, dict):
        for key in ("email", "value", "address"):
            if key in value:
                email = pick_email_any(value[key])
                if email:
                    return email
        return ""
    if isinstance(value, str):
        extracted = _extract_email_from_string(value)
        return extracted or ""

    # Fallback: coerce to string and retry
    return pick_email_any(str(value))


def should_overwrite_email(existing: Optional[str], new_email: str) -> bool:
    """
    Decide whether to overwrite an existing email field.
    - If the database value is empty/null -> allow.
    - If the database value looks like \"['email']\" -> allow when the new one is valid.
    - Otherwise do not overwrite non-empty values.
    """
    if not is_valid_email(new_email):
        return False
    if not existing:
        return True
    if is_listified_email(existing):
        return True
    return False


def sync_ul(conn, org_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Upsert a legal entity using canonical name matching and normalized email.
    """
    email_value = pick_email_any(org_payload.get("contact_email") or org_payload.get("email"))
    prepared = {
        "name": org_payload.get("name") or "",
        "name_canon": org_name_canon(org_payload.get("name")),
        "inn": org_payload.get("inn") or "",
        "ogrn": org_payload.get("ogrn") or "",
        "email": email_value,
    }
    return db.upsert_organization(conn, prepared, should_update_email=should_overwrite_email)

