from __future__ import annotations
import flet as ft
from typing import List, Callable


class OverlayManager:
    def __init__(self, page: ft.Page):
        self.page = page
        self._stack: List[Callable[[], None]] = []

        def on_key(e: ft.KeyboardEvent):
            if e.key == "Escape" and self._stack:
                closer = self._stack[-1]
                try:
                    closer()
                finally:
                    if self._stack and self._stack[-1] is closer:
                        self._stack.pop()
                self.page.update()

        self.page.on_keyboard_event = on_key

    def push_dialog(self, dialog: ft.AlertDialog, on_close: Callable[[], None] | None = None):
        def _close():
            if on_close:
                on_close()
            dialog.open = False
            self.page.update()

        self._stack.append(_close)
        self.page.dialog = dialog
        dialog.on_dismiss = lambda e: self.pop_if(_close)
        dialog.open = True
        self.page.update()

    def push_overlay(self, ctrl: ft.Control):
        backdrop = ft.Container(
            expand=True,
            bgcolor=ft.colors.with_opacity(0.40, ft.colors.BLACK),
            data="planner_backdrop",
        )
        layer = ft.Stack([backdrop, ctrl], data="planner_layer")
        self.page.overlay.append(layer)

        def _close():
            try:
                self.page.overlay.remove(layer)
            except ValueError:
                pass

        self._stack.append(_close)
        self.page.update()
        return _close

    def pop_top(self):
        if not self._stack:
            return
        closer = self._stack.pop()
        closer()
        self.page.update()

    def pop_if(self, closer: Callable[[], None]):
        if closer in self._stack:
            self._stack.remove(closer)
