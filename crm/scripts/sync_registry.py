import re
from typing import Any, Dict

from . import db
from .utils_text import org_name_canon as _shared_org_canon

_HEADER_SPACE = re.compile(r"[\s\u00A0]+")
_HEADER_HYPHEN = re.compile(r"[-–—]+")

_CONTRACT_COLS_ALL = [
    "number",
    "client_id",
    "title",
    "amount",
    "authorized_rep_fio",
    "authorized_rep_position",
]

_NULL_ONLY_FIELDS = {"authorized_rep_fio", "authorized_rep_position"}


def _org_name_canon(name: str) -> str:
    return _shared_org_canon(name)


def _normalize_header(title: str) -> str:
    normalized = str(title or "").strip().lower()
    normalized = _HEADER_HYPHEN.sub(" ", normalized)
    normalized = _HEADER_SPACE.sub(" ", normalized)
    return normalized


def _registry_synonyms() -> Dict[str, str]:
    synonyms = {
        "номер": "number",
        "номер договора": "number",
        "client id": "client_id",
        "клиент id": "client_id",
        "сумма": "amount",
        "название": "title",
        "уполномоченный представитель (фио)": "authorized_rep_fio",
        "уполномоченный представитель фио": "authorized_rep_fio",
        "уполномоченный представитель (должность)": "authorized_rep_position",
        "уполномоченный представитель должность": "authorized_rep_position",
    }
    return { _normalize_header(k): v for k, v in synonyms.items() }


def map_registry_row(row: Dict[str, Any]) -> Dict[str, Any]:
    synonyms = _registry_synonyms()
    payload: Dict[str, Any] = {}
    for header, value in row.items():
        field = synonyms.get(_normalize_header(header))
        if field:
            payload[field] = value
    return payload


def _prepare_contract_payload(mapped: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for col in _CONTRACT_COLS_ALL:
        if col in mapped:
            payload[col] = mapped[col]
    return payload


def upsert_registry_contract(conn, row: Dict[str, Any]) -> Dict[str, Any]:
    mapped = map_registry_row(row)
    payload = _prepare_contract_payload(mapped)

    # Ensure new fields are part of the payload even if they are empty strings.
    for field in _NULL_ONLY_FIELDS:
        payload.setdefault(field, mapped.get(field, ""))

    return db.upsert_contract(conn, payload, null_only_fields=_NULL_ONLY_FIELDS)

