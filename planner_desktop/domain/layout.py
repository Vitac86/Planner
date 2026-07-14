"""Пороги отзывчивой раскладки нового десктопа.

Чистый Python: QML не хардкодит числа, а спрашивает режим у
UiStateViewModel.layoutModeFor(width). Ширина здесь — доступная ширина
ОБЛАСТИ КОНТЕНТА страницы (без сайдбара), потому что именно от неё
зависит, помещается ли правая колонка инспектора.

Режимы:

- compact  — секции в один столбец, инспектор — выезжающая панель,
  ряды кнопок сворачиваются; ничего не обрезается;
- normal   — один основной столбец пошире, инспектор — выезжающая панель;
- wide     — двухколоночная раскладка с боковой колонкой инспектора.
"""
from __future__ import annotations

MODE_COMPACT = "compact"
MODE_NORMAL = "normal"
MODE_WIDE = "wide"

#: Верхняя граница compact-режима (ширина контента, px).
COMPACT_MAX_WIDTH = 700

#: Нижняя граница wide-режима (ширина контента, px) — хватает на основную
#: колонку + колонку инспектора 320px.
WIDE_MIN_WIDTH = 1000

#: Минимальный работоспособный размер окна (px): compact-раскладка,
#: сайдбар и контент без наложений и обрезанных русских подписей.
MIN_WINDOW_WIDTH = 680
MIN_WINDOW_HEIGHT = 560


def layout_mode(content_width: float) -> str:
    """Режим раскладки страницы по доступной ширине контента."""
    if content_width < COMPACT_MAX_WIDTH:
        return MODE_COMPACT
    if content_width < WIDE_MIN_WIDTH:
        return MODE_NORMAL
    return MODE_WIDE


def inspector_placement(mode: str) -> str:
    """Куда класть инспектор задачи: боковая колонка или выезжающая панель."""
    return "rail" if mode == MODE_WIDE else "drawer"
