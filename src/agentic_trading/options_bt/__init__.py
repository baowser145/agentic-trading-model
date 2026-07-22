"""Long-premium options backtest + scenario search agent.

Simulates single-leg debit calls/puts from underlying history using Black-Scholes
premiums and historical realized vol as an IV proxy. Paper research only —
never places live orders.
"""

from agentic_trading.options_bt.scenario import OptionScenario
from agentic_trading.options_bt.backtest import BacktestResult, run_backtest
from agentic_trading.options_bt.agent import SearchConfig, run_scenario_search
from agentic_trading.options_bt.metrics import TradeMetrics, summarize_trades

__all__ = [
    "OptionScenario",
    "BacktestResult",
    "run_backtest",
    "SearchConfig",
    "run_scenario_search",
    "TradeMetrics",
    "summarize_trades",
]
