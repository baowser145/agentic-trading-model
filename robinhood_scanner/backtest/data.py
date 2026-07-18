"""Historical daily data fetch with simple disk caching (yfinance, free)."""

import io
import time
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_MAX_AGE_SECONDS = 24 * 3600
SP500_CACHE_FILE = CACHE_DIR / "sp500_tickers.txt"
SP500_CACHE_MAX_AGE_SECONDS = 7 * 24 * 3600
WIKIPEDIA_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def fetch_daily(ticker: str, period: str = "3y") -> pd.DataFrame:
    """Fetch daily OHLCV for `ticker`. Cached on disk for CACHE_MAX_AGE_SECONDS."""
    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / f"{ticker}_{period}.csv"

    if cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < CACHE_MAX_AGE_SECONDS:
        df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
        return df

    df = yf.Ticker(ticker).history(period=period, auto_adjust=True)
    if df.empty:
        raise ValueError(f"No data returned for {ticker}")
    df.to_csv(cache_file)
    return df


def get_sp500_tickers() -> list[str]:
    """Fetch current S&P 500 constituents from Wikipedia, cached for a week.

    Tickers are normalized for yfinance (e.g. BRK.B -> BRK-B).
    """
    CACHE_DIR.mkdir(exist_ok=True)
    if (
        SP500_CACHE_FILE.exists()
        and (time.time() - SP500_CACHE_FILE.stat().st_mtime) < SP500_CACHE_MAX_AGE_SECONDS
    ):
        return [line.strip() for line in SP500_CACHE_FILE.read_text().splitlines() if line.strip()]

    resp = requests.get(WIKIPEDIA_SP500_URL, headers={"User-Agent": "Mozilla/5.0 (research script)"}, timeout=15)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    tickers = tables[0]["Symbol"].str.replace(".", "-", regex=False).tolist()
    SP500_CACHE_FILE.write_text("\n".join(tickers) + "\n")
    return tickers


def fetch_daily_bulk(tickers: list[str], period: str = "3y", chunk_size: int = 60) -> list[str]:
    """Batch-download daily OHLCV for many tickers via yf.download and warm the per-ticker cache
    that fetch_daily() reads from. Returns the list of tickers that failed/had no data.

    Chunked (rather than one giant request) to stay reliable against Yahoo rate limiting.
    """
    CACHE_DIR.mkdir(exist_ok=True)
    failed = []

    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i : i + chunk_size]
        print(f"  Bulk downloading {i + 1}-{i + len(chunk)} of {len(tickers)}...")
        try:
            data = yf.download(
                tickers=chunk,
                period=period,
                group_by="ticker",
                threads=True,
                auto_adjust=True,
                progress=False,
            )
        except Exception as e:
            print(f"  Chunk download failed: {e}")
            failed.extend(chunk)
            continue

        for ticker in chunk:
            try:
                if len(chunk) == 1:
                    df = data
                else:
                    df = data[ticker]
                df = df.dropna(how="all")
                if df.empty:
                    failed.append(ticker)
                    continue
                cache_file = CACHE_DIR / f"{ticker}_{period}.csv"
                df.to_csv(cache_file)
            except Exception:
                failed.append(ticker)

    return failed
