import argparse
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from crm.scripts import db
from crm.scripts.utils_text import org_name_canon


def _load_clients(conn) -> List[Dict]:
    db.ensure_clients_schema(conn)
    cur = conn.execute("SELECT * FROM clients_ul")
    return [dict(row) for row in cur.fetchall()]


def _group_clients(rows: Iterable[Dict]) -> Dict[Tuple[str, str], List[Dict]]:
    grouped: Dict[Tuple[str, str], List[Dict]] = defaultdict(list)
    for row in rows:
        if row.get("inn"):
            key = ("inn", row["inn"])
        else:
            key = ("canon", org_name_canon(row.get("name")))
        grouped[key].append(row)
    return grouped


def _completeness_score(row: Dict) -> int:
    fields = ("inn", "ogrn", "email", "phone", "legal_address")
    return sum(1 for field in fields if row.get(field))


def _pick_master(rows: List[Dict]) -> Dict:
    return sorted(
        rows,
        key=lambda r: (
            -1 if r.get("inn") else 0,
            -_completeness_score(r),
            r.get("id", 0),
        ),
    )[0]


def _merge_fields(master: Dict, duplicate: Dict) -> Dict[str, str]:
    updates: Dict[str, str] = {}
    for field in ("name", "inn", "ogrn", "email", "phone", "legal_address", "name_canon"):
        if not master.get(field) and duplicate.get(field):
            updates[field] = duplicate[field]
    return updates


def merge_duplicates(conn, dry_run: bool = True) -> List[Tuple[int, int]]:
    rows = _load_clients(conn)
    grouped = _group_clients(rows)
    merged_pairs: List[Tuple[int, int]] = []

    for _, group in grouped.items():
        if len(group) < 2:
            continue
        master = _pick_master(group)
        for duplicate in group:
            if duplicate["id"] == master["id"]:
                continue
            merged_pairs.append((duplicate["id"], master["id"]))
            updates = _merge_fields(master, duplicate)
            if dry_run:
                print(f"[DRY-RUN] Merge {duplicate['id']} -> {master['id']}; updates={updates}")
                continue

            if updates:
                db._update_row(conn, "clients_ul", updates, "id = ?", (master["id"],))
                master.update(updates)

            db.ensure_contracts_schema(conn)
            conn.execute(
                "UPDATE contracts SET client_id = ? WHERE client_id = ?",
                (master["id"], duplicate["id"]),
            )
            conn.execute("DELETE FROM clients_ul WHERE id = ?", (duplicate["id"],))
            conn.commit()
    return merged_pairs


def main():
    parser = argparse.ArgumentParser(description="Merge duplicate legal entities (clients_ul).")
    parser.add_argument("--db-path", type=str, default=str(db.DEFAULT_DB_PATH), help="Path to SQLite database.")
    parser.add_argument("--dry-run", action="store_true", help="Simulate merge without writing changes.")
    args = parser.parse_args()

    with db.get_connection(Path(args.db_path)) as conn:
        pairs = merge_duplicates(conn, dry_run=args.dry_run)
        print(f"Found {len(pairs)} duplicates to merge.")


if __name__ == "__main__":
    main()
