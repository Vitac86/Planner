from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from core.settings import GOOGLE_SYNC


class SyncTokenStorage:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or GOOGLE_SYNC.sync_token_path)

    def get(self) -> Optional[str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except json.JSONDecodeError:
            return None
        return data.get("syncToken")

    def set(self, token: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"syncToken": token}
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


__all__ = ["SyncTokenStorage"]
