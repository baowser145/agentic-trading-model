from __future__ import annotations

import json
from pathlib import Path

from agentic_trading.models import TickResult


class DecisionLogger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, result: TickResult) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(result.to_log_dict()) + "\n")
