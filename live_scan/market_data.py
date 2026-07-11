"""Free-data-source layer for the scheduled daily scans (yfinance + Alpha Vantage).

Replaces the Robinhood MCP tools the original session-hosted cron jobs used
(get_earnings_calendar, get_equity_quotes, get_option_chains, ...) so both daily jobs can run as
plain Python under GitHub Actions. Everything here is read-only public market data — there is no
brokerage access of any kind in this module or its callers.

Data sources:
- Prices / historicals / option chains: yfinance (same source the backtest already uses).
- Upcoming earnings calendar: Alpha Vantage EARNINGS_CALENDAR (free key, one CSV call covers a
  3-month horizon). Env var: ALPHAVANTAGE_API_KEY.
- Historical earnings dates: yfinance per-ticker (only fetched for the handful of tickers that
  survive earlier filters, never the whole S&P 500).
- Option delta: computed via Black-Scholes from the chain's implied volatility, because yfinance
  chains carry bid/ask/IV but not greeks.
"""

import io
import math
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf
from scipy.stats import norm

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backtest"))

from data import fetch_daily_bulk, get_sp500_tickers  # noqa: E402  (backtest/data.py)

ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
# Flat risk-free assumption for delta only. Delta's sensitivity to r over <2-month maturities is
# far below the 0.15 screening threshold's tolerance, so a live rate fetch isn't worth the
# extra failure mode.
RISK_FREE_RATE = 0.04
YF_RETRY_ATTEMPTS = 3
YF_RETRY_SLEEP_SECONDS = 2.0


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines from a local .env into os.environ (existing vars win). GitHub Actions
    passes real env vars; this exists so local runs keep working off config/.env."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def today_market_date() -> pd.Timestamp:
    """Today's date in market (US/Eastern) terms, tz-naive normalized — the runner is UTC, where
    the calendar date is already tomorrow during the evening scan window."""
    return pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)


def download_recent_bars(tickers: list[str], period: str = "4mo", chunk_size: int = 60) -> dict[str, pd.DataFrame]:
    """Batch daily OHLCV for many tickers, chunked against Yahoo rate limiting.
    Returns {ticker: DataFrame} with tz-naive normalized DatetimeIndex; tickers with no data are
    simply absent from the result."""
    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i : i + chunk_size]
        data = None
        for attempt in range(YF_RETRY_ATTEMPTS):
            try:
                data = yf.download(
                    tickers=chunk,
                    period=period,
                    group_by="ticker",
                    threads=True,
                    auto_adjust=True,
                    progress=False,
                )
                break
            except Exception as e:
                print(f"  bars chunk {i // chunk_size + 1} attempt {attempt + 1} failed: {e}", file=sys.stderr)
                time.sleep(YF_RETRY_SLEEP_SECONDS * (attempt + 1))
        if data is None or data.empty:
            continue
        for ticker in chunk:
            try:
                df = data if len(chunk) == 1 else data[ticker]
            except KeyError:
                continue
            df = df.dropna(how="all")
            if df.empty:
                continue
            idx = pd.DatetimeIndex(df.index)
            if idx.tz is not None:
                idx = idx.tz_localize(None)
            df = df.set_axis(idx.normalize())
            out[ticker] = df
    return out


def get_earnings_dates(ticker: str, limit: int = 12) -> list[pd.Timestamp]:
    """Past + upcoming earnings report timestamps for one ticker (tz-aware, US/Eastern), newest
    first. Empty list when Yahoo has nothing / keeps erroring — callers must treat that as
    'unknown', not 'no earnings'."""
    for attempt in range(YF_RETRY_ATTEMPTS):
        try:
            df = yf.Ticker(ticker).get_earnings_dates(limit=limit)
            if df is None or df.empty:
                return []
            return list(df.index)
        except Exception as e:
            if attempt == YF_RETRY_ATTEMPTS - 1:
                print(f"  earnings dates failed for {ticker}: {e}", file=sys.stderr)
                return []
            time.sleep(YF_RETRY_SLEEP_SECONDS * (attempt + 1))
    return []


def to_naive_date(ts) -> "pd.Timestamp":
    ts = pd.Timestamp(ts)
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    return ts.normalize()


def earnings_date_set(timestamps: list[pd.Timestamp]) -> set:
    """Calendar-date set for strategies.is_earnings_gap()."""
    return {to_naive_date(ts).date() for ts in timestamps}


def earnings_timing(ts: pd.Timestamp) -> str:
    """Best-effort am/pm classification from a report timestamp's time-of-day. Midnight is a
    'date only' placeholder in Yahoo data, not a real 12am report."""
    ts = pd.Timestamp(ts)
    if ts.hour == 0 and ts.minute == 0:
        return "unknown"
    minutes = ts.hour * 60 + ts.minute
    if minutes < 9 * 60 + 30:
        return "am"
    if minutes >= 15 * 60 + 30:
        return "pm"
    return "unknown"


def fetch_alpha_vantage_earnings_calendar(api_key: str, horizon: str = "3month") -> pd.DataFrame:
    """Upcoming earnings for all US symbols as a DataFrame (columns include symbol, name,
    reportDate). One call covers the whole horizon — the only Alpha Vantage request per day."""
    resp = requests.get(
        ALPHA_VANTAGE_URL,
        params={"function": "EARNINGS_CALENDAR", "horizon": horizon, "apikey": api_key},
        timeout=60,
    )
    resp.raise_for_status()
    body = resp.text.strip()
    # Errors (bad key, rate limit) come back as HTTP 200 with a JSON body instead of CSV.
    if body.startswith("{"):
        raise RuntimeError(f"Alpha Vantage returned an error instead of CSV: {body[:300]}")
    df = pd.read_csv(io.StringIO(body))
    if "symbol" not in df.columns or "reportDate" not in df.columns:
        raise RuntimeError(f"Unexpected Alpha Vantage CSV columns: {list(df.columns)}")
    df["symbol"] = df["symbol"].astype(str).str.replace(".", "-", regex=False)  # match yfinance style
    df["reportDate"] = pd.to_datetime(df["reportDate"])
    return df


def last_price(ticker: str) -> float | None:
    try:
        price = yf.Ticker(ticker).fast_info.last_price
        return float(price) if price and price > 0 else None
    except Exception:
        return None


def company_name(ticker: str) -> str:
    try:
        info = yf.Ticker(ticker).get_info()
        return info.get("shortName") or info.get("longName") or ticker
    except Exception:
        return ticker


def bs_call_delta(spot: float, strike: float, t_years: float, iv: float, r: float = RISK_FREE_RATE) -> float:
    """Black-Scholes call delta N(d1). Degenerate inputs (expired / zero IV) collapse to the
    intrinsic 0-or-1 answer rather than raising."""
    if spot <= 0 or strike <= 0:
        return 0.0
    if t_years <= 0 or iv <= 0:
        return 1.0 if spot > strike else 0.0
    d1 = (math.log(spot / strike) + (r + iv * iv / 2.0) * t_years) / (iv * math.sqrt(t_years))
    return float(norm.cdf(d1))


# --- Option contract selection (docs/pre-earnings-screen-prompt.md step 4) -----------------------

OPTION_MAX_COST_DOLLARS = 300.0
OPTION_MIN_DELTA = 0.15
OPTION_OTM_START_MULT = 1.075


def select_call_from_chain(calls: pd.DataFrame, spot: float, expiration: pd.Timestamp, today: pd.Timestamp) -> tuple[dict | None, str]:
    """Pure selection over an already-fetched calls chain (columns: strike, bid, ask,
    impliedVolatility). Start at the strike nearest spot*1.075 and walk deeper OTM; a pick needs
    ALL of: ask*100 < $300, bid > 0, computed delta >= 0.15.

    Returns (pick, reason). pick=None means stock-only; reason explains why, per the doc's two
    canonical phrasings."""
    if calls is None or calls.empty:
        return None, "no listed calls for that expiration"
    calls = calls.dropna(subset=["strike"]).sort_values("strike").reset_index(drop=True)
    t_years = max((pd.Timestamp(expiration) - today).days, 1) / 365.0

    start_strike = spot * OPTION_OTM_START_MULT
    start_idx = int((calls["strike"] - start_strike).abs().idxmin())

    any_within_budget = False
    for _, row in calls.iloc[start_idx:].iterrows():
        ask = float(row.get("ask") or 0)
        bid = float(row.get("bid") or 0)
        iv = float(row.get("impliedVolatility") or 0)
        if ask <= 0 or ask * 100 >= OPTION_MAX_COST_DOLLARS:
            continue
        any_within_budget = True
        if bid <= 0:
            continue
        delta = bs_call_delta(spot, float(row["strike"]), t_years, iv)
        if delta < OPTION_MIN_DELTA:
            continue
        return {
            "strike": float(row["strike"]),
            "expiration": pd.Timestamp(expiration),
            "bid": bid,
            "ask": ask,
            "cost": ask * 100,
            "delta": delta,
            "iv": iv,
        }, ""
    if not any_within_budget:
        return None, "too expensive even at deepest strike"
    return None, "only illiquid/near-zero-delta strikes fit the budget"


def pick_option_contract(ticker: str, spot: float, report_date: pd.Timestamp, today: pd.Timestamp) -> tuple[dict | None, str]:
    """Fetch the chain for the nearest expiration ON/AFTER report_date and select per the rules
    above. Network wrapper around select_call_from_chain()."""
    try:
        tk = yf.Ticker(ticker)
        expirations = [pd.Timestamp(e) for e in tk.options]
        valid = sorted(e for e in expirations if e >= pd.Timestamp(report_date).normalize())
        if not valid:
            return None, "no expiration on/after the report date"
        expiration = valid[0]
        chain = tk.option_chain(expiration.strftime("%Y-%m-%d"))
        return select_call_from_chain(chain.calls, spot, expiration, today)
    except Exception as e:
        return None, f"option chain unavailable ({type(e).__name__})"
