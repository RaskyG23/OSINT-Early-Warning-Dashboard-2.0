#!/bin/bash
set -u

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR" || exit 1

if ! docker info >/dev/null 2>&1; then
  echo "Docker Desktop is not running. Start Docker Desktop, wait for it to become ready, then run this launcher again."
  read -r -p "Press Return to close..."
  exit 1
fi

echo "Building and starting Horizon in the background..."
if ! docker compose up -d --build --force-recreate; then
  echo
  echo "Docker could not start the dashboard. Recent logs:"
  docker compose logs --tail=100
  read -r -p "Press Return to close..."
  exit 1
fi

echo "Waiting for http://localhost:8502 ..."
for attempt in $(seq 1 60); do
  if curl -fsS --max-time 2 http://127.0.0.1:8502/_stcore/health >/dev/null 2>&1; then
    echo "Horizon is ready."
    open http://localhost:8502/
    exit 0
  fi
  sleep 2
done

echo
echo "The container started but did not become healthy. Recent logs:"
docker compose ps
docker compose logs --tail=150
read -r -p "Press Return to close..."
exit 1
