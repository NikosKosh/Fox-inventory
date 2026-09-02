#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
echo "========== VERSION =========="
cat VERSION
test "$(tr -d '\r\n' < VERSION)" = "1.7.0"
echo "========== DJANGO CHECK =========="
docker compose exec -T web python manage.py check
echo "========== WORKSPACE CHECK =========="
docker compose exec -T web python manage.py check_workspace_170
echo "========== MIGRATION =========="
docker compose exec -T web python manage.py showmigrations inventory | tail -20
echo "========== HEALTH =========="
curl -fsS http://127.0.0.1:8088/health/ && echo
