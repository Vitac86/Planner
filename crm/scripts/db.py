import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional, Sequence, Tuple

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "app.db"


@contextmanager
def get_connection(db_path: Path = DEFAULT_DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def ensure_clients_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS clients_ul (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            name_canon TEXT,
            inn TEXT,
            ogrn TEXT,
            email TEXT,
            legal_address TEXT,
            phone TEXT
        )
        """
    )
    conn.commit()


def ensure_contracts_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS contracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            number TEXT UNIQUE,
            client_id INTEGER,
            title TEXT,
            amount NUMERIC,
            authorized_rep_fio TEXT,
            authorized_rep_position TEXT
        )
        """
    )
    # Migrate missing columns idempotently
    for column in ("authorized_rep_fio", "authorized_rep_position"):
        if not column_exists(conn, "contracts", column):
            conn.execute(f"ALTER TABLE contracts ADD COLUMN {column} TEXT")
    conn.commit()


def _select_one(conn: sqlite3.Connection, query: str, params: Sequence[Any]) -> Optional[Dict[str, Any]]:
    cur = conn.execute(query, params)
    row = cur.fetchone()
    if not row:
        return None
    try:
        return dict(row)
    except TypeError:
        # Row factory may not be configured; rebuild mapping manually.
        columns = [col[0] for col in cur.description]
        return {col: row[idx] for idx, col in enumerate(columns)}


def _update_row(conn: sqlite3.Connection, table: str, data: Dict[str, Any], where_clause: str, params: Sequence[Any]) -> None:
    if not data:
        return
    columns = ", ".join(f"{k}=?" for k in data.keys())
    values = list(data.values()) + list(params)
    conn.execute(f"UPDATE {table} SET {columns} WHERE {where_clause}", values)
    conn.commit()


def upsert_organization(
    conn: sqlite3.Connection,
    payload: Dict[str, Any],
    should_update_email: Optional[Callable[[Optional[str], str], bool]] = None,
) -> Dict[str, Any]:
    ensure_clients_schema(conn)
    inn = (payload.get("inn") or "").strip()
    name_canon = (payload.get("name_canon") or "").strip()

    existing = None
    if inn:
        existing = _select_one(conn, "SELECT * FROM clients_ul WHERE inn = ? LIMIT 1", (inn,))
    if not existing and name_canon:
        existing = _select_one(conn, "SELECT * FROM clients_ul WHERE name_canon = ? LIMIT 1", (name_canon,))

    if existing:
        updates: Dict[str, Any] = {}
        for field in ("name", "name_canon", "inn", "ogrn"):
            incoming = payload.get(field)
            if incoming and existing.get(field) != incoming:
                updates[field] = incoming

        new_email = payload.get("email") or ""
        if should_update_email and should_update_email(existing.get("email"), new_email):
            updates["email"] = new_email
        elif not existing.get("email") and new_email:
            updates["email"] = new_email

        if updates:
            _update_row(conn, "clients_ul", updates, "id = ?", (existing["id"],))
            existing.update(updates)
        return existing

    insert_fields = ["name", "name_canon", "inn", "ogrn", "email", "legal_address", "phone"]
    values = [payload.get(field) for field in insert_fields]
    placeholders = ", ".join("?" for _ in insert_fields)
    conn.execute(
        f"INSERT INTO clients_ul ({', '.join(insert_fields)}) VALUES ({placeholders})",
        values,
    )
    conn.commit()
    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return _select_one(conn, "SELECT * FROM clients_ul WHERE id = ?", (new_id,)) or {}


def upsert_contract(
    conn: sqlite3.Connection,
    payload: Dict[str, Any],
    null_only_fields: Iterable[str],
) -> Dict[str, Any]:
    ensure_contracts_schema(conn)
    number = payload.get("number")
    existing = None
    if number:
        existing = _select_one(conn, "SELECT * FROM contracts WHERE number = ? LIMIT 1", (number,))

    null_only_fields = set(null_only_fields)
    if existing:
        updates: Dict[str, Any] = {}
        for field, incoming in payload.items():
            if field == "id":
                continue
            if incoming is None or incoming == "":
                continue
            if field in null_only_fields:
                if not existing.get(field):
                    updates[field] = incoming
            elif existing.get(field) != incoming:
                updates[field] = incoming
        if updates:
            _update_row(conn, "contracts", updates, "id = ?", (existing["id"],))
            existing.update(updates)
        return existing

    columns = list(payload.keys())
    values = [payload.get(col) for col in columns]
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO contracts ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )
    conn.commit()
    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return _select_one(conn, "SELECT * FROM contracts WHERE id = ?", (new_id,)) or {}
