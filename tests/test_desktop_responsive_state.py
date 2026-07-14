"""Пороги отзывчивой раскладки и UI-хелперы (domain/layout.py + ui_state).

QML не хардкодит числа: режим compact/normal/wide, размещение инспектора
и минимальный размер окна приходят из Python и фиксируются здесь.
"""
import pytest

from planner_desktop.domain.layout import (
    COMPACT_MAX_WIDTH,
    MIN_WINDOW_HEIGHT,
    MIN_WINDOW_WIDTH,
    MODE_COMPACT,
    MODE_NORMAL,
    MODE_WIDE,
    WIDE_MIN_WIDTH,
    inspector_placement,
    layout_mode,
)
from planner_desktop.viewmodels.ui_state import (
    UiStateViewModel,
    human_date,
    time_options,
)


# ---- пороги режимов ------------------------------------------------------------

@pytest.mark.parametrize("width, expected", [
    (0, MODE_COMPACT),
    (400, MODE_COMPACT),
    (COMPACT_MAX_WIDTH - 1, MODE_COMPACT),
    (COMPACT_MAX_WIDTH, MODE_NORMAL),
    (WIDE_MIN_WIDTH - 1, MODE_NORMAL),
    (WIDE_MIN_WIDTH, MODE_WIDE),
    (1600, MODE_WIDE),
])
def test_layout_mode_thresholds(width, expected):
    assert layout_mode(width) == expected


def test_inspector_placement_per_mode():
    assert inspector_placement(MODE_WIDE) == "rail"
    assert inspector_placement(MODE_NORMAL) == "drawer"
    assert inspector_placement(MODE_COMPACT) == "drawer"


def test_minimum_window_is_defined_and_compact_capable():
    assert MIN_WINDOW_WIDTH >= 600
    assert MIN_WINDOW_HEIGHT >= 480
    # при минимальной ширине контент (минус сайдбар ~236) — компактный режим
    assert layout_mode(MIN_WINDOW_WIDTH - 236) == MODE_COMPACT


# ---- мост в QML ------------------------------------------------------------------

def test_ui_state_layout_slots():
    ui = UiStateViewModel()
    assert ui.layoutModeFor(500) == MODE_COMPACT
    assert ui.layoutModeFor(800) == MODE_NORMAL
    assert ui.layoutModeFor(1200) == MODE_WIDE
    assert ui.inspectorPlacement(MODE_WIDE) == "rail"
    assert ui.inspectorPlacement(MODE_COMPACT) == "drawer"
    assert ui.minWindowWidth == MIN_WINDOW_WIDTH
    assert ui.minWindowHeight == MIN_WINDOW_HEIGHT


# ---- русские подписи дат и список времени -------------------------------------------

def test_human_date_russian():
    assert human_date("2026-07-14") == "вт, 14 июля 2026"
    assert human_date("2026-01-01") == "чт, 1 января 2026"


def test_human_date_tolerates_garbage():
    assert human_date("") == ""
    assert human_date("не дата") == ""
    assert human_date("2026-13-40") == ""


def test_time_options_half_hour_grid():
    options = time_options()
    assert len(options) == 48
    assert options[0] == "00:00"
    assert options[1] == "00:30"
    assert options[-1] == "23:30"


def test_ui_state_exposes_helpers():
    ui = UiStateViewModel()
    assert ui.humanDate("2026-07-14") == "вт, 14 июля 2026"
    assert len(ui.timeOptions) == 48
