from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

_lock = threading.Lock()
_last_scan: dict[str, Any] | None = None


def set_scan(picks: list[dict], count: int) -> None:
    global _last_scan
    with _lock:
        _last_scan = {
            "picks": picks,
            "count": count,
            "cached_at": datetime.now().isoformat(timespec="seconds"),
        }


def get_scan() -> dict[str, Any] | None:
    with _lock:
        return _last_scan.copy() if _last_scan else None