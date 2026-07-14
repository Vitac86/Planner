import sqlite3

from planner_desktop.domain.tags import Tag
from planner_desktop.domain.task import Task, utc_now
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.storage.tag_repository import SQLiteTagRepository


def test_tag_migration_is_additive_and_idempotent(tmp_path):
    db_path = tmp_path / "desktop.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE preserved (value TEXT)")
        connection.execute("INSERT INTO preserved VALUES ('ok')")
        connection.commit()

    task_repo = SQLiteTaskRepository(db_path)
    task_repo.close()
    task_repo = SQLiteTaskRepository(db_path)
    task_repo.close()

    with sqlite3.connect(db_path) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"preserved", "tasks", "tags", "task_tags"} <= tables
        assert connection.execute("SELECT value FROM preserved").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5


def test_repository_reopen_persists_assignments_and_rename(tmp_path):
    db_path = tmp_path / "desktop.db"
    tasks = SQLiteTaskRepository(db_path)
    task = tasks.add(Task(title="Отчёт"))
    tags = SQLiteTagRepository(db_path)
    tag = tags.add(Tag("Работа", "работа"))
    tags.set_for_task(task.uid, [tag.id], utc_now())
    renamed = Tag(
        id=tag.id,
        name="Проект",
        normalized_name="проект",
        created_at=tag.created_at,
        updated_at=utc_now(),
    )
    tags.update(renamed)
    tags.close()
    tasks.close()

    tags = SQLiteTagRepository(db_path)
    tasks = SQLiteTaskRepository(db_path)
    try:
        assert [item.name for item in tags.list_for_task(task.uid)] == ["Проект"]
        assert tasks.get_by_uid(task.uid).tags == ("Проект",)
    finally:
        tags.close()
        tasks.close()


def test_delete_tag_removes_association_not_task(tmp_path):
    db_path = tmp_path / "desktop.db"
    tasks = SQLiteTaskRepository(db_path)
    task = tasks.add(Task(title="Остаётся"))
    tags = SQLiteTagRepository(db_path)
    tag = tags.add(Tag("Локально", "локально"))
    tags.set_for_task(task.uid, [tag.id], utc_now())

    assert tags.delete(tag.id) is True
    assert tags.list_for_task(task.uid) == []
    assert tasks.get_by_uid(task.uid).title == "Остаётся"
    tags.close()
    tasks.close()

