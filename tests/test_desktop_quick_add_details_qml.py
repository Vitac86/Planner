"""Run the real Quick Add component and its viewmodel in an isolated GUI process."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_collapsing_quick_add_preserves_details_and_compact_parsing():
    root = Path(__file__).resolve().parents[1]
    script = textwrap.dedent('''
        from datetime import datetime
        from pathlib import Path
        from PySide6.QtCore import QObject, QMetaObject, QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlComponent, QQmlEngine
        from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
        from planner_desktop.viewmodels.today_viewmodel import TodayViewModel

        app = QGuiApplication([])
        repo = FakeTaskRepository(seed=False)
        vm = TodayViewModel(repo, now_provider=lambda: datetime(2026, 7, 15, 10))
        engine = QQmlEngine()
        engine.rootContext().setContextProperty("todayVm", vm)
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(
            Path("planner_desktop/qml/components/QuickAdd.qml").resolve()
        )))
        form = component.create()
        assert form is not None, [error.toString() for error in component.errors()]

        def field(name):
            result = form.findChild(QObject, "quickAdd" + name)
            assert result is not None, name
            return result

        # Date, duration and notes survive opening Details, filling, collapsing.
        form.setProperty("expanded", True)
        field("Title").setProperty("text", "Meet")
        field("Notes").setProperty("text", "Bring the draft")
        field("Calendar").setProperty("checked", True)
        field("Date").setProperty("dateText", "2026-07-20")
        field("Time").setProperty("timeText", "14:30")
        field("Duration").setProperty("durationText", "45")
        form.setProperty("expanded", False)
        assert QMetaObject.invokeMethod(form, "submit")
        assert len(repo.list_all()) == 1
        task = repo.list_all()[0]
        assert task.notes == "Bring the draft"
        assert task.start == datetime(2026, 7, 20, 14, 30)
        assert task.duration_minutes == 45
        assert field("Notes").property("text") == ""
        assert not form.property("hasDetails")

        # A notes-only form remains undated after collapsing.
        form.setProperty("expanded", True)
        field("Title").setProperty("text", "Buy a book")
        field("Notes").setProperty("text", "Hardcover edition")
        form.setProperty("expanded", False)
        assert QMetaObject.invokeMethod(form, "submit")
        task = repo.list_all()[1]
        assert task.notes == "Hardcover edition"
        assert task.start is None

        # Once details are cleared, the compact natural-language path still works.
        field("Title").setProperty("text", "Отчёт завтра 15:00")
        assert QMetaObject.invokeMethod(form, "submit")
        task = repo.list_all()[2]
        assert task.title == "Отчёт"
        assert task.start == datetime(2026, 7, 16, 15)
    ''')
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=root,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software"},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
