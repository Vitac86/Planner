"""Draft persistence is isolated from task storage and survives safe restarts."""
import os

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
import pytest

from planner_desktop.storage.draft_store import DraftStore
from planner_desktop.viewmodels.drafts_viewmodel import DraftsViewModel


@pytest.fixture
def qt_app():
    return QCoreApplication.instance() or QCoreApplication([])


def _wait_for_events(milliseconds):
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def test_memory_drafts_never_create_profile_files(tmp_path, monkeypatch):
    monkeypatch.setenv("PLANNER_DESKTOP_DATA_DIR", str(tmp_path / "profile"))
    store = DraftStore()
    store.save("quick-add", "", {"title": "Набросок"})
    store.flush()

    assert store.load("quick-add", "") == {"title": "Набросок"}
    assert list(tmp_path.iterdir()) == []
    assert DraftStore().load("quick-add", "") == {}


def test_drafts_keep_contexts_and_task_uids_separate_across_restart(tmp_path):
    path = tmp_path / "profile" / "drafts.json"
    store = DraftStore(path)
    entries = [
        ("quick-add", "", "Быстрое добавление"),
        ("editor:new", "", "Новая задача"),
        ("editor:task", "one", "Первая задача"),
        ("editor:task", "two", "Вторая задача"),
        ("editor:occurrence", "one", "Экземпляр серии"),
    ]
    for context, uid, title in entries:
        store.save(context, uid, {"title": title})
    assert not path.exists(), "Changes should remain in memory until flush"

    store.flush()
    restarted = DraftStore(path)
    for context, uid, title in entries:
        assert restarted.load(context, uid) == {"title": title}

    restarted.discard("editor:task", "one")
    restarted.flush()
    restarted_again = DraftStore(path)
    assert restarted_again.load("editor:task", "one") == {}
    assert restarted_again.load("editor:occurrence", "one") == {
        "title": "Экземпляр серии"
    }
    assert restarted_again.load("editor:task", "two") == {
        "title": "Вторая задача"
    }


def test_saved_and_loaded_drafts_do_not_share_mutable_payloads():
    store = DraftStore()
    original = {
        "title": "Повторение",
        "tagIds": [1, 2],
        "rule": {"weekdays": [0, 2]},
    }
    store.save("editor:new", "", original)
    original["title"] = "Изменено вне store"
    original["rule"]["weekdays"].append(4)
    loaded = store.load("editor:new", "")
    loaded["tagIds"].clear()
    loaded["rule"]["weekdays"].clear()

    assert store.load("editor:new", "") == {
        "title": "Повторение",
        "tagIds": [1, 2],
        "rule": {"weekdays": [0, 2]},
    }


def test_corrupted_draft_file_does_not_prevent_new_drafts(tmp_path):
    path = tmp_path / "drafts.json"
    path.write_text('{"unfinished":', encoding="utf-8")
    store = DraftStore(path)
    assert store.load("quick-add", "") == {}

    store.save("quick-add", "", {"title": "Новый черновик"})
    store.flush()
    assert DraftStore(path).load("quick-add", "") == {
        "title": "Новый черновик"
    }


def test_failed_atomic_replace_preserves_file_and_allows_retry(tmp_path, monkeypatch):
    path = tmp_path / "drafts.json"
    store = DraftStore(path)
    store.save("editor:new", "", {"title": "Сохранённый черновик"})
    store.flush()
    previous_bytes = path.read_bytes()
    store.save("editor:new", "", {"title": "Последние правки"})

    def fail_replace(source, destination):
        raise OSError("Disk is temporarily unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_replace)
        with pytest.raises(OSError, match="temporarily unavailable"):
            store.flush()
    assert path.read_bytes() == previous_bytes
    assert store.load("editor:new", "") == {"title": "Последние правки"}

    store.flush()
    assert DraftStore(path).load("editor:new", "") == {
        "title": "Последние правки"
    }
    assert set(tmp_path.iterdir()) == {path}, "Temporary files should be cleaned up"


def test_viewmodel_debounces_disk_but_keeps_latest_fields_in_memory(tmp_path, qt_app):
    path = tmp_path / "drafts.json"
    store = DraftStore(path)
    vm = DraftsViewModel(store)
    vm.saveDraft("editor:new", "", {"title": "Первый ввод", "notes": ""})
    vm.saveDraft("editor:new", "", {"title": "Последний ввод", "notes": "Заметка"})

    assert vm.loadDraft("editor:new", "")["title"] == "Последний ввод"
    assert not path.exists()
    _wait_for_events(500)
    saved = DraftStore(path).load("editor:new", "")
    assert saved["title"] == "Последний ввод"
    assert saved["notes"] == "Заметка"


def test_viewmodel_keeps_form_fields_but_ignores_task_identity_and_sync_metadata(qt_app):
    store = DraftStore()
    vm = DraftsViewModel(store)
    vm.saveDraft("editor:new", "", {
        "title": "Новая серия",
        "recurring": True,
        "rule": {"frequency": "daily", "interval": 2},
        "tagIds": [1],
        "uid": "foreign-task",
        "isLinked": True,
        "google_calendar_event_id": "foreign-event",
        "seriesUid": "foreign-series",
    })
    vm.flush()

    assert store.load("editor:new", "") == {
        "title": "Новая серия",
        "recurring": True,
        "rule": {"frequency": "daily", "interval": 2},
        "tagIds": [1],
    }


@pytest.mark.parametrize("context,uid", [
    ("unknown", ""),
    ("editor:task", ""),
    ("editor:occurrence", ""),
    ("editor:new", "existing-task"),
    ("quick-add", "existing-task"),
])
def test_invalid_form_context_cannot_publish_a_draft(qt_app, context, uid):
    store = DraftStore()
    vm = DraftsViewModel(store)
    vm.saveDraft(context, uid, {"title": "Wrong context"})
    assert vm.loadDraft(context, uid) == {}
    assert store.load(context, uid) == {}


@pytest.mark.parametrize("context", ["editor:task", "editor:occurrence"])
def test_missing_task_drafts_are_removed_without_affecting_new_draft(
    tmp_path, qt_app, context
):
    path = tmp_path / "drafts.json"
    store = DraftStore(path)
    store.save(context, "deleted-task", {"title": "Не воскрешать"})
    store.save("editor:new", "", {"title": "Несохранённая новая задача"})
    store.flush()
    checked = []

    def task_exists(uid):
        checked.append(uid)
        return False

    vm = DraftsViewModel(store, task_exists=task_exists)
    assert vm.loadDraft(context, "deleted-task") == {}
    assert checked == ["deleted-task"]
    assert vm.loadDraft("editor:new", "")["title"] == "Несохранённая новая задача"
    assert checked == ["deleted-task"], "New drafts must not require a task record"
    vm.flush()
    assert DraftStore(path).load(context, "deleted-task") == {}


def test_viewmodel_disk_error_preserves_draft_and_clears_after_retry(
    tmp_path, qt_app, monkeypatch
):
    store = DraftStore(tmp_path / "drafts.json")
    vm = DraftsViewModel(store)
    vm.saveDraft("quick-add", "", {"title": "Не потерять текст"})

    def fail_replace(source, destination):
        raise OSError("Disk is temporarily unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_replace)
        vm.flush()
    assert vm.storageError
    assert vm.loadDraft("quick-add", "")["title"] == "Не потерять текст"

    vm.flush()
    assert not vm.storageError
    assert DraftStore(tmp_path / "drafts.json").load("quick-add", "")["title"] == (
        "Не потерять текст"
    )
