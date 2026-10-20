"""Isolated, atomic storage for unsaved UI forms; never touches task data."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from typing import Any


class DraftStore:
    """Keep edits in memory and persist them only when ``flush`` is called.

    ``path=None`` is deliberately memory-only for demo/injected repositories.
    The caller chooses the profile directory, independently of the task DB.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._drafts: dict[str, dict[str, dict[str, Any]]] = {}
        self._dirty = False
        if self.path is not None:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("version") == 1:
                    for context, entries in (data.get("drafts") or {}).items():
                        if isinstance(entries, dict):
                            self._drafts[str(context)] = {
                                str(uid): payload for uid, payload in entries.items()
                                if isinstance(payload, dict)
                            }
            except (OSError, ValueError, TypeError, AttributeError):
                # A damaged/missing draft file must not prevent app startup.
                self._drafts = {}

    def load(self, context: str, uid: str = "") -> dict[str, Any]:
        return deepcopy(self._drafts.get(context, {}).get(uid, {}))

    def save(self, context: str, uid: str, payload: dict[str, Any]) -> None:
        # Validate and detach mutable/QML-owned containers before publishing.
        clean = json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))
        if not isinstance(clean, dict):
            raise ValueError("Draft payload must be an object.")
        if self._drafts.get(context, {}).get(uid) == clean:
            return
        self._drafts.setdefault(context, {})[uid] = clean
        self._dirty = True

    def discard(self, context: str, uid: str = "") -> None:
        entries = self._drafts.get(context, {})
        if uid in entries:
            del entries[uid]
            if not entries:
                del self._drafts[context]
            self._dirty = True

    def flush(self) -> None:
        if not self._dirty:
            return
        if self.path is None:
            self._dirty = False
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as stream:
                temporary = stream.name
                json.dump({"version": 1, "drafts": self._drafts}, stream,
                          ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self._dirty = False
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass
