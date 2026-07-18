# Stock Screener 30d

CLI tool to screen for stocks matching 30-day swing-hold criteria and backtest the strategy against SPY.

## Install

```bash
cd ~/grok/projects/stock-screener-30d
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

**Daily scan** — top N picks matching criteria in `config/criteria.yaml`:

```bash
stock-screener scan
stock-screener scan --with-targets    # entry, exit date, 50-day SMA stop
stock-screener scan --top 5 -o picks.csv
```

With `--with-targets`:
- **Entry** — latest close (buy at market close on scan day)
- **ExitDate** — ~30 trading days later (matches backtest hold period)
- **Stop(50SMA)** — reference stop at 50-day moving average (not auto-executed)
- **Risk%** — distance from entry to stop

**Paper-trade log** — track daily picks and measure real performance:

```bash
stock-screener log                  # log today's scan picks
stock-screener log --update         # close matured trades, then log today
stock-screener log --status         # open positions + live P&L
stock-screener log --report         # win rate, avg return on closed trades
```

Log file: `data/paper-trades.csv`

**Backtest** — 30-day forward returns vs SPY (2019–2024, after 0.1% round-trip cost):

```bash
stock-screener backtest
```

## Criteria (default)

| Rule | Value |
|------|-------|
| RSI(14) | 40–60 |
| Price | Above 50-day SMA |
| Pullback from 52-week high | 5–15% |
| Min avg volume | 500k shares |

Edit `config/criteria.yaml` to tune.

## Web dashboard (local)

```bash
pip install -e ".[web]"
uvicorn stock_screener_30d.web:app --reload --port 8000
```

Open http://localhost:8000

The dashboard includes a **Paper vs Backtest** panel comparing your live logged picks against the historical simulation.

## Deploy to AWS Linux (Docker)

**On your Mac** — copy project to server:

```bash
rsync -avz --exclude .venv --exclude __pycache__ \
  ~/grok/projects/stock-screener-30d/ \
  ubuntu@YOUR_EC2_IP:/opt/stock-screener-30d/
```

**On the EC2 instance:**

```bash
ssh ubuntu@YOUR_EC2_IP
cd /opt/stock-screener-30d
chmod +x scripts/*.sh
./scripts/setup-aws.sh
```

**EC2 security group** — allow inbound TCP **8000** (or 80/443 if using nginx).

Dashboard: `http://YOUR_EC2_IP:8000`

### What runs automatically

- Web UI on port **8000**
- **Cron** inside container: `stock-screener log --update` weekdays at **6:30pm ET** (after market close)
- `data/` folder persisted on host (paper trades survive restarts)

### Useful Docker commands

```bash
docker compose logs -f
docker compose exec screener stock-screener scan --with-targets
docker compose exec screener stock-screener log --report
docker compose restart
```

### Optional: nginx + HTTPS

Put nginx in front on port 80, proxy to `localhost:8000`. Use Certbot for HTTPS if you expose it publicly.

**Security note:** This dashboard has no login. Use a security group that only allows your IP, or put it behind VPN / basic auth nginx.

## Tests

```bash
pip install -e ".[all]"
pytest
```