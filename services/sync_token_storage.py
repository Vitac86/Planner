from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from datetime_utils import ensure_utc, to_rfc3339_utc, utc_now
from core.settings import GOOGLE_SYNC


def _parse_datetime(value: Optional[str]):
    from datetime_utils import parse_rfc3339

    return ensure_utc(parse_rfc3339(value)) if value else None


class SyncTokenStorage:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or GOOGLE_SYNC.sync_token_path)

    # ------------------------------------------------------------------
    # generic helpers
    def _load(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError:
            return {}
        if isinstance(data, str):
            return {"calendar": {"syncToken": data}}
        if isinstance(data, dict):
            return data
        return {}

    def _save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data), encoding="utf-8")

    # ------------------------------------------------------------------
    # Calendar token helpers
    def get_calendar_token(self) -> Optional[str]:
        data = self._load()
        calendar = data.get("calendar", {})
        if isinstance(calendar, dict):
            token = calendar.get("syncToken")
            if token:
                return str(token)
        if "syncToken" in data:
            return str(data["syncToken"])
        return None

    def set_calendar_token(self, token: str) -> None:
        data = self._load()
        calendar = data.setdefault("calendar", {})
        if isinstance(calendar, dict):
            calendar["syncToken"] = token
        else:
            data["calendar"] = {"syncToken": token}
        self._save(data)

    def clear_calendar_token(self) -> None:
        data = self._load()
        calendar = data.get("calendar")
        if isinstance(calendar, dict) and "syncToken" in calendar:
            calendar.pop("syncToken", None)
        if "syncToken" in data:
            data.pop("syncToken", None)
        self._save(data)

    def set_calendar_pull_timestamp(self, moment=None) -> None:
        data = self._load()
        calendar = data.setdefault("calendar", {})
        if isinstance(calendar, dict):
            calendar["lastPullAt"] = to_rfc3339_utc(ensure_utc(moment) if moment else utc_now())
        self._save(data)

    def get_calendar_pull_timestamp(self):
        data = self._load()
        calendar = data.get("calendar", {})
        if isinstance(calendar, dict):
            return _parse_datetime(calendar.get("lastPullAt"))
        return None

    # ------------------------------------------------------------------
    # Tasks helpers
    def get_tasks_updated_min(self):
        data = self._load()
        tasks = data.get("tasks", {})
        if isinstance(tasks, dict):
            value = tasks.get("updatedMin")
            return _parse_datetime(value)
        return None

    def set_tasks_updated_min(self, value) -> None:
        data = self._load()
        tasks = data.setdefault("tasks", {})
        if isinstance(tasks, dict):
            tasks["updatedMin"] = to_rfc3339_utc(ensure_utc(value)) if value else None
        self._save(data)

    def set_tasks_pull_timestamp(self, moment=None) -> None:
        data = self._load()
        tasks = data.setdefault("tasks", {})
        if isinstance(tasks, dict):
            tasks["lastPullAt"] = to_rfc3339_utc(ensure_utc(moment) if moment else utc_now())
        self._save(data)

    def get_tasks_pull_timestamp(self):
        data = self._load()
        tasks = data.get("tasks", {})
        if isinstance(tasks, dict):
            return _parse_datetime(tasks.get("lastPullAt"))
        return None

    # ------------------------------------------------------------------
    # Push helpers
    def set_last_push_timestamp(self, moment=None) -> None:
        data = self._load()
        data["lastPushAt"] = to_rfc3339_utc(ensure_utc(moment) if moment else utc_now())
        self._save(data)

    def get_last_push_timestamp(self):
        data = self._load()
        return _parse_datetime(data.get("lastPushAt"))

    # ------------------------------------------------------------------
    def clear_all(self) -> None:
        if self.path.exists():
            self.path.unlink()


__all__ = ["SyncTokenStorage"]
