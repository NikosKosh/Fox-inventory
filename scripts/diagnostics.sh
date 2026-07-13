#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."

printf '\n\033[1;36m========== FOX INVENTORY: СОСТОЯНИЕ =========\033[0m\n'
docker compose ps

printf '\n\033[1;36m========== HEALTH =========\033[0m\n'
PORT=$(grep '^APP_PORT=' .env | cut -d= -f2)
curl -fsS "http://127.0.0.1:${PORT}/health/" && echo

printf '\n\033[1;36m========== ДИСК =========\033[0m\n'
df -h / /opt/fox-inventory 2>/dev/null || df -h /
du -sh data/media backups 2>/dev/null || true

printf '\n\033[1;36m========== ПОСЛЕДНИЕ ЛОГИ WEB =========\033[0m\n'
docker compose logs --tail=80 web
