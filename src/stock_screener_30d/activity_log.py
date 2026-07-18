from __future__ import annotations

import threading
from collections import deque
from datetime import datetime
from typing import Any

_MAX_ENTRIES = 200
_lock = threading.Lock()
_entries: deque[dict[str, Any]] = deque(maxlen=_MAX_ENTRIES)
_counter = 0


def log(message: str, level: str = "info", source: str = "system") -> int:
    global _counter
    with _lock:
        _counter += 1
        entry = {
            "id": _counter,
            "ts": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "source": source,
            "message": message,
        }
        _entries.append(entry)
        return _counter


def get_logs(since_id: int = 0) -> list[dict[str, Any]]:
    with _lock:
        if since_id <= 0:
            return list(_entries)
        return [e for e in _entries if e["id"] > since_id]


def clear_logs() -> None:
    with _lock:
        _entries.clear()