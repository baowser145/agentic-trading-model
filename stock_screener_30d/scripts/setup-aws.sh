#!/bin/bash
set -euo pipefail

# One-time AWS EC2 setup for stock-screener-30d
# Run on Ubuntu 22.04+ as a user with docker access

if ! command -v docker >/dev/null 2>&1; then
  echo "Installing Docker..."
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
  echo "Log out and back in so docker works without sudo."
fi

APP_DIR="${APP_DIR:-/opt/stock-screener-30d}"

if [ ! -d "$APP_DIR" ]; then
  echo "Clone or copy the project to $APP_DIR first."
  echo "  git clone <your-repo> $APP_DIR"
  exit 1
fi

cd "$APP_DIR"
mkdir -p data

echo "Building and starting..."
docker compose up -d --build

echo ""
echo "Dashboard: http://$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || hostname -I | awk '{print $1}'):8000"
echo ""
echo "Open port 8000 in your EC2 security group (or put nginx in front)."
echo "Daily auto-log runs weekdays at 6:30pm ET (see CRON_SCHEDULE in docker-compose.yml)."
echo ""
echo "Useful commands:"
echo "  docker compose logs -f"
echo "  docker compose exec screener stock-screener log --report"
echo "  docker compose run --rm screener scan"