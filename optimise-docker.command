#!/bin/bash
set -u

CURRENT_DIR="$(cd "$(dirname "$0")" && pwd)"
OLD_COMPOSE="/Users/elcapi/Documents/Codex/2026-06-23/i-need-you-to-design-and/outputs/osint-streamlit-pipeline/docker-compose.yml"

if ! docker info >/dev/null 2>&1; then
  echo "Docker Desktop is not ready. Start it and run this launcher again."
  read -r -p "Press Return to close..."
  exit 1
fi

echo "Stopping only the older port-8501 dashboard and collector..."
docker compose -f "$OLD_COMPOSE" stop

echo "Removing unused build cache and dangling image layers..."
docker builder prune --all --force
docker image prune --force

echo "Recreating the current port-8502 dashboard with a 2 GB memory cap..."
cd "$CURRENT_DIR" || exit 1
docker compose up -d --build --force-recreate

echo
echo "Current dashboard resource usage:"
docker stats --no-stream horizon-streamlit-dashboard
echo
echo "Docker disk usage after cleanup:"
docker system df
echo
echo "The current dashboard is available at http://localhost:8502"
open http://localhost:8502/
read -r -p "Press Return to close..."
