"""Pure, non-persistent selection policy shared by task collections."""
from __future__ import annotations

from typing import Iterable, Optional, Tuple


class TaskSelection:
    """Tracks visible ordering, selected uids and a Shift-range anchor."""

    def __init__(self) -> None:
        self._visible: Tuple[str, ...] = ()
        self._selected: set[str] = set()
        self._anchor: Optional[str] = None

    @property
    def visible(self) -> Tuple[str, ...]:
        return self._visible

    @property
    def selected(self) -> Tuple[str, ...]:
        return tuple(uid for uid in self._visible if uid in self._selected)

    @property
    def anchor(self) -> Optional[str]:
        return self._anchor

    @property
    def count(self) -> int:
        return len(self._selected)

    def contains(self, uid: str) -> bool:
        return uid in self._selected

    def set_visible(self, uids: Iterable[str]) -> bool:
        visible = tuple(dict.fromkeys(str(uid) for uid in uids if uid))
        before = (self._visible, frozenset(self._selected), self._anchor)
        self._visible = visible
        allowed = set(visible)
        self._selected.intersection_update(allowed)
        if self._anchor not in allowed:
            self._anchor = None
        return before != (self._visible, frozenset(self._selected), self._anchor)

    def select(self, uid: str, *, ctrl: bool = False, shift: bool = False) -> bool:
        uid = str(uid or "")
        if uid not in self._visible:
            return False
        before = (frozenset(self._selected), self._anchor)
        if shift and self._anchor in self._visible:
            left = self._visible.index(self._anchor)
            right = self._visible.index(uid)
            start, end = sorted((left, right))
            range_uids = set(self._visible[start:end + 1])
            self._selected = self._selected | range_uids if ctrl else range_uids
        elif ctrl:
            if uid in self._selected:
                self._selected.remove(uid)
            else:
                self._selected.add(uid)
            self._anchor = uid
        else:
            self._selected = {uid}
            self._anchor = uid
        if shift:
            # Keep the original range anchor; this mirrors desktop list UX.
            self._anchor = self._anchor or uid
        return before != (frozenset(self._selected), self._anchor)

    def toggle(self, uid: str) -> bool:
        return self.select(uid, ctrl=True)

    def select_all_visible(self) -> bool:
        before = frozenset(self._selected)
        self._selected = set(self._visible)
        if self._anchor not in self._selected:
            self._anchor = self._visible[0] if self._visible else None
        return before != frozenset(self._selected)

    def clear(self) -> bool:
        changed = bool(self._selected or self._anchor)
        self._selected.clear()
        self._anchor = None
        return changed

