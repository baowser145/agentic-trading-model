# AWS option stop poller

Always-on quote poller for long-premium stops. **Never places trades.**

On −10% (configurable): writes local alert + optional **SMS (Twilio)** / **Discord**.

## On server

```bash
# already deployed under:
cd /home/ubuntu/agentic-option-poller

# edit watch.json (entry, strike, exp)
# edit .env for Twilio and/or Discord

sudo systemctl enable --now agentic-option-stop.timer
systemctl list-timers | grep agentic
journalctl -u agentic-option-stop.service -n 20
```

## Manual run

```bash
cd /home/ubuntu/agentic-option-poller
source .venv/bin/activate
python option_stop_poller.py
```

## SMS setup (Twilio)

1. Create Twilio account + get a number  
2. Put in `.env`:

```
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_FROM_NUMBER=+1...
ALERT_PHONE=+1yourphone
```

3. `sudo systemctl start agentic-option-stop.service` once to test  

## Notes

- Quotes from **yfinance** (public), not Robinhood — can lag a bit.  
- Broker **GTC stop-market** remains primary execution.  
- This poller is for **text/Discord heads-up** so you can confirm sell in app or Grok.
