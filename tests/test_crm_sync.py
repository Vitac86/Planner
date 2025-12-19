import sqlite3

from crm.scripts import db
from crm.scripts.sync_api_to_main import pick_email_any
from crm.scripts.sync_registry import map_registry_row, upsert_registry_contract
from crm.scripts.utils_text import org_name_canon


def test_pick_email_any_variants():
    assert pick_email_any(["a@b.com"]) == "a@b.com"
    assert pick_email_any("['a@b.com']") == "a@b.com"
    assert pick_email_any({"email": "a@b.com"}) == "a@b.com"
    assert pick_email_any(None) == ""


def test_org_name_canon_punctuation():
    assert org_name_canon("Финхаб Глобал Лтд") == org_name_canon("Финхаб Глобал Лтд.")
    assert org_name_canon(" «Финхаб--Глобал» ") == org_name_canon("финхаб глобал")


def test_contract_migration_idempotent(tmp_path):
    db_path = tmp_path / "contracts.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE contracts (id INTEGER PRIMARY KEY, number TEXT)")
    conn.commit()
    db.ensure_contracts_schema(conn)

    columns = {row[1] for row in conn.execute("PRAGMA table_info(contracts)")}
    assert "authorized_rep_fio" in columns
    assert "authorized_rep_position" in columns

    # Should not raise on second run
    db.ensure_contracts_schema(conn)


def test_registry_mapping_and_upsert_respects_existing():
    conn = sqlite3.connect(":memory:")
    db.ensure_contracts_schema(conn)

    existing_payload = {
        "number": "CNT-001",
        "authorized_rep_fio": "Saved",
        "authorized_rep_position": "Saved",
    }
    db.upsert_contract(conn, existing_payload, null_only_fields={"authorized_rep_fio", "authorized_rep_position"})

    incoming_row = {
        "Номер": "CNT-001",
        "Уполномоченный представитель  (ФИО)": "New Name",
        "Уполномоченный представитель (должность)": "New Position",
    }

    mapped = map_registry_row(incoming_row)
    assert mapped["authorized_rep_fio"] == "New Name"
    assert mapped["authorized_rep_position"] == "New Position"

    # Should not overwrite populated fields
    upsert_registry_contract(conn, incoming_row)
    row = conn.execute(
        "SELECT authorized_rep_fio, authorized_rep_position FROM contracts WHERE number = ?",
        ("CNT-001",),
    ).fetchone()
    assert row[0] == "Saved"
    assert row[1] == "Saved"

    # Clear existing values and re-run to allow updates
    conn.execute(
        "UPDATE contracts SET authorized_rep_fio = '', authorized_rep_position = '' WHERE number = ?",
        ("CNT-001",),
    )
    conn.commit()
    upsert_registry_contract(conn, incoming_row)
    row = conn.execute(
        "SELECT authorized_rep_fio, authorized_rep_position FROM contracts WHERE number = ?",
        ("CNT-001",),
    ).fetchone()
    assert row[0] == "New Name"
    assert row[1] == "New Position"

