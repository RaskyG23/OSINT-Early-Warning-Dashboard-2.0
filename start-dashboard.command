#!/bin/bash
set -u

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR" || exit 1

if ! docker info >/dev/null 2>&1; then
  echo "Docker Desktop is not running. Start Docker Desktop, wait for it to become ready, then run this launcher again."
  read -r -p "Press Return to close..."
  exit 1
fi

# A container created under an earlier Compose project name is not considered
# an orphan of this renamed project and can continue holding the dashboard
# port. Resolve the exact port occupant before starting; this avoids a hidden
# "address already in use" failure without depending on any legacy name.
PORT_OCCUPANTS="$(docker ps -q --filter 'publish=8502')"
if [ -n "$PORT_OCCUPANTS" ]; then
  echo "Stopping the existing container currently using dashboard port 8502..."
  docker stop $PORT_OCCUPANTS >/dev/null || true
fi

echo "Building and starting OSINT Early Warning Dashboard in the background..."
if ! docker compose up -d --build --force-recreate --remove-orphans; then
  echo
  echo "Docker could not start the dashboard. Recent logs:"
  docker compose logs --tail=100
  read -r -p "Press Return to close..."
  exit 1
fi

echo "Waiting for http://localhost:8502 ..."
for attempt in $(seq 1 60); do
  if curl -fsS --max-time 2 http://127.0.0.1:8502/_stcore/health >/dev/null 2>&1; then
    echo "OSINT Early Warning Dashboard is ready."
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
