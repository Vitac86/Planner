import re
from typing import Optional


_PUNCTUATION_NORMALIZER = re.compile(r"[^0-9a-zа-я]+", re.IGNORECASE)
_SPACE_NORMALIZER = re.compile(r"\s+")
_QUOTE_CHARS = "«»\"'`“”‚’"


def _strip_quotes(text: str) -> str:
    return text.translate({ord(ch): " " for ch in _QUOTE_CHARS})


def org_name_canon(name: Optional[str]) -> str:
    """
    Produce a canonical representation of an organization name.

    Steps:
    - lower-case
    - strip common quotes
    - remove punctuation/special symbols, keep letters/digits/spaces
    - collapse repeated whitespace
    - trim
    """
    if not name:
        return ""
    t = str(name).lower()
    t = _strip_quotes(t)
    t = _PUNCTUATION_NORMALIZER.sub(" ", t)
    t = _SPACE_NORMALIZER.sub(" ", t).strip()
    return t

