#!/usr/bin/env bash
# Pull the latest main and redeploy with the public override. Safe to run repeatedly.
set -euo pipefail
cd "$(dirname "$0")/.."

git pull --ff-only
export DEPLOYMENT_VERSION=$(git rev-parse --short HEAD)
compose="docker compose -f docker-compose.yml -f deploy/docker-compose.public.yml"

$compose pull --ignore-buildable
$compose up -d --build --remove-orphans

echo "Waiting for the backend health check..."
for _ in $(seq 1 60); do
  if $compose exec -T backend python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=2)" 2>/dev/null; then
    host=$(grep '^PUBLIC_HOST=' .env | cut -d= -f2)
    echo "Deployed ${DEPLOYMENT_VERSION}: https://${host}/docs  and  https://${host}/grafana/"
    exit 0
  fi
  sleep 2
done
$compose ps
echo "Backend did not become healthy; see: $compose logs backend" >&2
exit 1
