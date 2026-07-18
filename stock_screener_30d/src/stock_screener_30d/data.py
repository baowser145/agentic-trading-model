from __future__ import annotations

import pandas as pd
import yfinance as yf


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def _safe_float(val) -> float | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        f = float(val)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


def fetch_ticker_metrics(
    ticker: str, lookback_days: int = 400, sma_days: int = 50
) -> dict | None:
    """Fetch latest metrics for screening. Returns None if insufficient data."""
    try:
        hist = yf.Ticker(ticker).history(period=f"{lookback_days}d", auto_adjust=True)
    except Exception:
        return None

    if hist is None or len(hist) < max(60, sma_days + 5):
        return None

    close = hist["Close"]
    volume = hist["Volume"]
    latest_close = _safe_float(close.iloc[-1])
    window_52w = close.tail(min(252, len(close)))
    high_52w = _safe_float(window_52w.max())
    sma = _safe_float(close.rolling(sma_days).mean().iloc[-1])
    rsi_val = _safe_float(rsi(close).iloc[-1])
    avg_volume = _safe_float(volume.tail(20).mean())

    if None in (latest_close, high_52w, sma, rsi_val, avg_volume) or high_52w <= 0:
        return None

    pullback_pct = ((high_52w - latest_close) / high_52w) * 100
    scan_date = close.index[-1]

    return {
        "ticker": ticker,
        "price": latest_close,
        "scan_date": scan_date,
        "rsi": rsi_val,
        "sma_50": sma,
        "above_sma": latest_close > sma,
        "high_52w": high_52w,
        "pullback_pct": pullback_pct,
        "avg_volume": avg_volume,
    }


def fetch_history(ticker: str, start: str, end: str) -> pd.Series | None:
    try:
        hist = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True)
    except Exception:
        return None
    if hist is None or hist.empty:
        return None
    return hist["Close"]