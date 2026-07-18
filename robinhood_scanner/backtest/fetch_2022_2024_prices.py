#!/usr/bin/env python3
"""Bulk-download daily OHLCV for all S&P 500 tickers over the 2022-06-01 to 2024-08-15 window,
for the out-of-sample PEAD backtest. Distinct cache suffix (_2022_2024) so it never overwrites the
{ticker}_3y.csv files the live pipeline reads.
"""
from pathlib import Path

import yfinance as yf

CACHE_DIR = Path(__file__).parent / "cache"
START = "2022-06-01"
END = "2024-08-16"  # yfinance end is exclusive
CHUNK_SIZE = 60

with open(CACHE_DIR / "sp500_tickers.txt") as f:
    tickers = [line.strip() for line in f if line.strip()]

print(f"Downloading {len(tickers)} tickers, {START} to {END}")
failed = []
for i in range(0, len(tickers), CHUNK_SIZE):
    chunk = tickers[i:i + CHUNK_SIZE]
    print(f"  {i + 1}-{i + len(chunk)} of {len(tickers)}...")
    try:
        data = yf.download(
            tickers=chunk,
            start=START,
            end=END,
            group_by="ticker",
            threads=True,
            auto_adjust=True,
            progress=False,
        )
    except Exception as e:
        print(f"  Chunk failed: {e}")
        failed.extend(chunk)
        continue

    for ticker in chunk:
        try:
            df = data if len(chunk) == 1 else data[ticker]
            df = df.dropna(how="all")
            if df.empty:
                failed.append(ticker)
                continue
            df.to_csv(CACHE_DIR / f"{ticker}_2022_2024.csv")
        except Exception:
            failed.append(ticker)

print(f"\nDone. {len(tickers) - len(failed)} succeeded, {len(failed)} failed.")
if failed:
    print("Failed tickers:", failed)
