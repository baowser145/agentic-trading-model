from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from stock_screener_30d.activity_log import log as act_log
from stock_screener_30d.backtest import run_backtest
from stock_screener_30d.config import DEFAULT_CONFIG_PATH, load_config

CACHE_PATH = DEFAULT_CONFIG_PATH.parents[1] / "data" / "backtest-cache.json"


def load_cached_backtest(path: Path | None = None) -> dict[str, Any] | None:
    p = path or CACHE_PATH
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def run_and_cache_backtest(config_path: Path | None = None, cache_path: Path | None = None) -> dict[str, Any]:
    cfg = load_config(config_path)
    act_log("Starting historical backtest validation…", source="backtest")
    result = run_backtest(cfg)
    p = cache_path or CACHE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cached_at": datetime.now().isoformat(timespec="seconds"),
        "period": cfg.get("backtest", {}).get("start_year", 2019),
        **result,
    }
    if "error" not in result:
        p.write_text(json.dumps(payload, indent=2))
        act_log(f"Cached backtest results → {p.name}", source="backtest")
    else:
        act_log(f"Backtest error: {result['error']}", level="error", source="backtest")
    return payload