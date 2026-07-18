#!/bin/bash
set -euo pipefail

cd /app

if [ "${BACKTEST_ON_START:-1}" = "1" ] && [ ! -f /app/data/backtest-cache.json ]; then
  echo "Running initial backtest in background…"
  python3 -c "from stock_screener_30d.backtest_cache import run_and_cache_backtest; run_and_cache_backtest()" &
fi

if [ "${CRON_LOG:-0}" = "1" ]; then
  SCHEDULE="${CRON_SCHEDULE:-30 18 * * 1-5}"
  echo "${SCHEDULE} cd /app && /usr/local/bin/stock-screener log --update >> /app/data/cron.log 2>&1" | crontab -
  cron
  echo "Cron enabled: ${SCHEDULE} (stock-screener log --update)"
fi

case "${1:-web}" in
  web)
    exec uvicorn stock_screener_30d.web:app --host 0.0.0.0 --port 8000
    ;;
  scan)
    exec stock-screener scan --with-targets
    ;;
  log)
    exec stock-screener log --update
    ;;
  *)
    exec "$@"
    ;;
esac