#!/usr/bin/env python3
"""AWS option stop poller — quote check + alert only (never places trades).

Reads a watch JSON (entry, strike, expiry, stop_loss_pct). Fetches a mid/mark
from yfinance (public data; not Robinhood). On breach: SMS (Twilio), Discord
webhook/channel, and local files. Idempotent: one alert per trigger cycle.

Exit codes:
  0  ok (no trigger or alert sent)
  1  config/runtime error
  2  trigger fired (still success for cron; useful for metrics)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def fetch_option_mark(symbol: str, expiration: str, strike: float, option_type: str) -> dict[str, float | None]:
    """Return bid/ask/last/mark from yfinance option chain."""
    import yfinance as yf  # lazy so --help works without deps

    t = yf.Ticker(symbol.upper())
    if expiration not in (t.options or ()):
        raise RuntimeError(
            f"expiry {expiration} not in yfinance chain for {symbol}; "
            f"have={list(t.options or [])[:12]}"
        )
    chain = t.option_chain(expiration)
    table = chain.calls if option_type.lower() == "call" else chain.puts
    rows = table[abs(table["strike"].astype(float) - float(strike)) < 0.001]
    if rows.empty:
        raise RuntimeError(f"no {option_type} strike {strike} for {symbol} {expiration}")
    row = rows.iloc[0]
    bid = float(row["bid"]) if row.get("bid") == row.get("bid") else None
    ask = float(row["ask"]) if row.get("ask") == row.get("ask") else None
    last = float(row["lastPrice"]) if row.get("lastPrice") == row.get("lastPrice") else None
    mark: float | None
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        mark = (bid + ask) / 2.0
    else:
        mark = last
    return {"bid": bid, "ask": ask, "last": last, "mark": mark}


def pct_change(mark: float, entry: float) -> float:
    return (mark - entry) / entry


def send_discord_webhook(url: str, content: str) -> None:
    body = json.dumps({"content": content[:1900]}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "agentic-option-poller/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()


def send_twilio_sms(to: str, body: str) -> None:
    import base64
    import urllib.parse

    sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
    token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
    frm = os.environ.get("TWILIO_FROM_NUMBER", "").strip()
    if not (sid and token and frm and to):
        raise RuntimeError("Twilio env incomplete (TWILIO_ACCOUNT_SID/AUTH_TOKEN/FROM_NUMBER + to)")

    data = urllib.parse.urlencode(
        {"To": to, "From": frm, "Body": body[:1500]}
    ).encode()
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "agentic-option-poller/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()


def send_discord_bot_channel(token: str, channel_id: str, content: str) -> None:
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    body = json.dumps({"content": content[:1900]}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "agentic-option-poller/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()


def build_alert_text(watch: dict[str, Any], mark: float, pct: float) -> str:
    sym = watch.get("symbol", "?")
    strike = watch.get("strike", "?")
    exp = watch.get("expiration", "?")
    otype = watch.get("option_type", "call")
    entry = float(watch["entry_price"])
    qty = watch.get("quantity", 1)
    return (
        f"AGENTIC STOP ALERT (no auto-trade)\n"
        f"{sym} ${strike} {otype} exp {exp} x{qty}\n"
        f"mark ${mark:.2f} vs entry ${entry:.2f} ({pct*100:.1f}%)\n"
        f"threshold −{float(watch.get('stop_loss_pct', 0.1))*100:.0f}% "
        f"(≤ ${float(watch.get('stop_trigger_mark', entry * 0.9)):.2f})\n"
        f"Action: sell-to-close in Robinhood app, OR open Grok and say: "
        f"\"sell BAC stop\" / \"place the stop sell\".\n"
        f"Broker GTC stop may also fire near $0.80."
    )


def deliver_alerts(watch: dict[str, Any], text: str, state_dir: Path) -> list[str]:
    """Send configured alerts. Returns list of channels that succeeded."""
    sent: list[str] = []
    errors: list[str] = []

    # Always archive
    alert_path = state_dir / "last_alert.txt"
    alert_path.write_text(text + "\n")
    sent.append(f"file:{alert_path}")

    # Discord webhook
    wh = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if wh:
        try:
            send_discord_webhook(wh, text)
            sent.append("discord_webhook")
        except Exception as e:
            errors.append(f"discord_webhook: {e}")

    # Discord bot → channel (reuse Sharp bot token optionally)
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    chan = os.environ.get("DISCORD_ALERT_CHANNEL_ID", "").strip() or os.environ.get(
        "ALLOWED_CHANNEL_ID", ""
    ).strip()
    if token and chan:
        try:
            send_discord_bot_channel(token, chan, text)
            sent.append("discord_bot")
        except Exception as e:
            errors.append(f"discord_bot: {e}")

    # Twilio SMS
    phone = os.environ.get("ALERT_PHONE", "").strip()
    if phone and os.environ.get("TWILIO_ACCOUNT_SID", "").strip():
        try:
            send_twilio_sms(phone, text)
            sent.append("twilio_sms")
        except Exception as e:
            errors.append(f"twilio: {e}")
    elif phone and not os.environ.get("TWILIO_ACCOUNT_SID", "").strip():
        errors.append("ALERT_PHONE set but TWILIO_* missing — SMS skipped")

    if errors:
        (state_dir / "last_alert_errors.json").write_text(
            json.dumps({"ts": utc_now(), "errors": errors}, indent=2) + "\n"
        )
    return sent


def run_once(watch_path: Path, state_dir: Path) -> int:
    watch = load_json(watch_path)
    if not watch.get("enabled", True):
        print(json.dumps({"ok": True, "skipped": "disabled"}))
        return 0

    status = str(watch.get("status") or "")
    if status.startswith("triggered") or status.startswith("closed"):
        print(json.dumps({"ok": True, "skipped": status}))
        return 0

    # Already alerted this breach cycle?
    state_file = state_dir / "poller_state.json"
    state: dict[str, Any] = load_json(state_file) if state_file.exists() else {}

    symbol = str(watch["symbol"])
    expiration = str(watch["expiration"])
    strike = float(watch["strike"])
    otype = str(watch.get("option_type") or "call")
    entry = float(watch["entry_price"])
    stop_pct = float(watch.get("stop_loss_pct") or 0.10)
    trigger_mark = float(watch.get("stop_trigger_mark") or (entry * (1.0 - stop_pct)))

    q = fetch_option_mark(symbol, expiration, strike, otype)
    mark = q["mark"]
    if mark is None or mark <= 0:
        raise RuntimeError(f"no usable mark: {q}")

    pct = pct_change(float(mark), entry)
    triggered = pct <= -stop_pct or float(mark) <= trigger_mark + 1e-9

    watch["last_check"] = utc_now()
    watch["last_mark"] = round(float(mark), 4)
    watch["last_bid"] = q.get("bid")
    watch["last_ask"] = q.get("ask")
    watch["last_pct_change"] = round(pct, 6)
    watch["quote_source"] = "yfinance"
    save_json(watch_path, watch)

    state.update(
        {
            "last_check": watch["last_check"],
            "last_mark": watch["last_mark"],
            "last_pct_change": watch["last_pct_change"],
            "triggered": triggered,
        }
    )

    result = {
        "ok": True,
        "symbol": symbol,
        "mark": mark,
        "entry": entry,
        "pct": round(pct, 4),
        "triggered": triggered,
        "action": "none",
    }

    if not triggered:
        # Reset alert latch when price recovers above stop (re-arm)
        if state.get("alert_sent") and pct > -stop_pct * 0.5:
            state["alert_sent"] = False
            state["rearmed_at"] = utc_now()
        save_json(state_file, state)
        print(json.dumps(result))
        return 0

    # Triggered
    if state.get("alert_sent"):
        result["action"] = "already_alerted"
        save_json(state_file, state)
        print(json.dumps(result))
        return 2

    text = build_alert_text(watch, float(mark), pct)
    sent = deliver_alerts(watch, text, state_dir)
    state["alert_sent"] = True
    state["alert_sent_at"] = utc_now()
    state["alert_channels"] = sent
    state["alert_text"] = text
    save_json(state_file, state)

    watch["status"] = "alert_sent_awaiting_user"
    watch["triggered_at"] = utc_now()
    watch["alert_channels"] = sent
    save_json(watch_path, watch)

    (state_dir / "ALERT_PENDING").write_text(text + "\n")
    result["action"] = "alert_sent"
    result["channels"] = sent
    print(json.dumps(result))
    return 2


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Option stop poller (alert only)")
    p.add_argument(
        "--watch",
        type=Path,
        default=Path(os.environ.get("WATCH_FILE", "watch.json")),
        help="Path to option_stop_watch.json",
    )
    p.add_argument(
        "--state-dir",
        type=Path,
        default=Path(os.environ.get("STATE_DIR", "state")),
        help="Writable state/log directory",
    )
    args = p.parse_args(argv)

    try:
        return run_once(args.watch, args.state_dir)
    except Exception as e:
        err = {"ok": False, "error": str(e), "ts": utc_now()}
        print(json.dumps(err), file=sys.stderr)
        try:
            args.state_dir.mkdir(parents=True, exist_ok=True)
            (args.state_dir / "last_error.json").write_text(json.dumps(err, indent=2) + "\n")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
