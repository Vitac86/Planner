"""Политика маршрутизации клавиатурных сокращений.

Чистый Python: QML не решает сам, когда сокращение уместно, а спрашивает
UiStateViewModel.allowShortcut(...), который делегирует сюда. Благодаря
этому правило «голые клавиши не мешают набору текста» тестируется без Qt.

Правила:

- «голые» клавиши (Enter, Space, Delete, стрелки, «/») работают только
  вне текстовых полей и только когда не открыт диалог;
- Ctrl-сочетания страницы (Ctrl+N, Ctrl+K, Ctrl+R, Ctrl+Shift+N) работают
  и при фокусе в тексте (не конфликтуют с вводом), но не поверх диалога;
- сокращения диалога (Ctrl+S, Escape) живут в самом диалоге и здесь
  не описываются: окно их не перехватывает;
- Ctrl+F открывает глобальный поиск даже из текстового поля; Ctrl+A и
  Ctrl+D работают только в контексте списка/выбранной задачи и уступают
  текстовому вводу.
"""
from __future__ import annotations

from typing import Tuple

# Сокращения окна (вне диалогов). bare=True — «голая» клавиша, которая
# обязана уступать текстовому вводу.
_WINDOW_SHORTCUTS: dict[str, bool] = {
    "new_task": False,          # Ctrl+N
    "new_scheduled_task": False,  # Ctrl+Shift+N
    "new_from_template": False,  # Ctrl+Alt+N — новая задача из шаблона
    "quick_add": False,         # Ctrl+K
    "refresh": False,           # Ctrl+R — только локальные модели, НЕ синк
    "search": False,            # Ctrl+F
    "select_all": True,         # Ctrl+A только в списке результатов
    "duplicate_selected": True,  # Ctrl+D вне текстового ввода
    "open_selected": True,      # Enter
    "toggle_selected": True,    # Space
    "delete_selected": True,    # Delete
    "clear_selection": True,    # Esc (через Keys, но политика общая)
    "calendar_prev_day": True,  # Left
    "calendar_next_day": True,  # Right
    "calendar_prev_period": True,  # PageUp
    "calendar_next_period": True,  # PageDown
    "calendar_today": True,  # Home
    "calendar_prev_event": True,  # Up
    "calendar_next_event": True,  # Down
    "calendar_move_slot": True,  # Alt+Up/Down
    "calendar_move_day": True,  # Alt+Shift+Left/Right
    "calendar_resize": True,  # Alt+Shift+Up/Down
    "calendar_to_all_day": True,  # Ctrl+Alt+A
    "calendar_unschedule": True,  # Ctrl+Alt+U
    "quick_add_slash": True,    # «/»
}

RESERVED_SHORTCUTS: Tuple[str, ...] = ()


def known_shortcuts() -> Tuple[str, ...]:
    return tuple(_WINDOW_SHORTCUTS)


def allow_shortcut(name: str, *, typing: bool, dialog_open: bool) -> bool:
    """Можно ли сейчас сработать сокращению окна.

    typing — фокус в текстовом поле; dialog_open — открыт модальный
    диалог/попап (его собственные клавиши обрабатывает он сам).
    """
    if name in RESERVED_SHORTCUTS:
        return False
    bare = _WINDOW_SHORTCUTS.get(name)
    if bare is None:
        return False  # неизвестное имя — безопаснее не срабатывать
    if dialog_open:
        return False
    if bare and typing:
        return False
    return True
