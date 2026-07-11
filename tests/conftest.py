import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "backtest"))
sys.path.insert(0, str(ROOT / "alerts"))
sys.path.insert(0, str(ROOT / "live_scan"))
