from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from stock_screener_30d.config import load_config
from stock_screener_30d.screener import run_scan
from stock_screener_30d.backtest import run_backtest
from stock_screener_30d.targets import enrich_with_targets, format_targets_table, targets_summary
from stock_screener_30d.paper_log import (
    append_scan,
    open_positions_status,
    performance_report,
    update_closed_trades,
)

app = typer.Typer(help="Screen stocks for 30-day holds and backtest the strategy.")


@app.command()
def scan(
    config: Path = typer.Option(None, "--config", "-c", help="Path to criteria.yaml"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Save results to CSV"),
    top_n: Optional[int] = typer.Option(None, "--top", "-n", help="Override top N"),
    with_targets: bool = typer.Option(
        False, "--with-targets", "-t", help="Show entry, exit date, and 50-day SMA stop"
    ),
):
    """Run daily scan: output top N stocks matching 30-day hold criteria."""
    cfg = load_config(config)
    if top_n:
        cfg["output"]["top_n"] = top_n

    typer.echo("Scanning universe for 30-day hold candidates...")
    df = run_scan(cfg)

    if df.empty:
        typer.echo("No stocks matched criteria today.")
        raise typer.Exit(0)

    hold_days = cfg.get("backtest", {}).get("hold_days", 30)

    if with_targets:
        df = enrich_with_targets(df, hold_days=hold_days)
        typer.echo(f"\nTop {len(df)} picks (with {hold_days}-day targets):\n")
        typer.echo(format_targets_table(df))
        typer.echo(f"\n{targets_summary(cfg)}")
    else:
        typer.echo(f"\nTop {len(df)} picks:\n")
        display = df[["ticker", "price", "rsi", "pullback_pct", "avg_volume", "score"]].copy()
        display.columns = ["Ticker", "Price", "RSI", "Pullback%", "AvgVol", "Score"]
        display["Price"] = display["Price"].map(lambda x: f"${x:.2f}")
        display["RSI"] = display["RSI"].map(lambda x: f"{x:.1f}")
        display["Pullback%"] = display["Pullback%"].map(lambda x: f"{x:.1f}%")
        display["AvgVol"] = display["AvgVol"].map(lambda x: f"{x:,.0f}")
        display["Score"] = display["Score"].map(lambda x: f"{x:.3f}")
        typer.echo(display.to_string(index=False))
        typer.echo("\nTip: add --with-targets to see entry, exit date, and stop levels.")

    if output:
        df.to_csv(output, index=False)
        typer.echo(f"\nSaved to {output}")


@app.command()
def log(
    config: Path = typer.Option(None, "--config", "-c", help="Path to criteria.yaml"),
    log_file: Path = typer.Option(None, "--log", "-l", help="Path to paper-trades.csv"),
    update: bool = typer.Option(False, "--update", "-u", help="Close matured trades before logging"),
    report: bool = typer.Option(False, "--report", "-r", help="Show performance report only"),
    status: bool = typer.Option(False, "--status", "-s", help="Show open positions with live P&L"),
):
    """Log today's scan picks for paper trading; track outcomes over time."""
    cfg = load_config(config)

    if report:
        r = performance_report(log_file)
        typer.echo("Paper Trade Report")
        typer.echo(f"  Log file: {r.get('path', 'data/paper-trades.csv')}")
        typer.echo(f"  Total trades: {r['total_trades']}")
        typer.echo(f"  Open: {r['open']}  |  Closed: {r['closed']}")
        if r["closed"] == 0:
            typer.echo(f"\n  {r.get('message', 'No closed trades yet.')}")
            raise typer.Exit(0)
        typer.echo(f"  Win rate:       {r['win_rate_pct']}%")
        typer.echo(f"  Avg return:     {r['avg_return_net_pct']}% (net)")
        typer.echo(f"  Total return:   {r['total_return_net_pct']}% (sum of closed)")
        typer.echo(f"  Best:  {r['best_trade']} ({r['best_return_pct']}%)")
        typer.echo(f"  Worst: {r['worst_trade']} ({r['worst_return_pct']}%)")
        raise typer.Exit(0)

    if status:
        df = open_positions_status(log_file)
        if df.empty:
            typer.echo("No open paper trades.")
            raise typer.Exit(0)
        typer.echo("Open positions:\n")
        for _, row in df.iterrows():
            cur = f"${row['current_price']:.2f}" if row["current_price"] else "N/A"
            pnl = f"{row['unrealized_pct']:+.2f}%" if row["unrealized_pct"] is not None else "N/A"
            flag = " ⚠ below stop" if row["below_stop"] else ""
            typer.echo(
                f"  {row['ticker']:6} entry ${row['entry_price']:.2f} → now {cur} "
                f"({pnl})  exit {row['exit_date']}  ({row['days_left']}d left){flag}"
            )
        raise typer.Exit(0)

    if update:
        closed = update_closed_trades(cfg, log_file)
        if closed:
            typer.echo(f"Closed {closed} matured trade(s).")

    typer.echo("Scanning and logging picks...")
    result = append_scan(cfg, log_file)

    if result["appended"] == 0 and result["skipped"] == 0:
        typer.echo("No stocks matched criteria — nothing logged.")
        raise typer.Exit(0)

    typer.echo(f"Logged {result['appended']} new trade(s) to {result['path']}")
    if result["skipped"]:
        typer.echo(f"Skipped {result['skipped']} duplicate(s) (same ticker + entry date).")

    if result["new_tickers"]:
        typer.echo(f"NEW today:   {', '.join(result['new_tickers'])}")
    if result["dropped_tickers"]:
        typer.echo(f"DROPPED:     {', '.join(result['dropped_tickers'])} (vs last scan)")

    typer.echo("\nNext: run `stock-screener log --status` to check open P&L.")
    typer.echo("      run `stock-screener log --update` daily to close matured trades.")


@app.command()
def backtest(
    config: Path = typer.Option(None, "--config", "-c", help="Path to criteria.yaml"),
):
    """Backtest 30-day hold strategy vs benchmark after transaction costs."""
    cfg = load_config(config)
    typer.echo("Running backtest (this may take a few minutes)...")
    result = run_backtest(cfg)

    if "error" in result:
        typer.echo(f"Error: {result['error']}", err=True)
        raise typer.Exit(1)

    typer.echo(f"\nBacktest Results ({result['period']})")
    typer.echo(f"  Hold period:     {result['hold_days']} days")
    typer.echo(f"  Round-trip cost: {result['round_trip_cost_pct']}%")
    typer.echo(f"  Rebalances:      {result['num_rebalances']}")
    typer.echo(f"  Strategy (avg):  {result['strategy_avg_per_period_pct']}% per period")
    typer.echo(f"  Benchmark (avg): {result['benchmark_avg_per_period_pct']}% per period")
    typer.echo(f"  Strategy (ann.): {result['strategy_annualized_pct']}%")
    typer.echo(f"  Benchmark (ann.):{result['benchmark_annualized_pct']}%")
    typer.echo(f"  Excess (ann.):   {result['excess_annualized_pct']}%")
    if result["beats_benchmark"]:
        typer.echo("\n  Strategy BEATS benchmark after costs.")
    else:
        typer.echo("\n  Strategy does NOT beat benchmark after costs.")
        typer.echo("  Consider adjusting criteria in config/criteria.yaml before building further.")


if __name__ == "__main__":
    app()